"""Detached, descriptor-based ID-mapped data mounts.

Internal mount primitive, NOT a path authorization API or a daemon RPC.
Caller must authorize both project and runtime identity, pin the approved
source, prepare an empty root-owned publication directory under a protected
parent, exclude nested mounts/hardlinks, and serialize mount operations.
The registered pilot also uses it inside a private service mount namespace.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
import os
from pathlib import Path
import select
import stat
import subprocess

AT_EMPTY_PATH = 0x1000
OPEN_TREE_CLONE = 1
MOVE_MOUNT_F_EMPTY_PATH = 0x04
MOVE_MOUNT_T_EMPTY_PATH = 0x40
MOUNT_ATTR_IDMAP = 0x00100000
MOUNT_ATTR_RDONLY = 0x01
MOUNT_ATTR_NOSUID = 0x02
MOUNT_ATTR_NODEV = 0x04
MOUNT_ATTR_NOEXEC = 0x08
MOUNT_FLAGS = os.ST_NOSUID | os.ST_NODEV | os.ST_NOEXEC
ENV = {'PATH': '/usr/bin:/bin', 'LANG': 'C'}


class IdmapError(RuntimeError):
    pass


@dataclass(frozen=True)
class IdMapping:
    developer_uid: int
    developer_gid: int
    runtime_uid: int
    runtime_gid: int

    def __post_init__(self):
        if any(type(n) is not int or not 0 < n < 2**32 - 1
               for n in (self.developer_uid, self.developer_gid,
                         self.runtime_uid, self.runtime_gid)):
            raise IdmapError('Expected exactly one non-root UID/GID pair')
        if (self.developer_uid == self.runtime_uid
                or self.developer_gid == self.runtime_gid):
            raise IdmapError('Developer and runtime identities must differ')

    @property
    def developer(self):
        return self.developer_uid, self.developer_gid

    @property
    def runtime(self):
        return self.runtime_uid, self.runtime_gid


class MountAttr(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in
                ('attr_set', 'attr_clr', 'propagation', 'userns_fd')]


def checked_call(function, *args):
    result = function(*args)
    if result < 0:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number))
    return result


def mount_api():
    # Use the glibc wrappers, not architecture-specific syscall numbers.
    # An unavailable API fails closed; no path-based or ACL fallback.
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        tree, setattr, move = libc.open_tree, libc.mount_setattr, libc.move_mount
    except AttributeError as exc:
        raise IdmapError('New mount API unavailable') from exc
    tree.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    setattr.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint,
                        ctypes.POINTER(MountAttr), ctypes.c_size_t]
    move.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                     ctypes.c_char_p, ctypes.c_uint]
    for function in (tree, setattr, move):
        function.restype = ctypes.c_int
    return tree, setattr, move


@contextmanager
def mapping_namespace(mapping):
    # A fixed helper creates a new user namespace and waits for EOF. The
    # parent writes the exact maps while the unreaped child pins its PID.
    # The helper never executes project code, and root is not mapped.
    program = """import os, sys
os.unshare(os.CLONE_NEWUSER)
os.write(1, b'R')
sys.stdin.buffer.read(1)
"""
    child = subprocess.Popen(['/usr/bin/python3', '-I', '-S', '-c', program],
                             env=ENV, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             close_fds=True)
    fd = None
    try:
        if not select.select([child.stdout], [], [], 5)[0] or child.stdout.read(1) != b'R':
            raise IdmapError('Cannot create mapping user namespace')
        proc = Path('/proc') / str(child.pid)
        if os.path.samestat((proc / 'ns/user').stat(), Path('/proc/self/ns/user').stat()):
            raise IdmapError('Mapping namespace was not separated')
        uid_map = f'{mapping.developer_uid} {mapping.runtime_uid} 1\n'
        gid_map = f'{mapping.developer_gid} {mapping.runtime_gid} 1\n'
        (proc / 'setgroups').write_text('deny\n')
        (proc / 'uid_map').write_text(uid_map)
        (proc / 'gid_map').write_text(gid_map)
        for name, expected in (('uid_map', uid_map), ('gid_map', gid_map)):
            if (proc / name).read_text().split() != expected.split():
                raise IdmapError('Unexpected namespace ID mapping')
        fd = os.open(proc / 'ns/user', os.O_RDONLY | os.O_CLOEXEC)
        yield fd
    finally:
        if fd is not None:
            os.close(fd)
        child.stdin.close()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
        finally:
            child.stdout.close()


def validate_sources(source_fd, target_fd, mapping):
    if os.geteuid() != 0:
        raise IdmapError('Mount preparation requires the trusted administrator')
    source, target = os.fstat(source_fd), os.fstat(target_fd)
    if (not stat.S_ISDIR(source.st_mode)
            or (source.st_uid, source.st_gid) != mapping.developer):
        raise IdmapError('Source owner does not match approved shared data')
    if (not stat.S_ISDIR(target.st_mode) or target.st_uid != 0 or target.st_gid != 0
            or stat.S_IMODE(target.st_mode) != 0o700
            or os.path.samestat(source, target)):
        raise IdmapError('Expected an empty protected publication directory')
    # Inspect the pinned target rather than its pathname.
    fd = os.open('.', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                 | os.O_CLOEXEC, dir_fd=target_fd)
    try:
        if os.listdir(fd):
            raise IdmapError('Publication directory is not empty')
    finally:
        os.close(fd)
    return source


def publish_idmapped(source_fd, target_fd, mapping, *, readonly=False):
    """Attach an exact non-recursive ID map to two already-authorized FDs.

    No project path is re-opened. All attributes and owner checks happen on
    the detached clone, before publication. Closing an unattached tree on
    failure drops it. The caller owns the attached mount and its cleanup.
    """
    if not isinstance(mapping, IdMapping):
        raise IdmapError('Expected a validated ID mapping')
    source = validate_sources(source_fd, target_fd, mapping)
    tree, setattr, move = mount_api()
    fd = checked_call(tree, source_fd, b'',
                      AT_EMPTY_PATH | OPEN_TREE_CLONE | os.O_CLOEXEC)
    try:
        with mapping_namespace(mapping) as userns_fd:
            attrs = MountAttr(MOUNT_ATTR_IDMAP | MOUNT_ATTR_NOSUID |
                              MOUNT_ATTR_NODEV | MOUNT_ATTR_NOEXEC |
                              (MOUNT_ATTR_RDONLY if readonly else 0),
                              0, 0, userns_fd)
            checked_call(setattr, fd, b'', AT_EMPTY_PATH,
                         ctypes.byref(attrs), ctypes.sizeof(attrs))
        view = os.fstat(fd)
        if (not os.path.samestat(source, view)
                or (view.st_uid, view.st_gid) != mapping.runtime
                or os.fstatvfs(fd).f_flag & MOUNT_FLAGS != MOUNT_FLAGS
                or bool(os.fstatvfs(fd).f_flag & os.ST_RDONLY) != readonly):
            raise IdmapError('Detached mapping or flags do not match request')
        checked_call(move, fd, b'', target_fd, b'',
                     MOVE_MOUNT_F_EMPTY_PATH | MOVE_MOUNT_T_EMPTY_PATH)
    finally:
        os.close(fd)
