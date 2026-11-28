"""Administrator-controlled Linux executor for synthetic projects only."""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
import errno
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys

MODULES = (
    'project_executor.py', 'project_pilot.py', 'project_isolation_preflight.py',
    'project_runtime_identity.py', 'project_idmap.py', 'project_mounts.py',
    'project_access.py', 'project_data_views.py', 'project_runtime.py',
    'project_http.py', 'project_lifecycle.py',
)


def check_installation(base):
    if (os.geteuid() != 0 or not sys.flags.isolated or base.parent != Path('/var/lib')
            or not re.fullmatch(r'devstack-executor-probe-[a-zA-Z0-9_]{8}', base.name)
            or Path(__file__).absolute() != base / 'launcher/project_executor.py'):
        raise ValueError('Expected protected disposable executor installation')
    launcher = base / 'launcher'
    for path in (*reversed(launcher.parents), launcher, *(launcher / n for n in MODULES)):
        info = path.lstat()
        kind = stat.S_ISREG if path.parent == launcher else stat.S_ISDIR
        if not kind(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Unprotected disposable executor installation')


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit('Expected a disposable fixture and session ID')
    fixture = Path(sys.argv[1])
    check_installation(fixture)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(fixture / 'launcher'))

from project_access import MAX_ENTRIES, visit
from project_idmap import IdMapping
from project_isolation_preflight import protected_control, protected_parents, read_control_json
from project_lifecycle import (IO_URING_SYSCALLS, RUNTIME_SECURITY_SETTINGS, STOPPED, SYSTEMCTL,
                               SYSTEMD_RUN, run, unit_state)
from project_mounts import DIR_FLAGS, PinnedProject, exec_pinned, mount_id
from project_pilot import make_mounts_private, mapped_view, require_backend
from project_runtime_identity import PROJECT_ID, verify_binding

WORKSPACE = '/workspace'
TOOLS = frozenset({'/usr/bin/python3', '/usr/bin/php8.5', '/usr/bin/git',
                   '/usr/bin/node', '/usr/bin/npm', '/usr/bin/composer', '/usr/bin/sh'})
EXECUTOR_PROPERTIES = (
    # Bubblewrap needs IPv4 for loopback setup. Its payload receives a
    # second filter restricting sockets to AF_UNIX in the private netns.
    *RUNTIME_SECURITY_SETTINGS,
    '--property=SystemCallFilter=~' + IO_URING_SYSCALLS + ' ptrace process_vm_readv process_vm_writev '
    'bpf perf_event_open keyctl add_key request_key open_by_handle_at',
    '--property=PrivateMounts=yes',
    '--property=KillMode=control-group',
    '--property=TimeoutStopSec=3s',
    '--property=TasksMax=64',
    '--property=UMask=0077',
    '--property=LimitFSIZE=16M',
    '--property=LimitCORE=0',
    '--property=StandardInput=null',
    '--property=StandardError=inherit',
)


def identifier(value):
    if not isinstance(value, str) or not PROJECT_ID.fullmatch(value):
        raise ValueError('Invalid fixture or session identity')
    return value


