"""ID-mapped data lifecycle for the disposable systemd runtime fixture.

Unlike the earlier private-namespace CLI smoke, PID 1 must see these
publications before starting the non-root PHP/HTTP services. All mounts
are confined to a unique protected CI fixture directory. The holder is
made private before any shared data is attached to it.

This is NOT a production registry, recovery mechanism or mount RPC.
The controller remembers only mounts it created in this process. It
refuses unknown or replaced publications instead of adopting them.
The fixture is quiescent during preparation; no concurrent untrusted
editor or administrator may change its tree or mount topology.
"""
from __future__ import annotations

import os
from pathlib import Path
import pwd
import re
import stat
import subprocess

from project_access import MAX_ENTRIES, visit
from project_idmap import IdMapping, publish_idmapped
from project_mounts import (DIR_FLAGS, PinnedProject, SHARED_DATA, mount_id,
                            open_directory, reject_nested_mounts, verify_data_view,
                            view_name)
from project_runtime_identity import verify_binding


class PublicationError(RuntimeError):
    pass


def reference(path):
    fd = os.open(path, DIR_FLAGS)
    try:
        info = os.fstat(fd)
        return mount_id(fd), info.st_dev, info.st_ino
    finally:
        os.close(fd)


def mount_command(*args):
    subprocess.run(args, check=True, timeout=15, close_fds=True,
                   env={'PATH': '/usr/bin:/bin', 'LANG': 'C'})


def private_holder(path, expected):
    if reference(path) != expected:
        raise PublicationError('Publication holder changed')
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        fields = line.split(' - ', 1)[0].split()
        if int(fields[0]) == expected[0]:
            if any(x.startswith(('shared:', 'master:', 'propagate_from:'))
                   for x in fields[6:]):
                raise PublicationError('Publication holder is not private')
            return
    raise PublicationError('Publication mount not found')


def validate_shared_tree(fd, mapping, device):
    # Metadata only, bounded, no symlink following. Unlike the initial ACL
    # fixture preparation, normal runtime-created modes such as 0644 are
    # allowed. Foreign owners, special nodes, hardlinks and nested mounts
    # are not. This scan does not authorize concurrent hostile editors.
    for index, (_, child) in enumerate(visit(fd)):
        info = os.fstat(child)
        if (index >= MAX_ENTRIES or mount_id(child) != device
                or (info.st_uid, info.st_gid) != mapping.developer
                or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)
                        or stat.S_ISLNK(info.st_mode))
                or (not stat.S_ISDIR(info.st_mode) and info.st_nlink != 1)):
            raise PublicationError('Unexpected shared data metadata')


