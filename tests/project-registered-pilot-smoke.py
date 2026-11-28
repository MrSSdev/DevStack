#!/usr/bin/env python3
"""Exercise the installed preview entry point on a disposable hosted runner.

The ordinary registry/layout is real; this stages the protected modules and
creates a small PHP/SQLite application as a regular developer. It is not a
full installation or a substitute for the target Laravel/WSL acceptance.
"""
import json
import os
from pathlib import Path
import pwd
import secrets
import shutil
import stat
import subprocess
import sys

if (os.geteuid() != 0 or not sys.flags.isolated
        or os.getenv('GITHUB_ACTIONS') != 'true'
        or 'microsoft' in Path('/proc/sys/kernel/osrelease').read_text().lower()):
    raise SystemExit('Disposable hosted runner only')

ROOT = Path(__file__).resolve().parents[1]
MODULES = ('project_pilot.py', 'project_isolation_preflight.py', 'project_runtime_identity.py',
           'project_idmap.py', 'project_mounts.py', 'project_access.py', 'project_data_views.py',
           'project_runtime.py', 'project_http.py', 'project_lifecycle.py')
INSTALLED = Path('/opt/devstack/daemon/project_pilot.py')
CONFIG = Path('/etc/devstack')
STATE = Path('/var/lib/devstack')
SLUG = 'isolation-test'
KEY = secrets.token_hex(16)
USER = 'dspilot-' + secrets.token_hex(5)
HOME = Path('/home') / USER
PROJECT = HOME / 'projects' / SLUG
RUNDIRS = [Path('/run') / ('devstack-pilot-' + KEY + '-' + kind)
           for kind in ('php', 'http')]
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C'}


def run(*args, **kwargs):
    result = subprocess.run(args, check=False, text=True, timeout=90, env=ENV,
                            capture_output=True, **kwargs)
    if result.returncode:
        # This is a disposable synthetic project; its output contains no
        # workstation credentials. Keep the failing command's diagnostics.
        print('Fixture command failed:', args, 'exit=', result.returncode, flush=True)
        print(result.stderr[-4000:], file=sys.stderr, flush=True)
        print(result.stdout[-2000:], flush=True)
        result.check_returncode()
    return result.stdout


def as_user(user, *args, **kwargs):
    return run('/usr/sbin/runuser', '-u', user, '--', *args, **kwargs)


def command(action, value=SLUG):
    return json.loads(run('/usr/bin/python3', '-I', str(INSTALLED), action, value))


def request(path='/'):
    return as_user('www-data', '/usr/bin/curl', '--fail', '--silent', '--show-error',
                   '--max-time', '10', '--noproxy', '*',
                   '--unix-socket', str(RUNDIRS[1] / 'sockets/http.sock'),
                   '-H', 'Host: isolation-test.test', '-H', 'X-Forwarded-Proto: https',
                   '-H', 'X-Forwarded-Port: 443', '-H', 'X-Forwarded-For: 127.0.0.1',
                   'http://isolation-test.test' + path)


PHP = r'''<?php
$root = dirname(__DIR__);
$db = new PDO('sqlite:' . $root . '/database/runtime.sqlite');
$db->exec('CREATE TABLE IF NOT EXISTS proof (id INTEGER PRIMARY KEY)');
$db->exec('INSERT OR IGNORE INTO proof VALUES (1)');
$private = $root . '/storage/owner-only';
if (!file_exists($private)) {
    file_put_contents($private, 'runtime');
    chmod($private, 0600);
}
file_put_contents($root . '/storage/app/public/upload.txt', 'runtime-upload');
file_put_contents($root . '/bootstrap/cache/proof', 'cache');
echo json_encode([
    'uid' => posix_geteuid(),
    'rows' => array_map('intval', $db->query('SELECT id FROM proof ORDER BY id')->fetchAll(PDO::FETCH_COLUMN)),
    'private' => file_get_contents($private),
    'code_readonly' => @file_put_contents($root . '/code.txt', 'unexpected') === false,
    'sibling_denied' => @file_put_contents(dirname($root) . '/other-test/marker', 'unexpected') === false,
    'host_hidden' => !file_exists('/mnt/c') && !file_exists('/run/devstack'),
    'https' => $_SERVER['HTTPS'] ?? '',
]);
'''


def no_host_views():
    text = Path('/proc/self/mountinfo').read_text()
    assert all(str(path) + '/' not in text for path in RUNDIRS), 'Pilot mounts leaked to host'


def stopped():
    no_host_views()
    assert all(not path.exists() for path in RUNDIRS), 'RuntimeDirectory not removed'