@dataclass(frozen=True)
class Session:
    base: Path
    session_id: str

    def __post_init__(self):
        identifier(self.session_id)

    @property
    def record(self):
        return self.base / ('session-' + self.session_id)

    @property
    def runtime_directory(self):
        return 'devstack-executor-' + self.session_id

    @property
    def unit(self):
        return self.runtime_directory + '.service'

    @property
    def description(self):
        return f'DevStack disposable executor {self.base.name} {self.session_id}'

    @property
    def registration(self):
        # The shared systemd identity check expects a root setup phase
        # when a registration identity is present.
        return self.session_id

    def request(self):
        value = read_control_json(self.record / 'launch.json')
        if (set(value) != {'schema', 'session_id', 'project_id', 'agent_id', 'profile', 'argv'}
                or type(value['schema']) is not int or value['schema'] != 1
                or value['session_id'] != self.session_id
                or value['profile'] not in ('read', 'write-cli')
                or not isinstance(value['agent_id'], str)
                or not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,63}', value['agent_id'])):
            raise ValueError('Invalid executor fixture request')
        identifier(value['project_id'])
        argv = value['argv']
        if (not isinstance(argv, list) or not 1 <= len(argv) <= 32
                or any(not isinstance(arg, str) or '\0' in arg or len(arg) > 4096 for arg in argv)
                or argv[0] not in TOOLS):
            raise ValueError('Invalid fixture command vector')
        return value

    def service_argv(self, *, runtime_seconds=90):
        if type(runtime_seconds) is not int or not 1 <= runtime_seconds <= 90:
            raise ValueError('Invalid executor fixture runtime limit')
        self.request()
        verify_binding(self.record, 'e')
        directory = Path('/run') / self.runtime_directory
        if unit_state(self) != 'absent' or os.path.lexists(directory):
            raise ValueError('Executor unit or runtime directory already exists')
        return [SYSTEMD_RUN, '--quiet', '--wait', '--collect', '--service-type=exec',
                '--unit=' + self.unit, '--description=' + self.description,
                *EXECUTOR_PROPERTIES, '--property=RuntimeMaxSec=' + str(runtime_seconds) + 's',
                '--property=RuntimeDirectory=' + self.runtime_directory,
                '--property=RuntimeDirectoryMode=0711',
                '--property=StandardOutput=file:' + str(self.record / 'output'),
                '/usr/bin/python3', '-I',
                str(self.base / 'launcher/project_executor.py'), str(self.base), self.session_id]

    def stop(self):
        # Load the protected session identity even after project removal.
        self.request()
        if unit_state(self) != 'absent':
            result = run([SYSTEMCTL, 'stop', '--', self.unit], timeout=15)
            if result.returncode and unit_state(self) != 'absent':
                raise RuntimeError('Executor unit stop failed')
        if unit_state(self) not in STOPPED or os.path.lexists(Path('/run') / self.runtime_directory):
            raise RuntimeError('Executor cleanup not confirmed')


def validated_project(session, project_id):
    value = read_control_json(session.base / ('registration-' + project_id + '.json'))
    if (set(value) != {'schema', 'project_id', 'source'}
            or type(value['schema']) is not int or value['schema'] != 1
            or value['project_id'] != project_id
            or not isinstance(value['source'], list) or len(value['source']) != 2
            or any(type(n) is not int or n <= 0 for n in value['source'])):
        raise ValueError('Invalid synthetic project registration')
    developer = verify_binding(session.base / ('developer-' + project_id), 'd')
    account = verify_binding(session.record, 'e')
    return value['source'], IdMapping(developer['uid'], developer['gid'],
                                     account['uid'], account['gid'])


def validate_tree(pinned, mapping):
    # This is a bounded launch-time check within the synthetic fixture.
    # The protected parent pins the top level. Active sessions can create
    # names only in the mapped fixture and cannot link from another mount.
    for index, (relative, fd) in enumerate(visit(pinned.root)):
        info = os.fstat(fd)
        if (index >= MAX_ENTRIES or mount_id(fd) != pinned.device
                or (info.st_uid, info.st_gid) != mapping.developer
                or not (stat.S_ISDIR(info.st_mode) or
                        (stat.S_ISREG(info.st_mode) and info.st_nlink == 1))
                or info.st_mode & 0o7022
                or relative and relative[-1].casefold() in ('.git', '.gitmodules')):
            raise ValueError('Unsafe fixture tree metadata')


class ScmpArgCompare(ctypes.Structure):
    _fields_ = [('arg', ctypes.c_uint), ('op', ctypes.c_int),
                ('datum_a', ctypes.c_uint64), ('datum_b', ctypes.c_uint64)]


