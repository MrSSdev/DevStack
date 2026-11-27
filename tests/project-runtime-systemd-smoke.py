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
from project_runtime import RuntimePlan, fpm_config

PHP = r'''<?php
header('Content-Type: application/json');
$root = dirname(__DIR__);
$other = trim(file_get_contents($root . '/other-path.txt'));
$port = (int) trim(file_get_contents($root . '/port.txt'));
$db = new PDO('sqlite:' . $root . '/database/fixture.sqlite');
$db->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
$db->exec('CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY)');
$db->exec('INSERT INTO items DEFAULT VALUES');
$net = @stream_socket_client("tcp://127.0.0.1:$port", $errno, $errstr, 0.3);
if ($net) { fclose($net); }
$result = [
    'uid' => posix_geteuid(),
    'gid' => posix_getegid(),
    'own' => trim(file_get_contents($root . '/marker.txt')),
    'other_hidden' => @file_get_contents($other) === false,
    'link_blocked' => is_link($root . '/outside-link')
        && @file_get_contents($root . '/outside-link') === false,
    'source_read_only' => @file_put_contents($root . '/unexpected.txt', 'x') === false,
    'storage_write' => file_put_contents($root . '/storage/runtime.txt', 'runtime') === 7,
    'cache_write' => file_put_contents($root . '/bootstrap/cache/runtime.txt', 'cache') === 5,
    'sqlite_count' => (int) $db->query('SELECT COUNT(*) FROM items')->fetchColumn(),
    'devstack_hidden' => !file_exists('/run/devstack'),
    'shared_php_hidden' => !file_exists('/run/php'),
    'windows_hidden' => !file_exists('/mnt/c'),
    'home_hidden' => count(scandir('/home')) === 2,
    'network_namespace' => readlink('/proc/self/ns/net'),
    'network_blocked' => $net === false,
];
echo json_encode($result, JSON_THROW_ON_ERROR);
'''


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, timeout=30, **kwargs)


def as_user(user, *args, **kwargs):
    return run('/usr/sbin/runuser', '-u', user, '--', *args, **kwargs)


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
    run('setfacl', '-P', '-R', '-m', 'u:' + account + ':rX', str(root))
    for relative in ('storage', 'bootstrap/cache', 'database'):
        path = root / relative
        run('setfacl', '-m', 'u:' + account + ':rwx', str(path))
        run('setfacl', '-m',
            'd:u:' + developer.pw_name + ':rwx,d:u:' + account + ':rwx,d:g::---,d:o::---,d:m::rwx',
            str(path))
    run('setfacl', '-m', 'u:' + account + ':rw', str(db))
    config_dir = base / ('config-' + name)
    config_dir.mkdir(mode=0o755)
    config = config_dir / 'fpm.conf'
    config.write_text(fpm_config())
    config.chmod(0o644)
    sockets = base / ('sockets-' + name)
    sockets.mkdir(mode=0o700)
    run('setfacl', '-m', 'u:' + account + ':rwx,u:www-data:--x', str(sockets))
    run('setfacl', '-m', 'd:u:www-data:rwX,d:g::---,d:o::---,d:m::rwx', str(sockets))
    return RuntimePlan(root, config, sockets)


def start(unit, user, plan):
    run('systemd-run', '--quiet', '--collect', '--service-type=exec',
        '--unit=' + unit, '--uid=' + user,
        '--property=NoNewPrivileges=yes',
        '--property=KillMode=control-group',
        '--property=UMask=0007',
        '--property=TimeoutStopSec=5s',
        '--property=RuntimeMaxSec=180s',
        *plan.argv())
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if (plan.sockets / 'php.sock').is_socket():
            return
        time.sleep(0.1)
    raise RuntimeError('FPM socket did not become ready: ' + unit)


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
    for field in ('other_hidden', 'link_blocked', 'source_read_only',
                  'storage_write', 'cache_write', 'devstack_hidden',
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
    developer = pwd.getpwuid(int(os.environ['SUDO_UID']))
    if developer.pw_uid == 0:
        raise SystemExit('Requires a non-root CI developer.')
    token = secrets.token_hex(5)
    users = ['dsrt-a-' + token, 'dsrt-b-' + token]
    units = ['devstack-runtime-probe-' + name for name in users]
    created = []
    attempted = []
    base = Path(tempfile.mkdtemp(prefix='devstack-runtime-probe-', dir='/var/lib'))
    base.chmod(0o711)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    port = listener.getsockname()[1]
    try:
        # Host connectivity is established before testing the namespace denial.
        with socket.create_connection(('127.0.0.1', port), timeout=1):
            conn, _ = listener.accept()
            conn.close()
        for user in users:
            run('useradd', '--system', '--user-group', '--no-create-home',
                '--shell', '/usr/sbin/nologin', user)
            created.append(user)
        identities = [pwd.getpwnam(user) for user in users]
        assert identities[0].pw_uid != identities[1].pw_uid
        for user, identity in zip(users, identities):
            assert identity.pw_uid != developer.pw_uid
            groups = os.getgrouplist(user, identity.pw_gid)
            assert groups == [identity.pw_gid], (user, groups)
        plans = [
            fixture(base, 'a', users[0], developer, base / 'project-b', port),
            fixture(base, 'b', users[1], developer, base / 'project-a', port),
        ]
        for index in (0, 1):
            as_user(users[index], '/usr/bin/test', '!', '-r',
                    str(plans[1 - index].project / 'marker.txt'))
            attempted.append(units[index])
            start(units[index], users[index], plans[index])
        host_net = os.readlink('/proc/self/ns/net')
        a = verify(plans[0], 'a', identities[0], host_net, 1)
        b = verify(plans[1], 'b', identities[1], host_net, 1)
        assert a['network_namespace'] != b['network_namespace']
        verify(plans[0], 'a', identities[0], host_net, 2)
        for index, plan in enumerate(plans):
            assert (plan.project / 'storage/runtime.txt').read_text() == 'runtime'
            as_user(developer.pw_name, '/usr/bin/test', '-w',
                    str(plan.project / 'storage/runtime.txt'))
            as_user(developer.pw_name, '/usr/bin/test', '-w',
                    str(plan.project / 'database/fixture.sqlite'))
            as_user(users[1 - index], '/usr/bin/test', '!', '-w',
                    str(plan.sockets / 'php.sock'))
        print('PASS: Separate identities, sockets and developer access')
        run('systemctl', 'stop', units[0])
        assert not (plans[0].sockets / 'php.sock').is_socket()
        verify(plans[1], 'b', identities[1], host_net, 2)
        print('PASS: Independent project stop')
    except BaseException:
        for unit in attempted:
            subprocess.run(['journalctl', '-u', unit, '--no-pager', '-n', '60'],
                           check=False, timeout=10)
        raise
    finally:
        listener.close()
        for unit in attempted:
            run('systemctl', 'stop', unit)
        # Do not delete fixture data or accounts if stopping a unit failed.
        shutil.rmtree(base)
        for user in reversed(created):
            run('userdel', user)
    print('=== PHP RUNTIME PROTOTYPE PASSED ===')


if __name__ == '__main__':
    main()