def probe_mounts(key):
    # This disposable CI test calls the actual installed preparation
    # function. No application or unreviewed Bubblewrap is executed.
    sys.path.insert(0, str(INSTALLED.parent))
    from project_pilot import check_installation, prepare_runtime, registered
    check_installation()
    _, _, developer = registered(None, project_id=key)
    import re
    if not re.fullmatch(r'dspilot-[0-9a-f]{10}', developer.pw_name):
        raise AssertionError('Expected disposable pilot developer')
    for line in Path('/proc/self/status').read_text().splitlines():
        if line.startswith(('CapPrm:', 'CapEff:', 'CapBnd:', 'NoNewPrivs:', 'Seccomp:')):
            print('Preparation policy:', line, flush=True)
    label = Path('/proc/self/attr/current')
    if label.exists():
        print('Preparation LSM:', label.read_text().strip(), flush=True)
    with prepare_runtime(key, 'php') as (args, sources, mapping):
        info = os.fstat(sources.root)
        assert (info.st_uid, info.st_gid) == mapping.runtime
        assert os.fstatvfs(sources.root).f_flag & os.ST_RDONLY
        assert set(sources.writable) == {'storage', 'bootstrap/cache', 'database'}
        for fd in sources.writable.values():
            info = os.fstat(fd)
            assert (info.st_uid, info.st_gid) == mapping.runtime
            assert not os.fstatvfs(fd).f_flag & os.ST_RDONLY
        assert args[0] == '/usr/bin/bwrap'
    print('PASS: Actual service-private PHP mount preparation', flush=True)