def socket_filter():
    """Export a native libseccomp filter for Bubblewrap to apply after setup."""
    lib = ctypes.CDLL('libseccomp.so.2')
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_rule_add_array.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int,
                                          ctypes.c_uint, ctypes.POINTER(ScmpArgCompare)]
    lib.seccomp_export_bpf.argtypes = [ctypes.c_void_p, ctypes.c_int]
    allow = 0x7fff0000  # SCMP_ACT_ALLOW
    deny = 0x00050000 | errno.EAFNOSUPPORT  # SCMP_ACT_ERRNO
    condition = ScmpArgCompare(0, 1, socket.AF_UNIX, 0)  # SCMP_CMP_NE
    ctx = lib.seccomp_init(allow)
    if not ctx:
        raise RuntimeError('Cannot initialize executor socket filter')
    try:
        for name in (b'socket', b'socketpair'):
            number = lib.seccomp_syscall_resolve_name(name)
            if number < 0 or lib.seccomp_rule_add_array(ctx, deny, number, 1, ctypes.byref(condition)):
                raise RuntimeError('Cannot configure executor socket filter')
        # A named filesystem socket in a shared RW workspace would permit
        # exchanging descriptors across sessions, unlike a private socketpair.
        bind = lib.seccomp_syscall_resolve_name(b'bind')
        if bind < 0 or lib.seccomp_rule_add_array(ctx, 0x00050000 | errno.EACCES, bind, 0, None):
            raise RuntimeError('Cannot disable named Unix sockets')
        fd = os.memfd_create('devstack-executor-seccomp', os.MFD_CLOEXEC)
        try:
            if lib.seccomp_export_bpf(ctx, fd) or not os.fstat(fd).st_size:
                raise RuntimeError('Cannot export executor socket filter')
            os.lseek(fd, 0, os.SEEK_SET)
            return fd
        except BaseException:
            os.close(fd)
            raise
    finally:
        lib.seccomp_release(ctx)


def sandbox_argv(view_fd, git_fd, proof_fd, filter_fd, request):
    bind = '--ro-bind-fd' if request['profile'] == 'read' else '--bind-fd'
    args = ['/usr/bin/bwrap', '--unshare-user', '--unshare-pid', '--unshare-net',
            '--unshare-ipc', '--unshare-uts', '--disable-userns', '--cap-drop', 'ALL',
            '--die-with-parent', '--new-session', '--clearenv']
    for path in ('/usr/bin', '/usr/lib', '/usr/share/git-core', '/usr/share/nodejs',
                 '/usr/share/php', '/etc/php/8.5/cli', '/etc/php/8.5/mods-available'):
        args += ['--ro-bind', path, path]
    if Path('/usr/lib64').is_dir():
        args += ['--ro-bind', '/usr/lib64', '/usr/lib64', '--symlink', 'usr/lib64', '/lib64']
    if os.path.lexists('/usr/lib/wsl'):
        args += ['--tmpfs', '/usr/lib/wsl', '--remount-ro', '/usr/lib/wsl']
    args += ['--symlink', 'usr/bin', '/bin', '--symlink', 'usr/lib', '/lib',
             '--symlink', '/usr/bin/php8.5', '/etc/alternatives/php',
             '--proc', '/proc', '--dev', '/dev',
             '--size', str(64 * 1024 * 1024), '--tmpfs', '/tmp',
             '--perms', '0700', '--dir', '/tmp/home',
             '--perms', '0700', '--dir', '/tmp/cache',
             bind, str(view_fd), WORKSPACE,
             '--ro-bind-fd', str(git_fd), '/run/git',
             '--ro-bind-data', str(proof_fd), '/run/devstack-proof.py',
             '--remount-ro', '/', '--chdir', WORKSPACE, '--seccomp', str(filter_fd)]
    for key, value in {
        'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'HOME': '/tmp/home',
        'TMPDIR': '/tmp', 'XDG_CACHE_HOME': '/tmp/cache', 'XDG_CONFIG_HOME': '/tmp/home/.config',
        'GIT_DIR': '/run/git', 'GIT_WORK_TREE': WORKSPACE, 'GIT_CONFIG_NOSYSTEM': '1',
        'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_OPTIONAL_LOCKS': '0',
        'GIT_CONFIG_COUNT': '2',
        'GIT_CONFIG_KEY_0': 'safe.directory', 'GIT_CONFIG_VALUE_0': WORKSPACE,
        'GIT_CONFIG_KEY_1': 'safe.directory', 'GIT_CONFIG_VALUE_1': '/run/git',
        'GIT_TERMINAL_PROMPT': '0', 'COMPOSER_HOME': '/tmp/cache/composer',
        'COMPOSER_DISABLE_NETWORK': '1', 'npm_config_cache': '/tmp/cache/npm',
        'npm_config_offline': 'true', 'npm_config_update_notifier': 'false',
    }.items():
        args += ['--setenv', key, value]
    return [*args, '--', *request['argv']]


