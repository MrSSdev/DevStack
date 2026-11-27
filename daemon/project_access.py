#!/usr/bin/env python3
"""ACL preparation for new, quiescent, private disposable project trees.

The owner applies permissions, never a privileged recursive chmod/chown.
Existing ACLs, hardlinks, foreign owners, special nodes and mount crossings
are not silently normalized. This is not a legacy migration or an operation
that may run against a concurrently edited/agent-controlled production tree.
"""
from __future__ import annotations

import ctypes
import errno
import os
from pathlib import Path
import pwd
import re
import stat
import sys

WRITABLE = (('storage',), ('bootstrap', 'cache'), ('database',))
MAX_ENTRIES = 20000
MAX_DEPTH = 64
PATH_FLAGS = os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC


class AccessError(RuntimeError):
    pass


def visit(fd, relative=()):
    """Yield a borrowed descriptor; the caller checks each node before descent."""
    yield relative, fd
    if not stat.S_ISDIR(os.fstat(fd).st_mode):
        return
    if len(relative) >= MAX_DEPTH:
        raise AccessError('Project tree exceeds preparation depth')
    directory = os.open('.', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
                        dir_fd=fd)
    try:
        with os.scandir(directory) as children:
            for entry in children:
                child = os.open(entry.name, PATH_FLAGS, dir_fd=directory)
                try:
                    yield from visit(child, (*relative, entry.name))
                finally:
                    os.close(child)
    finally:
        os.close(directory)


def descriptor_path(fd):
    return '/proc/self/fd/' + str(fd)


def has_xattr(fd, name):
    try:
        os.getxattr(descriptor_path(fd), name)
        return True
    except OSError as exc:
        if exc.errno == errno.ENODATA:
            return False
        raise


def writable(relative):
    return any(relative[:len(prefix)] == prefix for prefix in WRITABLE)


def private_entry(fd, relative, developer_uid, developer_gid, device, mount_id):
    """Validate metadata on the actual inode, without reading its contents."""
    info = os.fstat(fd)
    if (mount_id(fd) != device or info.st_uid != developer_uid
            or info.st_gid != developer_gid):
        raise AccessError('Foreign owner, group or mount in project')
    if not stat.S_ISDIR(info.st_mode) and info.st_nlink != 1:
        raise AccessError('Hardlinked or unlinked project entry')
    if stat.S_ISLNK(info.st_mode):
        # Keep normal framework/package symlinks. Do not follow, inspect ACLs
        # through, or grant access to their targets.
        return info
    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
        raise AccessError('Special project entry')
    if info.st_mode & 0o7077:
        raise AccessError('Non-private mode requires enrollment review')
    required = 0o700 if stat.S_ISDIR(info.st_mode) else (
        0o600 if writable(relative) else 0o400)
    if info.st_mode & required != required:
        raise AccessError('Owner permissions require enrollment review')
    if (has_xattr(fd, 'system.posix_acl_access')
            or stat.S_ISDIR(info.st_mode) and has_xattr(fd, 'system.posix_acl_default')
            or stat.S_ISREG(info.st_mode) and has_xattr(fd, 'security.capability')):
        raise AccessError('Existing extended permissions require enrollment review')
    return info


def fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_ctime_ns, info.st_mtime_ns)


def inspect_tree(root, developer_uid, developer_gid, device, mount_id):
    """Read-only first pass; a rejected tree receives no ACL changes."""
    snapshot = {}
    for relative, fd in visit(root):
        if len(snapshot) >= MAX_ENTRIES:
            raise AccessError('Project tree exceeds preparation limit')
        info = private_entry(fd, relative, developer_uid, developer_gid, device, mount_id)
        snapshot[relative] = fingerprint(info)
    return snapshot


def permission(bits):
    return ''.join(char if bits & mask else '-'
                   for char, mask in zip('rwx', (4, 2, 1)))


def acl_plan(info, relative, developer_uid, runtime_uid, *, mapped_data=False):
    if stat.S_ISLNK(info.st_mode) or (mapped_data and writable(relative)):
        return ()
    directory = stat.S_ISDIR(info.st_mode)
    owner = permission((info.st_mode >> 6) & 7)
    shared = writable(relative)
    runtime = permission((6 if shared else 4) |
                         (1 if directory or info.st_mode & stat.S_IXUSR else 0))
    access = 'u::' + owner + ',u:' + str(runtime_uid) + ':' + runtime
    access += ',g::---,m::' + runtime + ',o::---'
    if not directory:
        return ((0x8000, access),)
    # A default named developer ACL survives new runtime-owned entries.
    # No membership in the runtime's private group is needed.
    default = 'u::rwx,u:' + str(runtime_uid) + ':' + runtime
    if shared:
        default += ',u:' + str(developer_uid) + ':rwx'
    default += ',g::---,m::' + runtime + ',o::---'
    return ((0x8000, access), (0x4000, default))


