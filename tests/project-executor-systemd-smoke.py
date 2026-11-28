#!/usr/bin/env python3
"""Real systemd/IDMAP/Bubblewrap proof on two newly generated fixtures."""
from contextlib import nullcontext
import grp
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'tests'))
sys.path.insert(0, str(ROOT / 'daemon'))

from project_executor import MODULES, PHP_MODULES, Session
from project_executor_workstation import check_account_hooks, protect_workstation
from project_isolation_preflight import protected_control, protected_parents
from project_lifecycle import ENV, SYSTEMCTL, run, unit_state
from project_mounts import reject_nested_mounts
from project_runtime_identity import provision_executor_fixture, verify_binding
from project_pilot import require_backend

PYTHON = '/usr/bin/python3'
EXPECT = 'synthetic baseline\n'
ARTISAN = """<?php
require __DIR__.'/vendor/autoload.php';
$app = Illuminate\\Foundation\\Application::configure(basePath: __DIR__)->create();
exit($app->handleCommand(new Symfony\\Component\\Console\\Input\\ArgvInput));
"""
CREATE = r'''
import json
from pathlib import Path
import shutil
import sys

p = Path('.')
for directory in ('bootstrap/cache', 'storage/framework/cache', 'storage/framework/views',
                  'database', 'node-local', 'php-local'):
    (p / directory).mkdir(parents=True)
(p / 'source.txt').write_text(sys.argv[1])
(p / 'script.sh').write_text('#!/bin/sh\nprintf "interpreted\\n"\n')
shutil.copyfile('/usr/bin/true', p / 'native')
for name in ('native', 'script.sh'):
    (p / name).chmod(0o700)
(p / 'package.json').write_text(json.dumps({
    'name': 'executor-fixture', 'version': '1.0.0', 'private': True,
    'scripts': {'proof': 'node node-proof.js'}, 'dependencies': {'proof-local': 'file:node-local'},
}))
(p / 'node-local/package.json').write_text('{"name":"proof-local","version":"1.0.0","main":"index.js"}')
(p / 'node-local/index.js').write_text('module.exports = "node:local";')
(p / 'node-proof.js').write_text('console.log(require("proof-local"));')
(p / 'php-local/composer.json').write_text(json.dumps({
    'name': 'fixture/local', 'version': '1.0.0',
    'autoload': {'files': ['proof.php']},
}))
(p / 'php-local/proof.php').write_text('<?php function fixture_value() { return "composer:local"; }')
(p / 'composer.json').write_text(json.dumps({
    'name': 'fixture/controlled', 'require': {'fixture/local': '1.0.0'},
    'repositories': [{'type': 'path', 'url': 'php-local', 'options': {'symlink': False}},
                     {'packagist.org': False}],
}))
(p / 'composer-proof.php').write_text('<?php require "vendor/autoload.php"; echo fixture_value();')
(p / 'probe.php').write_text("""<?php
$db = new PDO('sqlite:database/test.sqlite');
$db->exec('CREATE TABLE IF NOT EXISTS fixtures (id INTEGER PRIMARY KEY)');
$db->exec('INSERT OR IGNORE INTO fixtures VALUES (1)');
echo 'sqlite:' . $db->query('SELECT count(*) FROM fixtures')->fetchColumn();
""")
'''


def command(*argv, **kwargs):
    result = subprocess.run(argv, check=False, capture_output=True, text=True,
                            timeout=45, env=ENV, **kwargs)
    if result.returncode:
        raise RuntimeError(f'{argv[0]} exited {result.returncode}: {result.stderr[-1200:]}')
    return result.stdout


