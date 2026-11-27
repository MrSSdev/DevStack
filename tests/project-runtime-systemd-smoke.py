#!/usr/bin/env python3
"""Real two-project FPM prototype on a disposable GitHub-hosted Linux runner.

Creates unique system accounts and protected fixture directories; never
reads/changes registered projects, existing pools, or Nginx configuration.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import pwd
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'daemon'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from project_runtime import RuntimePlan, fpm_config
from project_http_smoke import exercise_http
from project_lifecycle import RUNTIME_SECURITY_PROPERTIES
from project_runtime_identity import IdentityError, provision_fixture, verify_binding

PHP = r'''<?php
header('Content-Type: application/json');
$root = dirname(__DIR__);
$other = trim(file_get_contents($root . '/other-path.txt'));
$port = (int) trim(file_get_contents($root . '/port.txt'));
$db = new PDO('sqlite:' . $root . '/database/fixture.sqlite');
$db->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
$db->exec('CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY)');
$db->exec('INSERT INTO items DEFAULT VALUES');
$runtimeDb = new PDO('sqlite:' . $root . '/database/runtime-created.sqlite');
$runtimeDb->exec('CREATE TABLE IF NOT EXISTS runtime_proof (id INTEGER PRIMARY KEY)');
$runtimeDb->exec('INSERT OR IGNORE INTO runtime_proof VALUES (1)');
$restricted = $root . '/storage/restricted.txt';
file_put_contents($restricted, 'private fixture');
chmod($restricted, 0600);
$net = @stream_socket_client("tcp://127.0.0.1:$port", $errno, $errstr, 0.3);
if ($net) { fclose($net); }
// Run a fixed child of the actual FPM worker to verify inherited filters.
// No connections to the host and no caller-controlled command.
$policyProbe = <<<'PY'
import ctypes, errno, os, socket
for family, kind in ((socket.AF_UNIX, socket.SOCK_STREAM),
                     (socket.AF_INET, socket.SOCK_STREAM),
                     (socket.AF_INET6, socket.SOCK_STREAM),
                     (socket.AF_NETLINK, socket.SOCK_RAW)):
    with socket.socket(family, kind):
        pass
for family, kind in ((getattr(socket, 'AF_VSOCK', 40), socket.SOCK_STREAM),
                     (socket.AF_PACKET, socket.SOCK_DGRAM)):
    try:
        with socket.socket(family, kind):
            pass
    except OSError as exc:
        if exc.errno != errno.EAFNOSUPPORT:
            raise
    else:
        raise RuntimeError('Unexpected socket family')
if os.uname().machine != 'x86_64':
    raise RuntimeError('Unexpected CI architecture')
libc = ctypes.CDLL(None, use_errno=True)
libc.syscall.restype = ctypes.c_long
params = ctypes.create_string_buffer(256)
fd = libc.syscall(425, ctypes.c_uint(1), ctypes.byref(params))
if fd >= 0:
    os.close(fd)
    raise RuntimeError('io_uring unexpectedly allowed')
if ctypes.get_errno() != errno.ENOSYS:
    raise RuntimeError('Unexpected io_uring error')
print('FILTERED')
PY;
$proc = proc_open(['/usr/bin/python3', '-I', '-S', '-c', $policyProbe],
    [0 => ['file', '/dev/null', 'r'], 1 => ['pipe', 'w'],
     2 => ['file', '/dev/null', 'w']], $pipes);
$policyOK = false;
if (is_resource($proc)) {
    $out = stream_get_contents($pipes[1]);
    fclose($pipes[1]);
    $policyOK = proc_close($proc) === 0 && trim($out) === 'FILTERED';
}
$processStatus = file_get_contents('/proc/self/status');
$policyOK = $policyOK
    && preg_match('/^CapEff:[ \\t]+0+$/m', $processStatus) === 1
    && preg_match('/^CapPrm:[ \\t]+0+$/m', $processStatus) === 1
    && preg_match('/^NoNewPrivs:[ \\t]+1$/m', $processStatus) === 1
    && preg_match('/^Seccomp:[ \\t]+2$/m', $processStatus) === 1;
$result = [
    'security_policy' => $policyOK,
    'uid' => posix_geteuid(),
    'gid' => posix_getegid(),
    'own' => trim(file_get_contents($root . '/marker.txt')),
    'other_hidden' => @file_get_contents($other) === false,
    'link_blocked' => is_link($root . '/outside-link')
        && @file_get_contents($root . '/outside-link') === false,
    'source_read_only' => @file_put_contents($root . '/unexpected.txt', 'x') === false,
    'storage_write' => file_put_contents($root . '/storage/runtime.txt', 'runtime') === 7,
    'cache_write' => file_put_contents($root . '/bootstrap/cache/runtime.txt', 'cache') === 5,
    'nested_write' => (is_dir($root . '/storage/runtime-dir') || mkdir($root . '/storage/runtime-dir'))
        && file_put_contents($root . '/storage/runtime-dir/nested.txt', 'runtime') === 7,
    'sqlite_count' => (int) $db->query('SELECT COUNT(*) FROM items')->fetchColumn(),
    'devstack_hidden' => !file_exists('/run/devstack'),
    'shared_php_hidden' => !file_exists('/run/php'),
    'windows_hidden' => !file_exists('/mnt/c'),
    'home_hidden' => count(scandir('/home')) === 2,
    'network_namespace' => readlink('/proc/self/ns/net'),
    'network_blocked' => $net === false,
    'request_uri' => $_SERVER['REQUEST_URI'] ?? '',
    'request_method' => $_SERVER['REQUEST_METHOD'] ?? '',
    'request_body' => file_get_contents('php://input'),
    'https' => $_SERVER['HTTPS'] ?? '',
    'remote_addr' => $_SERVER['REMOTE_ADDR'] ?? '',
];
echo json_encode($result, JSON_THROW_ON_ERROR);
'''


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, timeout=30, **kwargs)


def as_user(user, *args, **kwargs):
    # Resolve the fixture identity and initialize its actual NSS groups.
    # Avoid PAM session policies and cached groups of the CI login user.
    account = pwd.getpwnam(user)
    return run('/usr/bin/setpriv', '--reuid=' + str(account.pw_uid),
               '--regid=' + str(account.pw_gid), '--init-groups',
               '--no-new-privs', '--', *args, **kwargs)


def fixture(base, name, account, developer, other, port):
    root = base / ('project-' + name)
    root.mkdir(mode=0o700)
    for rel in ('public', 'storage', 'bootstrap', 'bootstrap/cache', 'database'):
        (root / rel).mkdir(mode=0o700)
    (root / 'public/index.php').write_text(PHP)
    (root / 'marker.txt').write_text(name + '\n')
    (root / 'other-path.txt').write_text(str(other / 'marker.txt'))
    (root / 'port.txt').write_text(str(port))
    (root / 'outside-link').symlink_to(other / 'marker.txt')
    db = root / 'database/fixture.sqlite'
    db.touch(mode=0o660)
    # Only root-created known fixture entries; no user-controlled traversal.
    for path in [root, *root.rglob('*')]:
        os.chown(path, developer.pw_uid, developer.pw_gid, follow_symlinks=False)
        if not path.is_symlink():
            path.chmod(0o700 if path.is_dir() else 0o600)
    # The unprivileged owner, not root, prepares this new quiescent tree.
    # Inherited named ACLs remove the need for developer group membership.
    as_user(developer.pw_name, '/usr/bin/python3', '-I',
            str(base / 'launcher/project_access.py'), str(root))
    config_dir = base / ('config-' + name)
    config_dir.mkdir(mode=0o755)
    config = config_dir / 'fpm.conf'
    config.write_text(fpm_config())
    config.chmod(0o644)
    sockets = base / ('sockets-' + name)
    sockets.mkdir(mode=0o700)
    run('setfacl', '-m', 'u:' + account + ':rwx,u:www-data:--x', str(sockets))
    run('setfacl', '-m', 'd:u:www-data:rwX,d:g::---,d:o::---,d:m::rwx', str(sockets))
    # Reopening systemd's journal stream via /dev/stderr is not supported
    # inside this namespace. A private, pre-created fixture log is sufficient
    # for diagnostics; it is not an authoritative audit log.
    log = sockets / 'fpm-error.log'
    log.touch(mode=0o600)
    identity = pwd.getpwnam(account)
    os.chown(log, identity.pw_uid, identity.pw_gid)
    log.chmod(0o600)
    return RuntimePlan(root, config, sockets)


def start(unit, user, plan):
    run('systemd-run', '--quiet', '--collect', '--service-type=exec',
        '--unit=' + unit, '--uid=' + user,
        *RUNTIME_SECURITY_PROPERTIES,
        '--property=KillMode=control-group',
        '--property=UMask=0007',
        '--property=TimeoutStopSec=5s',
        '--property=RuntimeMaxSec=180s',
        *plan.launch_argv())
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if (plan.sockets / 'php.sock').is_socket():
            return
        time.sleep(0.1)
    raise RuntimeError('FPM socket did not become ready: ' + unit)

    
def stop(unit):
    result = subprocess.run(['systemctl', 'stop', unit], check=False,
                            capture_output=True, text=True, timeout=30)
    state = subprocess.run(['systemctl', 'show', '-p', 'LoadState',
                            '-p', 'ActiveState', unit], check=False,
                           capture_output=True, text=True, timeout=10)
    values = dict(line.split('=', 1) for line in state.stdout.splitlines()
                  if '=' in line)
    # A --collect transient unit may already have disappeared.
    if values.get('LoadState') == 'not-found':
        return
    if result.returncode or values.get('ActiveState') not in ('inactive', 'failed'):
        raise RuntimeError('Cannot confirm runtime stopped: ' + unit)


def assert_socket_closed(path):
    # A killed FPM may leave a stale pathname; check the listening endpoint.
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(1)
        try:
            client.connect(str(path))
        except (FileNotFoundError, ConnectionRefusedError):
            return
        raise AssertionError('Stopped runtime still accepts connections')


def request(plan):
    result = as_user(
        'www-data', '/usr/bin/env', '-i',
        'REQUEST_METHOD=GET', 'SCRIPT_NAME=/index.php',
        'SCRIPT_FILENAME=' + str(plan.project / 'public/index.php'),
        'DOCUMENT_ROOT=' + str(plan.project / 'public'),
        'SERVER_PROTOCOL=HTTP/1.1', 'SERVER_NAME=fixture.test',
        'REMOTE_ADDR=127.0.0.1', 'CONTENT_LENGTH=0', 'REDIRECT_STATUS=200',
        '/usr/bin/cgi-fcgi', '-bind', '-connect', str(plan.sockets / 'php.sock'),
        input='', capture_output=True)
    _, sep, body = result.stdout.replace('\r\n', '\n').partition('\n\n')
    if not sep:
        raise RuntimeError('FastCGI did not return a response body')
    return json.loads(body)


def verify(plan, name, identity, host_net, expected_count):
    result = request(plan)
    assert result['uid'] == identity.pw_uid, result
    assert result['gid'] == identity.pw_gid, result
    assert result['own'] == name, result
    assert result['sqlite_count'] == expected_count, result
    for field in ('security_policy', 'other_hidden', 'link_blocked', 'source_read_only',
                  'storage_write', 'cache_write', 'nested_write', 'devstack_hidden',
                  'shared_php_hidden', 'windows_hidden', 'home_hidden', 'network_blocked'):
        assert result[field] is True, (field, result)
    assert result['network_namespace'] != host_net, result
    assert not (plan.project / 'unexpected.txt').exists()
    print('PASS: PHP-FPM fixture', name, 'request', expected_count, flush=True)
    return result


def main():
    if (os.geteuid() != 0 or os.getenv('GITHUB_ACTIONS') != 'true'
            or os.getenv('DEVSTACK_RUNTIME_CI') != '1'):
        raise SystemExit('Only for the disposable CI runner, not a workstation.')
    token = secrets.token_hex(5)
    project_ids = [secrets.token_hex(16), secrets.token_hex(16)]
    users = []
    developer_name = 'dsrt-dev-' + token
    created = []
    attempted = []
    base = Path(tempfile.mkdtemp(prefix='devstack-runtime-probe-', dir='/var/lib'))
    base.chmod(0o711)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    port = listener.getsockname()[1]
    try:
        # Stage a fixed, root-owned launcher readable by the fixture account,
        # independent of the hosted runner's home-directory permissions.
        launcher = base / 'launcher'
        launcher.mkdir(mode=0o755)
        source = Path(__file__).resolve().parents[1] / 'daemon'
        for name in ('project_runtime_launch.py', 'project_runtime.py',
                     'project_http.py', 'project_mounts.py', 'project_runtime_identity.py',
                     'project_access.py'):
            destination = launcher / name
            destination.write_bytes((source / name).read_bytes())
            destination.chmod(0o644)
        # Host connectivity is established before testing the namespace denial.
        with socket.create_connection(('127.0.0.1', port), timeout=1):
            conn, _ = listener.accept()
            conn.close()
        for slot, project_id in zip(('a', 'b'), project_ids):
            binding = provision_fixture(base, slot, project_id)
            users.append(binding['account'])
            created.append(binding['account'])
        units = ['devstack-runtime-probe-' + name for name in users]
        for slot, project_id, user in zip(('a', 'b'), project_ids, users):
            record = verify_binding(base, slot, expected_user=user)
            assert record['project_id'] == project_id
        try:
            verify_binding(base, 'a', expected_user=users[1])
        except IdentityError:
            pass
        else:
            raise AssertionError('Sibling identity was accepted')
        # A retained record must not authorize a recycled or altered UID.
        path = base / 'identity-a.json'
        original = path.read_text()
        changed = json.loads(original)
        changed['uid'] = pwd.getpwnam(users[1]).pw_uid
        try:
            path.write_text(json.dumps(changed))
            try:
                verify_binding(base, 'a')
            except IdentityError:
                pass
            else:
                raise AssertionError('Mismatched UID was accepted')
        finally:
            path.write_text(original)
        print('PASS: Durable registration-account binding and mismatched UID refusal', flush=True)
        # Exercise the actual staged launcher with the sibling's real UID.
        # It must refuse at the identity gate, before attempting any mounts.
        try:
            as_user(users[1], '/usr/bin/python3', '-I',
                    str(launcher / 'project_runtime_launch.py'),
                    'php', str(base / 'project-a'), capture_output=True)
        except subprocess.CalledProcessError as exc:
            assert exc.returncode == 2, exc.returncode
            assert 'Fixture source launch refused: IdentityError' in exc.stderr
        else:
            raise AssertionError('Sibling runtime launcher accepted')
        print('PASS: Real launcher denies a different project identity', flush=True)
        # One ordinary developer principal, no added runtime-group memberships.
        run('useradd', '--system', '--user-group', '--no-create-home',
            '--home-dir', '/nonexistent', '--shell', '/usr/sbin/nologin',
            developer_name)
        created.append(developer_name)
        developer = pwd.getpwnam(developer_name)
        identities = [pwd.getpwnam(user) for user in users]
        expected_groups = {developer.pw_gid}
        assert set(os.getgrouplist(developer_name, developer.pw_gid)) == expected_groups
        actual = as_user(developer_name, '/usr/bin/id', '-G', capture_output=True)
        actual_groups = set(map(int, actual.stdout.split()))
        assert actual_groups == expected_groups, (actual_groups, expected_groups)
        print('PASS: Developer has no runtime group memberships', flush=True)
        assert identities[0].pw_uid != identities[1].pw_uid
        for user, identity in zip(users, identities):
            assert identity.pw_uid != developer.pw_uid
            groups = os.getgrouplist(user, identity.pw_gid)
            assert groups == [identity.pw_gid], (user, groups)
        # Separate, privileged feasibility test: mount only newly created
        # disposable data, in a private child mount namespace. No changes to
        # the established ACL/FPM prototype or its expected limitation.
        subprocess.run(['/usr/bin/unshare', '--mount', '--propagation', 'private',
            '/usr/bin/python3', '-I',
            str(Path(__file__).with_name('project-idmap-smoke.py').absolute()),
            str(base), developer_name, str(os.stat('/proc/self/ns/mnt').st_ino)],
            check=True, text=True, timeout=120)
        plans = [
            fixture(base, 'a', users[0], developer, base / 'project-b', port),
            fixture(base, 'b', users[1], developer, base / 'project-a', port),
        ]
        for index in (0, 1):
            # Test the shared writable directory independently of FPM.
            # Both accounts must be able to modify a file created with normal defaults.
            sample = plans[index].project / 'storage' / 'acl-probe.txt'
            as_user(developer_name, '/usr/bin/python3', '-c',
                    'import sys; open(sys.argv[1], "w").write("developer")', str(sample))
            as_user(users[index], '/usr/bin/python3', '-c',
                    'import sys; open(sys.argv[1], "a").write("-runtime")', str(sample))
            assert sample.read_text() == 'developer-runtime'
            as_user(users[index], '/usr/bin/test', '!', '-r',
                    str(plans[1 - index].project / 'marker.txt'))
            # A normal editor's newly created file inherits the runtime read
            # grant, not permission to write sources. Test actual I/O.
            source = plans[index].project / 'public/editor-created.js'
            as_user(developer_name, '/usr/bin/python3', '-c',
                    'import sys;open(sys.argv[1],"w").write("editor")', str(source))
            assert as_user(users[index], '/usr/bin/cat', str(source),
                           capture_output=True).stdout == 'editor'
            as_user(users[index], '/usr/bin/python3', '-c', '''
import sys
try:
    with open(sys.argv[1], 'a') as f:
        f.write('unexpected')
except PermissionError:
    pass
else:
    raise SystemExit('Runtime can write source outside sandbox')
''', str(source))
            attempted.append(units[index])
            start(units[index], users[index], plans[index])
        host_net = os.readlink('/proc/self/ns/net')
        a = verify(plans[0], 'a', identities[0], host_net, 1)
        b = verify(plans[1], 'b', identities[1], host_net, 1)
        assert a['network_namespace'] != b['network_namespace']
        verify(plans[0], 'a', identities[0], host_net, 2)
        for index, plan in enumerate(plans):
            assert (plan.project / 'storage/runtime.txt').read_text() == 'runtime'
            output = plan.project / 'storage/runtime.txt'
            assert output.stat().st_gid == identities[index].pw_gid
            run('getfacl', '-p', str(output))
            run('namei', '-l', str(output))
            as_user(developer_name, '/usr/bin/id')
            # Do not infer functional write access from an access(2) probe.
            # Exercise the actual open/write operation and verify its effect.
            as_user(developer_name, '/usr/bin/python3', '-c', '''
import os, sys
p = sys.argv[1]
s = os.stat(p)
print("Developer file probe:", os.getuid(), os.getgroups(),
      s.st_uid, s.st_gid, oct(s.st_mode), "access(W_OK) =", os.access(p, os.W_OK),
      flush=True)
with open(p, "a") as stream:
    stream.write("-developer")
''', str(output))
            assert output.read_text() == 'runtime-developer'
            # The CLI must also be able to commit a real transaction in the
            # same SQLite database. Use an independent table so the existing
            # FPM request count remains a useful regression assertion.
            as_user(developer_name, '/usr/bin/python3', '-c', '''
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as db:
    db.execute("CREATE TABLE developer_probe (value TEXT NOT NULL)")
    db.execute("INSERT INTO developer_probe VALUES (?)", ("developer",))
''', str(plan.project / 'database/fixture.sqlite'))
            import sqlite3
            with sqlite3.connect(plan.project / 'database/fixture.sqlite') as db:
                assert db.execute('SELECT value FROM developer_probe').fetchall() == [('developer',)]
            # Real runtime-owned nested entries and a database created by PHP,
            # not only a file that was previously owned by the developer.
            nested = plan.project / 'storage/runtime-dir/nested.txt'
            as_user(developer_name, '/usr/bin/python3', '-c',
                    'import sys;open(sys.argv[1],"a").write("-developer")', str(nested))
            assert nested.read_text() == 'runtime-developer'
            runtime_db = plan.project / 'database/runtime-created.sqlite'
            assert runtime_db.stat().st_uid == identities[index].pw_uid
            # SQLite may create a database with mode 0644. In that case the
            # inherited named developer ACL is masked to read-only. Record
            # the real behavior rather than chmod'ing it in the application
            # or silently declaring runtime-created data fully compatible.
            result = as_user(developer_name, '/usr/bin/python3', '-c', '''
import os, sqlite3, stat, sys
path, restricted = sys.argv[1:]
from pathlib import Path
with sqlite3.connect(Path(path).as_uri()+'?mode=ro', uri=True) as db:
    assert db.execute('SELECT id FROM runtime_proof').fetchall() == [(1,)]
with sqlite3.connect(path) as db:
    if os.stat(path).st_mode & stat.S_IWGRP:
        db.execute('INSERT INTO runtime_proof VALUES (2)')
        print('runtime-db:shared-write')
    else:
        try:
            db.execute('INSERT INTO runtime_proof VALUES (2)')
        except sqlite3.OperationalError as exc:
            assert 'readonly' in str(exc).lower(), str(exc)
        else:
            raise AssertionError('Restrictive SQLite creation mode was bypassed')
        print('runtime-db:masked-read-only')
try:
    with open(restricted) as stream:
        stream.read()
except PermissionError:
    pass
else:
    raise AssertionError('Explicit private mode was bypassed')
''', str(runtime_db), str(plan.project / 'storage/restricted.txt'), capture_output=True)
            observed = result.stdout.strip()
            with sqlite3.connect(runtime_db) as conn:
                rows = conn.execute('SELECT id FROM runtime_proof ORDER BY id').fetchall()
                if observed == 'runtime-db:masked-read-only':
                    assert rows == [(1,)]
                    print('OBSERVED LIMITATION: runtime-created SQLite ACL mask is read-only for developer', index, flush=True)
                else:
                    assert observed == 'runtime-db:shared-write' and rows == [(1,), (2,)]
                    print('PASS: runtime-created SQLite permits shared write on this build', index, flush=True)
            print('PASS: Developer writes nested runtime-owned data and pre-provisioned SQLite without runtime groups; private mode respected', index, flush=True)
            as_user(users[1 - index], '/usr/bin/test', '!', '-w',
                    str(plan.sockets / 'php.sock'))
        assert set(os.getgrouplist(developer_name, developer.pw_gid)) == expected_groups
        print('PASS: Separate identities, sockets and developer access; no group changes')
        for slot, user in zip(('a', 'b'), users):
            verify_binding(base, slot, expected_user=user)
        # Hand off the same fixture data to the real pair lifecycle. Never
        # run a legacy/direct FPM instance alongside the new pair.
        for index, unit in enumerate(units):
            stop(unit)
            assert_socket_closed(plans[index].sockets / 'php.sock')
        exercise_http(base, token, plans, users, developer, identities, attempted,
                      as_user, stop, verify, host_net)
    except BaseException:
        for unit in attempted:
            subprocess.run(['journalctl', '-u', unit, '--no-pager', '-n', '60'],
                           check=False, timeout=10)
        for name in ('a', 'b'):
            log = base / ('sockets-' + name) / 'fpm-error.log'
            if log.is_file():
                print('Fixture FPM log', name, log.read_text(errors='replace')[-6000:])
        raise
    finally:
        listener.close()
        for unit in reversed(attempted):
            stop(unit)
        # Do not delete fixture data or accounts if stopping a unit failed.
        shutil.rmtree(base)
        for user in reversed(created):
            run('userdel', user)
    print('=== PHP RUNTIME AND HTTPS INGRESS PROTOTYPE PASSED ===')


if __name__ == '__main__':
    main()