class LinuxACL:
    def __init__(self):
        # Fixed system library, not a plugin loaded from the project.
        self.lib = ctypes.CDLL('libacl.so.1', use_errno=True)
        self.lib.acl_from_text.argtypes = [ctypes.c_char_p]
        self.lib.acl_from_text.restype = ctypes.c_void_p
        self.lib.acl_set_file.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p]
        self.lib.acl_set_file.restype = ctypes.c_int
        self.lib.acl_free.argtypes = [ctypes.c_void_p]
        self.lib.acl_free.restype = ctypes.c_int

    def apply(self, fd, kind, value):
        acl = self.lib.acl_from_text(value.encode('ascii'))
        if not acl:
            raise OSError(ctypes.get_errno(), 'Cannot construct ACL')
        try:
            # libacl's file interface follows this procfs descriptor reference
            # to the pinned inode. It never reopens an agent-controlled name.
            if self.lib.acl_set_file(descriptor_path(fd).encode('ascii'), kind, acl):
                raise OSError(ctypes.get_errno(), 'Cannot apply pinned ACL')
        finally:
            self.lib.acl_free(acl)


def prepare_tree(root, developer_uid, developer_gid, runtime_uid, device, mount_id,
                 *, mapped_data=False):
    if (developer_uid != os.geteuid() or developer_gid != os.getegid()
            or developer_uid == 0 or developer_gid == 0
            or type(runtime_uid) is not int or not 0 < runtime_uid < 2**32-1
            or runtime_uid == developer_uid):
        raise AccessError('Expected distinct unprivileged project identities')
    snapshot = inspect_tree(root, developer_uid, developer_gid, device, mount_id)
    acl = LinuxACL()
    seen = set()
    for relative, fd in visit(root):
        info = private_entry(fd, relative, developer_uid, developer_gid, device, mount_id)
        if snapshot.get(relative) != fingerprint(info):
            raise AccessError('Tree changed during permission preparation')
        seen.add(relative)
        if relative:
            for kind, text in acl_plan(info, relative, developer_uid, runtime_uid, mapped_data=mapped_data):
                acl.apply(fd, kind, text)
    if seen != set(snapshot):
        raise AccessError('Tree changed during permission preparation')
    # Publish the root traversal permission last. This is a staged fixture,
    # not a transactional rollback guarantee for an existing live project.
    info = private_entry(root, (), developer_uid, developer_gid, device, mount_id)
    if snapshot[()] != fingerprint(info):
        raise AccessError('Project root changed during preparation')
    # Set default ACL first, then expose traversal through the access ACL.
    for kind, text in reversed(acl_plan(info, (), developer_uid, runtime_uid, mapped_data=mapped_data)):
        acl.apply(root, kind, text)
    return len(snapshot)


def main():
    if (len(sys.argv) not in (2, 3) or sys.argv[2:] not in ([], ['idmapped'])
            or not sys.flags.isolated or os.geteuid() == 0):
        raise AccessError('Expected fixed non-root fixture worker')
    project = Path(sys.argv[1])
    base = project.parent
    if (base.parent != Path('/var/lib')
            or not re.fullmatch(r'devstack-runtime-probe-[a-zA-Z0-9_]{8}', base.name)
            or project.name not in ('project-a', 'project-b')):
        raise AccessError('Expected protected disposable fixture')
    launcher = base / 'launcher'
    if Path(__file__).absolute() != launcher / 'project_access.py':
        raise AccessError('Expected staged fixture worker')
    for path in (Path('/'), Path('/var'), Path('/var/lib'), base, launcher):
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise AccessError('Unprotected worker directory')
    for name in ('project_access.py', 'project_runtime_identity.py', 'project_mounts.py'):
        info = (launcher / name).lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise AccessError('Unprotected worker file')
    developer = pwd.getpwuid(os.geteuid())
    if (not re.fullmatch(r'dsrt-dev-[a-f0-9]{10}', developer.pw_name)
            or developer.pw_shell != '/usr/sbin/nologin'
            or developer.pw_dir != '/nonexistent'
            or developer.pw_gid != os.getegid()
            or set(os.getgroups()) - {developer.pw_gid}):
        raise AccessError('Unexpected fixture developer')
    sys.path.insert(0, str(launcher))
    from project_mounts import PinnedProject, mount_id
    from project_runtime_identity import verify_binding

    record = verify_binding(base, project.name[-1])
    with PinnedProject(project) as pinned:
        prepare_tree(pinned.root, developer.pw_uid, developer.pw_gid,
                     record['uid'], pinned.device, mount_id,
                     mapped_data=sys.argv[2:] == ['idmapped'])
    print('PASS: Non-root ACL preparation of private fixture')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print('Fixture access preparation refused: ' + type(exc).__name__, file=sys.stderr)
        raise SystemExit(2)