def write_record(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')
    path.chmod(0o600)


def as_developer(identity, *argv, **kwargs):
    return command('/usr/bin/setpriv', '--reuid=' + str(identity['uid']),
                   '--regid=' + str(identity['gid']), '--clear-groups',
                   '--no-new-privs', '--', '/usr/bin/env', '-i',
                   'PATH=/usr/bin:/bin', 'HOME=/nonexistent', 'COMPOSER_DISABLE_NETWORK=1',
                   *argv, **kwargs)


def make_project(base, allocate, create_owned_dir):
    key = secrets.token_hex(16)
    developer = allocate(base / ('developer-' + key), 'd', key)
    project = base / ('project-' + key)
    create_owned_dir(project, developer)
    as_developer(developer, PYTHON, '-I', '-c', CREATE, EXPECT, cwd=project)
    info = project.stat()
    write_record(base / ('registration-' + key + '.json'),
                 {'schema': 1, 'project_id': key, 'source': [info.st_dev, info.st_ino]})

    # A root-owned standalone baseline, assembled without reading or running
    # anything from either fixture. No Git metadata in the work tree.
    git = base / ('git-' + key)
    git.mkdir(mode=0o755)
    git.chmod(0o755)
    git_cmd = ['/usr/bin/env', 'GIT_CONFIG_NOSYSTEM=1', 'GIT_CONFIG_GLOBAL=/dev/null',
               '/usr/bin/git', '-C', str(git), '--git-dir=' + str(git)]
    command(*git_cmd, 'init', '--bare', '--template=')
    command(*git_cmd, 'config', 'core.hooksPath', '/dev/null')
    command(*git_cmd, 'config', 'core.fsmonitor', 'false')
    blob = command(*git_cmd, 'hash-object', '-w', '--stdin', input=EXPECT).strip()
    command(*git_cmd, 'update-index', '--add', '--cacheinfo', '100644,' + blob + ',source.txt')
    tree = command(*git_cmd, 'write-tree').strip()
    commit = command(*git_cmd, '-c', 'user.name=DevStack fixture', '-c',
                     'user.email=fixture@example.invalid', 'commit-tree', tree,
                     '-m', 'Synthetic baseline').strip()
    command(*git_cmd, 'update-ref', 'HEAD', commit)
    git.chmod(0o755)
    for directory, subdirs, filenames in os.walk(git):
        for name in subdirs:
            (Path(directory) / name).chmod(0o755)
        for name in filenames:
            (Path(directory) / name).chmod(0o644)
    return key, project, developer


def prepare_tools(base, fixture, create_owned_dir):
    _, project, developer = fixture
    scratch = base / 'dependency-prep'
    create_owned_dir(scratch, developer)
    npmrc = base / 'empty-npmrc'
    npmrc.touch(exist_ok=False)
    npmrc.chmod(0o644)
    as_developer(developer, '/usr/bin/env', f'npm_config_cache={scratch}/npm',
                 '/usr/bin/npm', 'install', '--offline', '--ignore-scripts',
                 '--no-audit', '--no-fund', '--install-links',
                 '--userconfig=/dev/null', f'--globalconfig={npmrc}', cwd=project)
    as_developer(developer, '/usr/bin/env', f'COMPOSER_HOME={scratch}/composer',
                 '/usr/bin/composer', '--no-plugins', '--no-scripts', '--no-interaction',
                 'install', cwd=project)


def add_artisan(fixture, archive):
    _, project, developer = fixture
    # A separate fixture for the actual Laravel vendor tree. Extract as the
    # synthetic owner; the archive is an input, never a mount source.
    with archive.open('rb') as stream:
        as_developer(developer, '/usr/bin/tar', '--no-same-owner', '--no-same-permissions',
                     '-xf', '-', '--', 'vendor', cwd=project, stdin=stream)
    # Exercise Laravel's CLI in the synthetic application. E2.0 has
    # already exercised the original starter kit's optional providers.
    as_developer(developer, PYTHON, '-I', '-c', '''
import json, pathlib, sys
pathlib.Path("artisan").write_text(sys.argv[1])
manifest = pathlib.Path("composer.json")
config = json.loads(manifest.read_text())
config["extra"] = {"laravel": {"dont-discover": ["*"]}}
manifest.write_text(json.dumps(config))
''', ARTISAN, cwd=project)


def wait_for(predicate, label, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.05)
    raise RuntimeError('Timed out waiting for ' + label)


def proc_identity(pid):
    try:
        comm, data = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)
        return comm, data.split()[19]
    except FileNotFoundError:
        return None


