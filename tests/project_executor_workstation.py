"""Read-only checks around a disposable executor run on installed DevStack."""
from contextlib import contextmanager
import fcntl
import grp
import os
from pathlib import Path
import pwd
import re
import stat

from project_access import MAX_ENTRIES, visit
from project_isolation_preflight import (
    CONFIG, REGISTRY, directory_chain, project_spec, protected_control,
    protected_parents, read_control_json,
)
from project_lifecycle import SYSTEMCTL, run
from project_mounts import DIR_FLAGS, mount_id

CORE = ('devstackd.service', 'nginx.service', 'php8.5-fpm.service')
SERVICES = (*CORE, 'mariadb.service', 'redis-server.service',
            'devstack-mailpit.service', 'devstack-notifications.timer')
PILOT_UNIT = re.compile(r'devstack-pilot-[0-9a-f]{32}-(php|http)\.service\Z')
CONTROL_TREES = tuple(map(Path, (
    '/etc/devstack', '/etc/nginx', '/etc/php/8.5', '/etc/systemd/system',
)))
CONTROL_FILES = (REGISTRY, REGISTRY.with_name('credentials.json'),
                 Path('/opt/devstack'), Path('/run/devstack/control.sock'))


def file_metadata(info):
    # Ignore access time. Observe identity, owner, permissions, replacement
    # and in-place writes without reading config/credential file contents.
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def path_metadata(path):
    try:
        return file_metadata(path.lstat())
    except FileNotFoundError:
        return None


def control_tree(path):
    protected_parents(path)
    protected_control(path, directory=True)
    root = os.open(path, DIR_FLAGS)
    try:
        device = mount_id(root)
        entries = {}
        for relative, fd in visit(root):
            info = os.fstat(fd)
            if (len(entries) >= MAX_ENTRIES or mount_id(fd) != device
                    or (stat.S_ISDIR(info.st_mode) and (info.st_uid or info.st_mode & 0o022))):
                raise ValueError('Cannot safely observe DevStack configuration tree')
            entries[relative] = file_metadata(info)
        return entries
    finally:
        os.close(root)


@contextmanager
def registry_snapshot_lock():
    # The installed daemon and pilot take an exclusive flock on this inode.
    # Hold a shared lock only while collecting snapshots. Never create it or
    # wait behind an active project operation on the workstation.
    path = REGISTRY.with_name('project-operations.lock')
    protected_parents(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid or info.st_mode & 0o077
                or info.st_nlink != 1):
            raise ValueError('Unsafe DevStack project operations lock')
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('A DevStack project operation is in progress') from None
        yield
        if not os.path.samestat(info, path.lstat()):
            raise RuntimeError('DevStack project operations lock changed')
    finally:
        os.close(fd)


def project_roots(config, registry):
    roots = {}
    fd = os.open('/', DIR_FLAGS)
    try:
        for slug, record in registry.items():
            spec = project_spec(config, registry, slug)
            path = Path(record['path'])
            try:
                with directory_chain(fd, path.parts[1:]) as project:
                    roots[slug] = (file_metadata(os.fstat(project)), mount_id(project))
            except FileNotFoundError:
                roots[slug] = None
    finally:
        os.close(fd)
    return roots


def unit_names(pattern, *, busy=False):
    scope = '--state=activating,active,reloading,deactivating' if busy else '--all'
    result = run([SYSTEMCTL, 'list-units', scope, '--plain', '--no-legend',
                  '--no-pager', '--full', pattern])
    if result.returncode:
        raise RuntimeError('Cannot read DevStack service inventory')
    return tuple(sorted(line.split()[0] for line in result.stdout.splitlines() if line.strip()))


