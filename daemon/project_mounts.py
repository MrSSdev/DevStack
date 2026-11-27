"""Pin project directory mount sources under the runtime's unprivileged UID.

This is a source-selection primitive, not a grant or a complete filesystem
boundary. The caller must authorize the project and its identity first. The
current launcher is restricted to disposable protected fixtures.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat

DIR_FLAGS = os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
SHARED_DATA = ('storage', 'bootstrap/cache', 'database')
DATA_FLAGS = os.ST_NOSUID | os.ST_NODEV | os.ST_NOEXEC


def mount_id(fd):
    for line in Path('/proc/self/fdinfo/' + str(fd)).read_text().splitlines():
        if line.startswith('mnt_id:'):
            return int(line.split(':', 1)[1])
    raise OSError('Mount identity unavailable')


def components(relative):
    if not isinstance(relative, str):
        raise ValueError('Expected a relative directory name')
    if relative == '':
        return []
    parts = relative.split('/')
    if any(p in ('', '.', '..') or not re.fullmatch(r'[A-Za-z0-9._-]+', p)
           for p in parts):
        raise ValueError('Unsafe relative directory')
    return parts


def open_directory(parent_fd, relative, expected_mount):
    """Select by descriptor; do not reopen the checked name for mounting."""
    fd = os.dup(parent_fd)
    try:
        for part in components(relative):
            child = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
            if mount_id(fd) != expected_mount:
                raise ValueError('Unexpected mount boundary')
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise ValueError('Expected directory')
        result = fd
        fd = None
        return result
    finally:
        if fd is not None:
            os.close(fd)


def decode_mount_path(value):
    # mountinfo encodes spaces, tabs, newlines and backslashes as octal.
    return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m.group(1), 8)), value)


def reject_nested_mounts(project):
    """Reject existing nested mounts; trusted host must serialize mount edits.

    A snapshot alone cannot prevent concurrent CAP_SYS_ADMIN mount changes.
    This is not authorization for a hostile administrator or a shared mount
    propagation topology. Hard links need separate enrollment controls.
    """
    prefix = str(project) + '/'
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        fields = line.split(' ')
        if len(fields) < 6:
            raise OSError('Invalid mountinfo')
        if decode_mount_path(fields[4]).startswith(prefix):
            raise ValueError('Nested project mount not permitted')


class PinnedProject:
    def __init__(self, path):
        self.path = Path(path)
        if (not self.path.is_absolute() or self.path == Path('/')
                or any(not re.fullmatch(r'[A-Za-z0-9._-]+', p) or p in ('.', '..')
                       for p in self.path.parts[1:])):
            raise ValueError('Invalid project path')
        self.root = None
        self.fds = []

    def __enter__(self):
        if self.root is not None:
            raise RuntimeError('Project already pinned')
        # Open each component without following a symlink, including the
        # ancestors. The project root may reside on an authorized filesystem;
        # every subsequently selected child must stay on that mount.
        fd = os.open('/', DIR_FLAGS)
        try:
            for part in self.path.parts[1:]:
                child = os.open(part, DIR_FLAGS, dir_fd=fd)
                os.close(fd)
                fd = child
            reject_nested_mounts(self.path)
            self.root = fd
            fd = None
            self.device = mount_id(self.root)
            return self
        except BaseException:
            self.close()
            raise
        finally:
            if fd is not None:
                os.close(fd)

    def bind(self, relative, *, writable=False, optional=False):
        if self.root is None:
            raise RuntimeError('Project not pinned')
        parts = components(relative)
        try:
            fd = open_directory(self.root, relative, self.device)
        except FileNotFoundError:
            if optional:
                return []
            raise
        self.fds.append(fd)
        destination = str(self.path.joinpath(*parts))
        return ['--bind-fd' if writable else '--ro-bind-fd', str(fd), destination]

    def close(self):
        for fd in self.fds:
            os.close(fd)
        self.fds.clear()
        if self.root is not None:
            os.close(self.root)
            self.root = None

    def __exit__(self, *exc):
        self.close()


def data_root(relative):
    parts = components(relative)
    for root in SHARED_DATA:
        prefix = components(root)
        if parts[:len(prefix)] == prefix:
            return root, '/'.join(parts[len(prefix):])
    return None


def view_name(relative):
    if relative not in SHARED_DATA:
        raise ValueError('Not an approved shared data root')
    return relative.replace('/', '-')


def verify_data_view(source_fd, view_fd, runtime):
    source, view = os.fstat(source_fd), os.fstat(view_fd)
    if (not os.path.samestat(source, view)
            or (source.st_uid, source.st_gid) == runtime
            or (view.st_uid, view.st_gid) != runtime
            or mount_id(source_fd) == mount_id(view_fd)
            or os.fstatvfs(view_fd).f_flag & DATA_FLAGS != DATA_FLAGS):
        raise ValueError('Mapped data identity or mount flags mismatch')


class MappedProject(PinnedProject):
    """Fixed fixture mode: never fall back to the original writable path.

    The controller publishes exactly the shared roots in a protected sibling
    directory. This launcher runs under the verified runtime UID, and pins
    the mapped root before selecting a nested read-only HTTP source.
    """
    def __init__(self, path, identity):
        super().__init__(path)
        if self.path.name not in ('project-a', 'project-b'):
            raise ValueError('Expected fixed fixture slot')
        self.holder = self.path.parent / ('mapped-' + self.path.name[-1])
        self.identity = identity

    def bind(self, relative, *, writable=False, optional=False):
        if self.root is None:
            raise RuntimeError('Project not pinned')
        selection = data_root(relative)
        if selection is None:
            if writable:
                raise ValueError('Writable source is not an approved data root')
            return super().bind(relative, optional=optional)
        root, suffix = selection
        source_fd = holder_fd = view_fd = selected_fd = None
        try:
            # Even an optional mapped root is mandatory in this fixture
            # profile. Only a nested published-storage directory may be absent.
            source_fd = open_directory(self.root, root, self.device)
            holder_fd = os.open(self.holder, DIR_FLAGS)
            info = os.fstat(holder_fd)
            if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o711:
                raise ValueError('Unprotected mapped data directory')
            view_fd = os.open(view_name(root), DIR_FLAGS, dir_fd=holder_fd)
            if mount_id(holder_fd) == mount_id(view_fd):
                raise ValueError('Mapped data was not published')
            verify_data_view(source_fd, view_fd,
                             (self.identity['uid'], self.identity['gid']))
            reject_nested_mounts(self.holder / view_name(root))
            try:
                selected_fd = open_directory(view_fd, suffix, mount_id(view_fd))
            except FileNotFoundError:
                if optional and suffix:
                    return []
                raise
            self.fds.append(selected_fd)
            result = ['--bind-fd' if writable else '--ro-bind-fd',
                      str(selected_fd), str(self.path / relative)]
            selected_fd = None
            return result
        finally:
            for fd in (selected_fd, view_fd, holder_fd, source_fd):
                if fd is not None:
                    os.close(fd)


def exec_pinned(argv, mounts):
    """Pass only selected mount FDs to bwrap; the root traversal FD is CLOEXEC."""
    if argv[0] != '/usr/bin/bwrap':
        raise ValueError('Expected fixed Bubblewrap executable')
    for fd in mounts.fds:
        os.set_inheritable(fd, True)
    try:
        os.execve(argv[0], argv, {'PATH': '/usr/bin:/bin', 'LANG': 'C'})
    finally:
        # execve only returns on failure.
        for fd in mounts.fds:
            os.set_inheritable(fd, False)