def verify_unused_accounts(accounts):
    if not accounts:
        return
    uids = {identity['uid'] for identity in accounts}
    gids = {identity['gid'] for identity in accounts}
    expected = {identity['account'] for identity in accounts}
    if ({p.pw_name for p in pwd.getpwall() if p.pw_uid in uids or p.pw_gid in gids} != expected
            or {g.gr_name for g in grp.getgrall() if g.gr_gid in gids} != expected
            or any(grp.getgrnam(name).gr_mem for name in expected)):
        raise RuntimeError('Temporary accounts or their groups are shared')
    for process in Path('/proc').iterdir():
        if not process.name.isdecimal():
            continue
        try:
            lines = (process / 'status').read_text(errors='replace').splitlines()
        except (FileNotFoundError, ProcessLookupError):
            continue
        for line in lines:
            key, _, value = line.partition(':')
            if key in ('Uid', 'Gid', 'Groups') and (
                    set(map(int, value.split())) & (uids if key == 'Uid' else gids)):
                raise RuntimeError('A process retains a temporary credential')


def cgroup_children(session):
    assert unit_state(session) == 'active'
    group = command('/usr/bin/systemctl', 'show', '-p', 'ControlGroup', '--value',
                    '--', session.unit).strip()
    assert group and group.endswith('/' + session.unit)
    procs = Path('/sys/fs/cgroup' + group) / 'cgroup.procs'
    pids = {int(pid): proc_identity(pid) for pid in procs.read_text().splitlines()}
    assert any(info and info[0].endswith('(sleep') for info in pids.values()), pids
    return procs, pids


def assert_clean(session, processes=None):
    if unit_state(session) != 'absent':
        args = [PYTHON, '-I', str(session.base / 'launcher/project_executor.py'),
                str(session.base), session.session_id]
        expected_command = '{ path=' + PYTHON + ' ; argv[]=' + ' '.join(args) + ' ; '
        result = run([SYSTEMCTL, 'show', '--value', '-p', 'ExecStart', '--', session.unit])
        if unit_state(session) != 'absent' and (
                result.returncode or not result.stdout.startswith(expected_command)):
            raise RuntimeError('Refusing cleanup of a different systemd command')
    session.stop()
    assert '/run/' + session.runtime_directory not in Path('/proc/self/mountinfo').read_text()
    group = Path('/sys/fs/cgroup/system.slice') / session.unit / 'cgroup.events'
    def empty():
        try:
            return 'populated 0' in group.read_text().splitlines()
        except FileNotFoundError:
            return True

    wait_for(empty, 'empty executor cgroup')
    if processes:
        group, pids = processes
        wait_for(lambda: not group.exists() or not group.read_text().strip(), 'empty cgroup')
        assert all(info is None or proc_identity(pid) != info for pid, info in pids.items())