def unit_states(names):
    result = run([SYSTEMCTL, 'show', '--all', '--no-pager',
                  '-p', 'Id,LoadState,ActiveState,MainPID,InvocationID,UnitFileState,'
                        'NeedDaemonReload,ExecMainStartTimestampMonotonic', '--', *names])
    units = {}
    for block in result.stdout.strip().split('\n\n'):
        fields = dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
        name = fields.get('Id')
        if (name not in names or name in units
                or fields.get('LoadState') not in ('loaded', 'not-found', 'masked')
                or fields.get('ActiveState') not in ('active', 'inactive', 'failed')):
            raise RuntimeError('Cannot confirm DevStack service state')
        if fields['ActiveState'] == 'active' and (
                fields['LoadState'] != 'loaded' or not fields.get('InvocationID')
                or fields['InvocationID'] == '0' * 32):
            raise RuntimeError('Cannot identify an active DevStack service invocation')
        units[name] = fields
    # Missing optional units may make systemctl return nonzero.
    if set(units) != set(names) or (result.returncode and all(
            fields['LoadState'] != 'not-found' for fields in units.values())):
        raise RuntimeError('Cannot confirm all DevStack services')
    return units


def account_metadata():
    # Ignore password fields. Existing UID/GID and supplementary membership
    # must survive the fixture's temporary account and group operations.
    return (
        {p.pw_name: (p.pw_uid, p.pw_gid, p.pw_gecos, p.pw_dir, p.pw_shell)
         for p in pwd.getpwall()},
        {g.gr_name: (g.gr_gid, tuple(sorted(g.gr_mem))) for g in grp.getgrall()},
    )


def snapshot():
    with registry_snapshot_lock():
        config, registry = read_control_json(CONFIG), read_control_json(REGISTRY)
        pilot = unit_names('devstack-pilot-*.service')
        if any(not PILOT_UNIT.fullmatch(name) for name in pilot):
            raise RuntimeError('Unexpected isolated runtime identity')
        if (unit_names('devstack-engine-*.service', busy=True)
                or unit_names('devstack-service-control.service', busy=True)):
            raise RuntimeError('A DevStack worker operation is in progress')
        states = unit_states((*SERVICES, *pilot))
        if any(states[name]['ActiveState'] != 'active' for name in CORE):
            raise RuntimeError('The DevStack manager, Nginx and PHP-FPM must be active')
        socket = Path('/run/devstack/control.sock')
        if not stat.S_ISSOCK(socket.lstat().st_mode):
            raise ValueError('DevStack manager socket is unavailable')
        runtimes = {}
        for name in pilot:
            directory = Path('/run') / name.removesuffix('.service')
            protected_parents(directory)
            if os.path.lexists(directory):
                protected_control(directory, directory=True)
            journal = REGISTRY.parent / ('pilot-' + name.split('-')[2]) / 'identity-p.json'
            runtimes[name] = (states.pop(name), path_metadata(journal),
                tuple(path_metadata(directory / relative)
                for relative in ('', 'sockets', 'sockets/php.sock', 'sockets/http.sock',
                                 'smtp', 'smtp/smtp.sock')))
        return {
            'projects': (path_metadata(REGISTRY), project_roots(config, registry)),
            'configuration': ({path: control_tree(path) for path in CONTROL_TREES},
                              {path: path_metadata(path) for path in CONTROL_FILES}),
            'services': states, 'isolated runtimes': runtimes, 'OS accounts': account_metadata(),
        }


@contextmanager
def protect_workstation():
    before = snapshot()
    print('PASS: DevStack state verified before creating fixtures', flush=True)
    try:
        yield
    finally:
        after = snapshot()
        changed = [name for name in before if before[name] != after[name]]
        if changed:
            raise RuntimeError('DevStack state changed during acceptance: ' + ', '.join(changed))
        print('PASS: existing DevStack projects, configuration, services, runtimes and accounts unchanged',
              flush=True)


def check_account_hooks():
    protected_control(Path('/etc/login.defs'), directory=False)
    private_groups = []
    for line in Path('/etc/login.defs').read_text().splitlines():
        fields = line.split('#', 1)[0].split()
        if fields[:1] == ['USERDEL_CMD']:
            raise ValueError('Custom user removal hook is configured')
        if fields[:1] == ['USERGROUPS_ENAB']:
            private_groups.append(fields[1:])
    if private_groups != [['yes']]:
        raise ValueError('Temporary private group removal is not configured')
    for name in ('useradd-pre.d', 'useradd-post.d', 'userdel-pre.d', 'userdel-post.d'):
        path = Path('/etc/shadow-maint') / name
        if os.path.lexists(path):
            protected_parents(path)
            protected_control(path, directory=True)
            if any(path.iterdir()):
                raise ValueError('Account-management hooks are configured')