def preparation_only():
    sys.path.insert(0, str(INSTALLED.parent))
    from project_pilot import journal
    from project_runtime_identity import provision_pilot
    from project_lifecycle import RUNTIME_SECURITY_PROPERTIES
    base = journal(KEY)
    base.mkdir(mode=0o700)
    provision_pilot(base, KEY)
    directory = RUNDIRS[0]
    unit = 'devstack-mount-only-' + KEY + '-php'
    try:
        output = run('/usr/bin/systemd-run', '--quiet', '--wait', '--pipe',
                     '--collect', '--service-type=exec', '--uid=root',
                     '--unit=' + unit, '--property=PrivateMounts=yes',
                     '--property=RuntimeDirectory=' + directory.name,
                     '--property=RuntimeDirectoryMode=0711',
                     '--property=UMask=0007', '--property=KillMode=control-group',
                     '--property=RuntimeMaxSec=45s', *RUNTIME_SECURITY_PROPERTIES,
                     '--setenv=GITHUB_ACTIONS=true',
                     '/usr/bin/python3', '-I', str(Path(__file__).absolute()),
                     '--probe-mounts', KEY)
        print(output, end='', flush=True)
    except BaseException:
        # Fixed disposable runner: retain relevant LSM diagnostics when
        # available. No host configuration or policy changes.
        result = subprocess.run(['/usr/bin/journalctl', '-k', '--since=-2min',
                                 '--no-pager'], text=True, capture_output=True,
                                timeout=15, check=False)
        for line in result.stdout.splitlines():
            if 'apparmor=' in line and ('DENIED' in line or 'userns' in line):
                print(line[-1500:], flush=True)
        raise
    finally:
        subprocess.run(['/usr/bin/systemctl', 'stop', unit + '.service'],
                       timeout=15, check=False, capture_output=True)
    stopped()
    print('PASS: Preparation-only service namespace removed; no Bubblewrap execution', flush=True)


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--probe-mounts':
        probe_mounts(sys.argv[2])
        return
    if sys.argv[1:] not in ([], ['--mount-only']):
        raise ValueError('Unexpected fixture invocation')
    roots = (INSTALLED.parent.parent, CONFIG, STATE, HOME)
    if any(os.path.lexists(path) for path in roots + tuple(RUNDIRS)):
        raise AssertionError('Refusing to overwrite an existing installation or account')
    opt_mode = stat.S_IMODE(Path('/opt').stat().st_mode)
    Path('/opt').chmod(opt_mode & ~0o022)  # hosted runner setup only
    try:
        run('/usr/sbin/useradd', '--create-home', '--user-group', '--shell', '/bin/bash', USER)
        developer = pwd.getpwnam(USER)
        # Match the actual installer, rather than inheriting the runner's
        # umask or a default ACL on /opt for newly staged control directories.
        for directory in (INSTALLED.parent.parent, INSTALLED.parent):
            directory.mkdir(mode=0o755)
            directory.chmod(0o755)
        for directory in (*reversed(INSTALLED.parents),):
            info = directory.lstat()
            print('Pilot control directory:', directory, 'uid=', info.st_uid,
                  'mode=', oct(stat.S_IMODE(info.st_mode)), flush=True)
        for name in MODULES:
            target = INSTALLED.parent / name
            shutil.copyfile(ROOT / 'daemon' / name, target)
            target.chmod(0o644)
        CONFIG.mkdir(mode=0o700)
        STATE.mkdir(mode=0o700)
        (CONFIG / 'server.json').write_text(json.dumps({
            'dev_user': USER, 'projects_dir': str(HOME / 'projects')}))
        registry = {SLUG: {'project_id': KEY, 'name': SLUG, 'path': str(PROJECT),
                           'domain': SLUG + '.test', 'webroot': 'public',
                           'https': True, 'database': False, 'local_mail': True,
                           'overrides': '', 'extra_locations': ''}}
        registry_file = STATE / 'projects.json'
        registry_file.write_text(json.dumps(registry))
        registry_file.chmod(0o600)
        (CONFIG / 'server.json').chmod(0o600)
        before = registry_file.read_bytes()
        as_user(USER, '/usr/bin/python3', '-I', '-c', '''
from pathlib import Path
import os, sys
os.umask(0o077)
p = Path(sys.argv[1])
for directory in ('public', 'storage/app/public', 'bootstrap/cache', 'database'):
    (p / directory).mkdir(parents=True)
(p / 'public/index.php').write_text(sys.stdin.read())
(p / 'code.txt').write_text('developer')
other = p.parent / 'other-test'
other.mkdir()
(other / 'marker').write_text('sibling')
''', str(PROJECT), input=PHP)
        # Reproduce the existing DevStack public ACL, including its default.
        as_user(USER, '/usr/bin/setfacl', '-m', 'u:www-data:--x', str(HOME),
                str(PROJECT.parent), str(PROJECT))
        as_user(USER, '/usr/bin/setfacl', '-R', '-m', 'u:www-data:rX', str(PROJECT / 'public'))
        as_user(USER, '/usr/bin/setfacl', '-d', '-m', 'u:www-data:rX', str(PROJECT / 'public'))
        acl = run('/usr/bin/getfacl', '-p', str(PROJECT), str(PROJECT / 'public'),
                  str(PROJECT / 'storage'), str(PROJECT / 'database'))
        assert command('status')['status'] == 'not-enrolled'
        if sys.argv[1:] == ['--mount-only']:
            preparation_only()
            assert registry_file.read_bytes() == before
            return
        result = command('start')
        assert result['status'] == 'running' and result['project_id'] == KEY, result
        assert result['local_mail_setting'] and not result['smtp_in_preview']
        assert result['public_domain_unchanged'] and not result['agent_access']
        assert command('start')['status'] == 'running'
        identity = json.loads((STATE / ('pilot-' + KEY) / 'identity-p.json').read_text())
        response = json.loads(request())
        assert response == {'uid': identity['uid'], 'rows': [1], 'private': 'runtime',
                            'code_readonly': True, 'sibling_denied': True,
                            'host_hidden': True, 'https': 'on'}, response
        assert request('/storage/upload.txt') == 'runtime-upload'
        no_host_views()
        for relative in ('database/runtime.sqlite', 'storage/owner-only',
                         'storage/app/public/upload.txt', 'bootstrap/cache/proof'):
            info = (PROJECT / relative).stat()
            assert (info.st_uid, info.st_gid) == (developer.pw_uid, developer.pw_gid), relative
        assert stat.S_IMODE((PROJECT / 'storage/owner-only').stat().st_mode) == 0o600
        as_user(USER, '/usr/bin/python3', '-I', '-c', '''
from pathlib import Path
import sqlite3, sys
p = Path(sys.argv[1])
with sqlite3.connect(p / 'database/runtime.sqlite') as db:
    db.execute('INSERT INTO proof VALUES (2)')
with (p / 'storage/owner-only').open('a') as f:
    f.write('-developer')
''', str(PROJECT))
        response = json.loads(request())
        assert response['rows'] == [1,2] and response['private'] == 'runtime-developer', response
        assert (PROJECT / 'code.txt').read_text() == 'developer'
        assert (PROJECT.parent / 'other-test/marker').read_text() == 'sibling'
        assert registry_file.read_bytes() == before
        assert run('/usr/bin/getfacl', '-p', str(PROJECT), str(PROJECT / 'public'),
                   str(PROJECT / 'storage'), str(PROJECT / 'database')) == acl
        print('PASS: Installed registered preview, real FPM/HTTP, developer-owned data, unchanged ACL and registry', flush=True)
        assert command('stop')['status'] == 'stopped'
        stopped()
        assert command('start')['status'] == 'running'
        response = json.loads(request())
        assert response['rows'] == [1,2] and response['private'] == 'runtime-developer'
        assert response['uid'] == identity['uid']
        # Stop remains possible after a registration is removed. No grant or
        # permanent production routing has been installed by this preview.
        registry_file.write_text('{}')
        assert command('stop-id', KEY)['status'] == 'stopped'
        stopped()
        assert (PROJECT / 'storage/owner-only').read_text() == 'runtime-developer'
        print('PASS: Private service mounts removed, restart preserves data, stop by registration ID', flush=True)
    except BaseException:
        for directory in RUNDIRS:
            subprocess.run(['/usr/bin/journalctl', '-u', directory.name + '.service',
                            '--no-pager', '-n', '60'], check=False, timeout=15)
        raise
    finally:
        record = STATE / ('pilot-' + KEY) / 'identity-p.json'
        if record.is_file() and json.loads(record.read_text()).get('state') == 'ready':
            command('stop-id', KEY)
            stopped()
        # The runner is disposable. Do not recursively remove any developer
        # directory here, especially on an unsuccessful mount cleanup.
        Path('/opt').chmod(opt_mode)


if __name__ == '__main__':
    main()