def run_suite(base, archive, *, ci, check_base, guard_installed):
    accounts = []
    sessions = []
    clients = []
    owned_dirs = {}

    def allocate(path, slot, key):
        check_base()
        path.mkdir(mode=0o700)
        record = provision_executor_fixture(path, slot, key)
        accounts.append((path, slot, record))
        return record

    def create_owned_dir(path, owner):
        check_base()
        path.mkdir(mode=0o700)
        os.chown(path, owner['uid'], owner['gid'], follow_symlinks=False)
        owned_dirs[path] = path.lstat()

    def start(fixture, sibling, mode, *, agent='fixture-agent-a',
              profile='read', runtime=90, missing=None):
        key, _, _ = fixture
        other_path = sibling[1]
        session = Session(base, secrets.token_hex(16))
        identity = allocate(session.record, 'e', session.session_id)
        request = {'schema': 1, 'session_id': session.session_id, 'project_id': missing or key,
                   'agent_id': agent,
                   'profile': profile,
                   'argv': [PYTHON, '-I', '-S', '/run/devstack-proof.py', mode, str(base / 'host-home'),
                            str(other_path), str(base / 'admin.sock'), EXPECT]}
        write_record(session.record / 'launch.json', request)
        output = session.record / 'output'
        argv = session.service_argv(runtime_seconds=runtime)
        argv[1:1] = ['--setenv=DEVSTACK_TEST_SECRET=host-only',
                     '--property=OpenFile=' + str(base / 'canary') + ':fixture:read-only']
        sessions.append(session)
        clients.append((session, subprocess.Popen(
            argv, env=ENV, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)))
        return session, identity, output, clients[-1][1]

    def finish(job, expected=0, failure=None):
        session, _, output, process = job
        logs, _ = process.communicate(timeout=110)
        proof = output.read_text() if output.exists() else ''
        if expected is not None:
            assert (process.returncode == 0) == (expected == 0), (logs, proof)
        if failure:
            assert failure in proof, (logs, proof)
        assert_clean(session)
        return proof

    def proof(job):
        _, identity, output, _ = job
        observation = json.loads(output.read_text().splitlines()[0])
        assert (observation['uid'], observation['gid']) == (identity['uid'], identity['gid'])
        for name, identifier in observation['namespaces'].items():
            assert identifier != os.readlink('/proc/self/ns/' + name)
        return observation

    def ready(job):
        def child_ready():
            if job[3].poll() is not None:
                finish(job)
                raise RuntimeError('Executor exited before creating its child')
            return job[2].exists() and 'child-ready' in job[2].read_text()

        wait_for(child_ready, 'sandbox child')
        return cgroup_children(job[0])

    try:
        a = make_project(base, allocate, create_owned_dir)
        b = make_project(base, allocate, create_owned_dir)
        prepare_tools(base, a, create_owned_dir)
        if archive:
            # Use the second fixture for Laravel so the standalone local
            # Composer package in the first fixture remains independent.
            add_artisan(b, archive)
        with socket.socket(socket.AF_UNIX) as host_socket, socket.socket(socket.AF_UNIX) as abstract:
            address = str(base / 'admin.sock')
            host_socket.bind(address)
            host_socket.listen()
            abstract.bind('\0' + address)
            abstract.listen()
            (base / 'canary').write_text('host-only descriptor')
            (base / 'host-home').mkdir(mode=0o700)
            (base / 'host-home/credentials').write_text('synthetic private home')

            holder = start(a, b, 'hold')
            held_processes = ready(holder)
            second = start(a, b, 'hold', agent='fixture-agent-b')
            second_processes = ready(second)
            third = start(b, a, 'hold', agent='fixture-agent-b')
            third_processes = ready(third)
            observations = [proof(job) for job in (holder, second, third)]
            assert len({(p['uid'], p['gid']) for p in observations}) == 3
            for name in observations[0]['namespaces']:
                assert len({p['namespaces'][name] for p in observations}) == 3
            assert_clean(holder[0], held_processes)
            assert 'PASS: hold' not in finish(holder, expected=None)
            assert unit_state(second[0]) == unit_state(third[0]) == 'active'
            # An administrator replaces the source name while a session is
            # active. A new launch must refuse it; the pinned view keeps
            # seeing the contents of the original inode until exit.
            _, project, developer = a
            moved = project.with_name('moved-project')
            project.rename(moved)
            project.mkdir(mode=0o700)
            try:
                finish(start(a, b, 'read'), 1, 'mount source changed')
                as_developer(developer, '/usr/bin/touch', 'finish', cwd=moved)
                assert 'PASS: hold' in finish(second)
                assert_clean(second[0], second_processes)
            finally:
                project.rmdir()
                moved.rename(project)
                (project / 'finish').unlink(missing_ok=True)
            assert unit_state(third[0]) == 'active'
            assert_clean(third[0], third_processes)
            assert 'PASS: hold' not in finish(third, expected=None)
            print('PASS: concurrent sessions, source pinning during replacement and independent stop', flush=True)

            readonly = start(a, b, 'read')
            assert 'PASS: read' in finish(readonly)
            proof(readonly)
            writable = start(a, b, 'write', profile='write-cli')
            assert 'PASS: write' in finish(writable)
            proof(writable)
            created = project / 'created'
            info = created.stat()
            assert (info.st_uid, info.st_gid) == (developer['uid'], developer['gid'])
            assert (project / 'database/test.sqlite').stat().st_uid == developer['uid']
            assert as_developer(developer, '/usr/bin/cat', 'source.txt', cwd=project) == 'sandbox edit\n'
            as_developer(developer, PYTHON, '-I', '-c',
                         'from pathlib import Path; Path("created").write_text("owner edit")', cwd=project)
            assert created.read_text() == 'owner edit'
            as_developer(developer, PYTHON, '-I', '-c',
                         'from pathlib import Path; import sys; Path("source.txt").write_text(sys.argv[1])',
                         EXPECT, cwd=project)
            assert (b[1] / 'source.txt').read_text() == EXPECT
            assert 'PASS: read' in finish(start(b, a, 'read'))
            wrote_b = start(b, a, 'write-second', profile='write-cli')
            output_b = finish(wrote_b)
            assert 'PASS: write-second' in output_b
            proof(wrote_b)
            assert ('PASS: actual Laravel Artisan' in output_b) == bool(archive)
            owner_b = b[2]
            created_b = (b[1] / 'created').stat()
            assert (created_b.st_uid, created_b.st_gid) == (owner_b['uid'], owner_b['gid'])
            assert as_developer(owner_b, '/usr/bin/cat', 'created', cwd=b[1]) == 'sandbox data\n'
            print('PASS: RO/RW, tool subset, physical owner and developer edits', flush=True)

            crash = start(a, b, 'crash')
            crashed_processes = ready(crash)
            as_developer(developer, '/usr/bin/touch', 'finish', cwd=project)
            assert 'child-ready' in finish(crash, expected=1)
            assert_clean(crash[0], crashed_processes)
            (project / 'finish').unlink()
            timeout = start(a, b, 'hold', runtime=8)
            timed_processes = ready(timeout)
            finish(timeout, expected=1)
            assert_clean(timeout[0], timed_processes)
            print('PASS: crash and RuntimeMaxSec remove descendant processes and mounts', flush=True)

            finish(start(a, b, 'read', missing=secrets.token_hex(16)), 1, 'No such file')
            registration = base / ('registration-' + a[0] + '.json')
            data = json.loads(registration.read_text())
            write_record(registration, {**data, 'project_id': b[0]})
            try:
                finish(start(a, b, 'read'), 1, 'Invalid synthetic project registration')
            finally:
                write_record(registration, data)
            identity = base / ('developer-' + a[0]) / 'identity-d.json'
            binding = identity.read_text()
            try:
                identity.write_text(json.dumps({**json.loads(binding), 'uid': 1}))
                finish(start(a, b, 'read'), 1, 'account or group changed')
            finally:
                identity.write_text(binding)
            for name, create in (
                ('absolute-link', lambda path: path.symlink_to(b[1] / 'source.txt')),
                ('hardlink', lambda path: os.link(project / 'source.txt', path)),
                ('.git', lambda path: path.write_text('gitdir: ' + str(b[1]))),
                ('foreign-owner', lambda path: path.write_text('root-owned')),
            ):
                path = project / name
                create(path)
                try:
                    finish(start(a, b, 'read'), 1, 'Unsafe fixture tree metadata')
                finally:
                    path.unlink()
            if ci:
                nested = project / 'nested'
                as_developer(developer, '/usr/bin/mkdir', 'nested', cwd=project)
                command('/usr/bin/mount', '-n', '--bind', str(b[1]), str(nested))
                try:
                    finish(start(a, b, 'read'), 1, 'Nested project mount')
                finally:
                    command('/usr/bin/umount', '-n', str(nested))
                    nested.rmdir()
                print('PASS: nested fixture mount refused', flush=True)
            # A system unit without PrivateMounts fails before creating a view.
            unsafe = Session(base, secrets.token_hex(16))
            allocate(unsafe.record, 'e', unsafe.session_id)
            write_record(unsafe.record / 'launch.json',
                         {**readonly[0].request(), 'session_id': unsafe.session_id})
            sessions.append(unsafe)
            argv = unsafe.service_argv()
            argv.remove('--property=PrivateMounts=yes')
            result = subprocess.run(argv, env=ENV, capture_output=True, timeout=20, check=False)
            assert result.returncode and 'PrivateMounts is required' in (
                unsafe.record / 'output').read_text()
            assert_clean(unsafe)
            print('PASS: missing registration, identity drift, replaced source, links, Git and incomplete setup refused',
                  flush=True)
    finally:
        check_base()
        errors = []
        for session in sessions:
            try:
                assert_clean(session)
            except (AssertionError, OSError, ValueError, RuntimeError,
                    subprocess.SubprocessError) as exc:
                errors.append(exc)
        if errors:
            raise RuntimeError('Executor cleanup could not be confirmed') from errors[0]
        for _, client in clients:
            client.communicate(timeout=20)
        identities = []
        for path, slot, allocated in accounts:
            identity = verify_binding(path, slot)
            if identity != allocated:
                raise RuntimeError('Temporary account binding changed')
            identities.append(identity)
        verify_unused_accounts(identities)
        reject_nested_mounts(base)
        # Remove the synthetic developer's files before releasing its UID.
        for directory, created in owned_dirs.items():
            current = directory.lstat()
            if (not os.path.samestat(created, current) or not stat.S_ISDIR(current.st_mode)
                    or (current.st_uid, current.st_gid) != (created.st_uid, created.st_gid)):
                raise ValueError('Temporary source or scratch directory was replaced')
        for directory in owned_dirs:
            shutil.rmtree(directory)
        if guard_installed:
            check_account_hooks()
        for path, slot, identity in reversed(accounts):
            if verify_binding(path, slot) != identity:
                raise RuntimeError('Temporary account binding changed before removal')
            command('/usr/sbin/userdel', '--', identity['account'])
            # Never adopt or delete a group left behind by system account tools.
            for lookup in (pwd.getpwnam, grp.getgrnam):
                try:
                    lookup(identity['account'])
                except KeyError:
                    pass
                else:
                    raise RuntimeError('Temporary account or group was not removed')