class FixtureDataViews:
    def __init__(self, project, identity):
        if (os.geteuid() != 0 or os.getenv('GITHUB_ACTIONS') != 'true'
                or os.getenv('DEVSTACK_RUNTIME_CI') != '1'
                or 'microsoft' in Path('/proc/sys/kernel/osrelease').read_text().lower()
                or not os.path.samestat(os.stat('/proc/self/ns/mnt'),
                                        os.stat('/proc/1/ns/mnt'))):
            raise PublicationError('Expected disposable CI controller in PID 1 mount namespace')
        self.project = Path(project)
        self.base = self.project.parent
        self.slot = self.project.name[-1]
        if (self.project.name not in ('project-a', 'project-b')
                or verify_binding(self.base, self.slot) != identity):
            raise PublicationError('Unexpected fixture identity')
        self.identity = identity
        with PinnedProject(self.project) as pinned:
            info = os.fstat(pinned.root)
        developer = pwd.getpwuid(info.st_uid)
        if (not re.fullmatch(r'dsrt-dev-[a-f0-9]{10}', developer.pw_name)
                or developer.pw_uid == 0 or developer.pw_gid != info.st_gid
                or developer.pw_dir != '/nonexistent'
                or developer.pw_shell != '/usr/sbin/nologin'
                or set(os.getgrouplist(developer.pw_name, developer.pw_gid)) != {developer.pw_gid}):
            raise PublicationError('Unexpected disposable developer identity')
        self.mapping = IdMapping(developer.pw_uid, developer.pw_gid,
                                 identity['uid'], identity['gid'])
        self.root = self.base / ('mapped-' + self.slot)
        # Each pair is (original directory reference, current mount reference).
        self.holder = None
        self.slots = {}

    def state(self):
        try:
            if self.holder is None:
                return 'absent' if not os.path.lexists(self.root) else 'degraded'
            before, after = self.holder
            if (before == after or reference(self.root) != after
                    or set(self.slots) != set(SHARED_DATA)
                    or set(os.listdir(self.root)) != {view_name(x) for x in SHARED_DATA}):
                return 'degraded'
            private_holder(self.root, after)
            for relative, (original, current) in self.slots.items():
                target = self.root / view_name(relative)
                if original == current or reference(target) != current:
                    return 'degraded'
                reject_nested_mounts(target)
            return 'ready'
        except (OSError, RuntimeError, ValueError):
            return 'degraded'

    def publish(self):
        if self.state() != 'absent' or self.slots:
            raise PublicationError('Unexpected existing publication')
        if verify_binding(self.base, self.slot) != self.identity:
            raise PublicationError('Runtime identity changed')
        # The protected base was verified above and by the lifecycle lock.
        # The holder is newly created by root, never by a project.
        self.root.mkdir(mode=0o711)
        self.root.chmod(0o711)
        original = reference(self.root)
        self.holder = (original, original)
        mount_command('/usr/bin/mount', '-n', '--bind', str(self.root), str(self.root))
        mounted = reference(self.root)
        self.holder = (original, mounted)
        if original[0] == mounted[0] or original[1:] != mounted[1:]:
            raise PublicationError('Unexpected holder bind')
        mount_command('/usr/bin/mount', '-n', '--make-private', str(self.root))
        private_holder(self.root, mounted)
        with PinnedProject(self.project) as pinned:
            for relative in SHARED_DATA:
                target = self.root / view_name(relative)
                target.mkdir(mode=0o700)
                original = reference(target)
                self.slots[relative] = (original, original)
                source_fd = open_directory(pinned.root, relative, pinned.device)
                try:
                    validate_shared_tree(source_fd, self.mapping, pinned.device)
                    target_fd = os.open(target, DIR_FLAGS)
                    try:
                        publish_idmapped(source_fd, target_fd, self.mapping)
                    finally:
                        os.close(target_fd)
                    current = reference(target)
                    self.slots[relative] = (original, current)
                    view_fd = os.open(target, DIR_FLAGS)
                    try:
                        verify_data_view(source_fd, view_fd, self.mapping.runtime)
                    finally:
                        os.close(view_fd)
                finally:
                    os.close(source_fd)
        if self.state() != 'ready':
            raise PublicationError('Mapped data readiness not confirmed')

    def remove(self):
        # Caller must confirm BOTH runtime services are stopped before this.
        # Never lazy-detach, unmount by a guessed name or delete mounted data.
        if self.holder is None:
            if self.state() != 'absent':
                raise PublicationError('Unknown publication; refusing cleanup')
            return
        original_holder, mounted_holder = self.holder
        if (reference(self.root) != mounted_holder
                or set(os.listdir(self.root)) != {view_name(x) for x in self.slots}):
            raise PublicationError('Publication replaced or unexpected entries')
        # Verify every publication before changing any of them.
        for relative, (_, current) in self.slots.items():
            target = self.root / view_name(relative)
            if reference(target) != current:
                raise PublicationError('Publication identity changed')
            reject_nested_mounts(target)
        for relative in reversed(tuple(self.slots)):
            target = self.root / view_name(relative)
            original, current = self.slots[relative]
            if original != current:
                mount_command('/usr/bin/umount', '-n', '--', str(target))
                self.slots[relative] = (original, original)
            if reference(target) != original:
                raise PublicationError('Publication slot did not return')
            target.rmdir()
            del self.slots[relative]
        reject_nested_mounts(self.root)
        if original_holder != mounted_holder:
            mount_command('/usr/bin/umount', '-n', '--', str(self.root))
            self.holder = (original_holder, original_holder)
        if reference(self.root) != original_holder:
            raise PublicationError('Publication holder did not return')
        self.root.rmdir()
        self.holder = None