def drop_credentials(mapping):
    os.setgroups([])
    os.setresgid(*(mapping.runtime_gid,) * 3)
    os.setresuid(*(mapping.runtime_uid,) * 3)
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines()
                  if ':' in line)
    if (os.getresuid() != (mapping.runtime_uid,) * 3
            or os.getresgid() != (mapping.runtime_gid,) * 3 or os.getgroups()
            or any(int(status[key], 16) for key in ('CapEff', 'CapPrm', 'CapInh', 'CapAmb'))
            or status['NoNewPrivs'].strip() != '1' or status['Seccomp'].strip() != '2'):
        raise ValueError('Executor credentials or systemd policy not confirmed')


def close_inherited_fds():
    # listdir closes its own directory FD before returning.
    for name in os.listdir('/proc/self/fd'):
        fd = int(name)
        if fd > 2:
            try:
                os.close(fd)
            except OSError as exc:
                if exc.errno != errno.EBADF:
                    raise
    if (not stat.S_ISCHR(os.fstat(0).st_mode)
            or os.fstat(0).st_rdev != os.stat('/dev/null').st_rdev
            or any(not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_uid != 0
                   for fd in (1, 2))):
        raise ValueError('Unexpected executor standard descriptors')


def launch(session):
    close_inherited_fds()
    require_backend()
    request = session.request()
    project_id = request['project_id']
    reference, mapping = validated_project(session, project_id)
    work = Path('/run') / session.runtime_directory
    protected_parents(work)
    protected_control(work, directory=True)
    if stat.S_IMODE(work.stat().st_mode) != 0o711 or any(work.iterdir()):
        raise ValueError('Expected empty systemd executor directory')
    make_mounts_private()
    source = session.base / ('project-' + project_id)
    git = session.base / ('git-' + project_id)
    proof = session.base / 'launcher/devstack-proof.py'
    protected_control(git, directory=True)
    protected_control(proof, directory=False)
    # The mapped FD is the sole project reference carried into Bubblewrap.
    with PinnedProject(source) as pinned, PinnedProject(session.base) as anchor:
        info = os.fstat(pinned.root)
        if (reference != [info.st_dev, info.st_ino] or pinned.device != anchor.device
                or stat.S_IMODE(info.st_mode) != 0o700):
            raise ValueError('Synthetic project mount source changed')
        validate_tree(pinned, mapping)
        pinned.fds.append(mapped_view(pinned.root, work / 'workspace', mapping,
                                      readonly=request['profile'] == 'read'))
        pinned.fds.append(os.open(git, DIR_FLAGS))
        # Pass the reviewed payload by data FD. Bubblewrap may canonicalize
        # bind-FD paths, so the root-only launcher directory stays private.
        pinned.fds.append(os.open(proof, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC))
        pinned.fds.append(socket_filter())
        args = sandbox_argv(*pinned.fds, request)
        drop_credentials(mapping)
        os.chdir('/')
        exec_pinned(args, pinned)


if __name__ == '__main__':
    try:
        launch(Session(fixture, sys.argv[2]))
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print('Disposable executor refused: ' + str(exc)[:240], file=sys.stderr)
        raise SystemExit(2)