def check_local(kernel):
    release = Path('/etc/os-release').read_text()
    if ('microsoft' not in kernel or 'wsl2' not in kernel
            or not re.search(r'^ID="?ubuntu"?$', release, re.MULTILINE)
            or not re.search(r'^VERSION_ID="?26\.04"?$', release, re.MULTILINE)
            or command('/usr/bin/findmnt', '-n', '-o', 'FSTYPE', '-T', '/var/lib').strip() != 'ext4'):
        raise ValueError('Use Ubuntu 26.04 WSL2 with an ext4 fixture filesystem')
    for path in map(Path, ('/usr/bin/git', '/usr/bin/php8.5', '/usr/bin/node',
                           '/usr/bin/npm', '/usr/bin/composer')):
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ValueError('Missing installed fixture tool: ' + path.name)
    for name in PHP_MODULES:
        if not Path('/etc/php/8.5/mods-available', name + '.ini').is_file():
            raise ValueError('Missing installed PHP module: ' + name)
    for path in map(Path, ('/usr/share/git-core', '/usr/share/nodejs',
                           '/usr/share/php', '/usr/share/zoneinfo')):
        if not path.is_dir():
            raise ValueError('Missing installed runtime directory: ' + path.name)


def run_fixture(archive, *, ci, guard_installed):
    protected_parents(Path('/var/lib/fixture'))
    if not shutil.rmtree.avoids_symlink_attacks:
        raise RuntimeError('Descriptor-based fixture cleanup is unavailable')
    base = None

    def check_base():
        protected_parents(base)
        protected_control(base, directory=True)
        current = base.lstat()
        if not os.path.samestat(created, current) or current.st_mode != created.st_mode:
            raise RuntimeError('Temporary fixture directory changed')

    try:
        with protect_workstation() if guard_installed else nullcontext():
            base = Path(tempfile.mkdtemp(prefix='devstack-executor-probe-', dir='/var/lib'))
            base.chmod(0o711)
            created = base.lstat()
            launcher = base / 'launcher'
            launcher.mkdir(mode=0o700)
            for name in MODULES:
                shutil.copyfile(ROOT / 'daemon' / name, launcher / name)
            proof = launcher / 'devstack-proof.py'
            shutil.copyfile(ROOT / 'tests/project-executor-payload.py', proof)
            proof.chmod(0o644)
            run_suite(base, archive, ci=ci, check_base=check_base, guard_installed=guard_installed)
        check_base()
        reject_nested_mounts(base)
        shutil.rmtree(base)
    except BaseException:
        # Retain the protected records for root-only investigation if a unit
        # or account could not be confirmed and removed.
        if base is not None:
            print('Synthetic fixture diagnostics:', base, file=sys.stderr, flush=True)
            if ci:
                check_base()
                for output in sorted(base.glob('session-*/output')):
                    print(output.parent.name, output.read_text()[-2500:], file=sys.stderr, flush=True)
        raise


def interrupt(signum, frame):
    raise KeyboardInterrupt(f'Test interrupted by signal {signum}')


def main():
    if (not sys.flags.isolated or sys.flags.optimize or os.geteuid() != 0
            or sys.argv[1:2] not in (['--ci'], ['--local'])
            or Path('/proc/1/comm').read_text().strip() != 'systemd'
            or not os.path.samestat(os.stat('/proc/self/ns/mnt'), os.stat('/proc/1/ns/mnt'))):
        raise SystemExit('Use sudo python3 -I from the systemd host with --local or --ci')
    kernel = Path('/proc/sys/kernel/osrelease').read_text().lower()
    ci = sys.argv[1] == '--ci'
    archive = None
    if ci:
        if ('microsoft' in kernel or os.getenv('GITHUB_ACTIONS') != 'true'
                or os.getenv('RUNNER_ENVIRONMENT') != 'github-hosted'):
            raise SystemExit('Expected a disposable hosted CI runner')
        if len(sys.argv) == 4 and sys.argv[2] == '--laravel-vendor':
            archive = Path(sys.argv[3]).absolute()
        elif len(sys.argv) != 2:
            raise SystemExit('Expected --ci [--laravel-vendor ARCHIVE]')
    else:
        if len(sys.argv) != 2:
            raise SystemExit('--local takes no additional arguments')
        check_local(kernel)
    require_backend()
    os.umask(0o077)
    # Avoid the administrator's working directory and Git configuration.
    os.chdir('/')
    installed = Path('/opt/devstack/daemon/devstackd.py').is_file()
    guard_installed = not ci or installed
    if guard_installed:
        check_account_hooks()
    signal.signal(signal.SIGTERM, interrupt)
    run_fixture(archive, ci=ci, guard_installed=guard_installed)


if __name__ == '__main__':
    main()
