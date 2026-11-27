"""Real HTTPS ingress and static-file isolation for the existing FPM fixtures.

Imported only by the disposable CI smoke test; not a deployment command.
"""
from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import pwd
import socket
import ssl
import subprocess
import time

from project_http import ProjectHttp, gateway_config
from project_mounts import SHARED_DATA
from project_lifecycle import LifecycleError, RuntimePair, StartError


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, timeout=30, **kwargs)


def launch(unit, user, args):
    run('systemd-run', '--quiet', '--collect', '--service-type=exec',
        '--unit=' + unit, '--uid=' + user,
        '--property=NoNewPrivileges=yes', '--property=KillMode=control-group',
        '--property=UMask=0007', '--property=TimeoutStopSec=5s',
        '--property=RuntimeMaxSec=180s', *args)


def prepare_public(root, developer, name, other, outside, as_user, *, mapped_data=False):
    # Simulate the normal editor adding source/build files and Laravel's
    # published storage. All new entries inherit the prepared named ACLs.
    as_user(developer.pw_name, '/usr/bin/python3', '-I', '-c', '''
from pathlib import Path
import os, sys
root, name, other, outside, mapped = sys.argv[1:]
if mapped == 'yes':
    os.umask(0o077)
root, other = Path(root), Path(other)
public = root / 'public'
build = public / 'build/assets'
build.mkdir(parents=True)
(build / 'app.js').write_text('const project = "' + name + '";')
(public / '.env').write_text('PRIVATE_ENV_' + name)
(public / 'private.php').write_text('<?php echo "PRIVATE_PHP_' + name + '";')
(build / 'host.txt').symlink_to(outside)
(build / 'other.txt').symlink_to(other / 'public/build/assets/app.js')
(build / 'private.txt').symlink_to(root / 'marker.txt')
(build / 'system.txt').symlink_to('/usr/bin/bash')
published = root / 'storage/app/public'
published.mkdir(parents=True)
(published / 'upload.txt').write_text('upload-' + name)
(published / 'outside.txt').symlink_to(outside)
(public / 'storage').symlink_to(published, target_is_directory=True)
''', str(root), name, str(other), str(outside), 'yes' if mapped_data else 'no')

def mapped_assertions(plan, payload, developer, *, edited=False):
    root = plan.project
    assert payload['source_read_only'] and payload['storage_write']
    assert payload['cache_write'] and payload['nested_write']
    assert payload['link_blocked'] and payload['devstack_hidden']
    assert payload['shared_php_hidden'] and payload['windows_hidden'] and payload['home_hidden']
    assert 'ro' in payload['mounts'][str(root)], payload
    for relative in SHARED_DATA:
        flags = set(payload['mounts'][str(root / relative)])
        assert {'rw', 'nosuid', 'nodev', 'noexec'} <= flags, (relative, flags)
    for relative in ('storage/runtime.txt', 'storage/runtime-dir/nested.txt',
                     'bootstrap/cache/runtime.txt', 'database/fixture.sqlite',
                     'database/runtime-created.sqlite', 'storage/restricted.txt',
                     'storage/app/public/runtime-upload.txt'):
        info = (root / relative).stat()
        assert (info.st_uid, info.st_gid) == (developer.pw_uid, developer.pw_gid), relative
    assert (root / 'storage/restricted.txt').stat().st_mode & 0o777 == 0o600
    assert payload['runtime_proof'] == ([1, 2] if edited else [1]), payload
    assert payload['restricted_content'] == ('private fixture-developer' if edited
                                             else 'private fixture'), payload


def developer_edit(plan, developer, as_user):
    # The actual developer credentials, not root and not runtime-group
    # membership. No chmod/chown/ACL repair between PHP and this transaction.
    as_user(developer.pw_name, '/usr/bin/python3', '-I', '-c', '''
from pathlib import Path
import sqlite3, sys
root = Path(sys.argv[1])
with sqlite3.connect(root / 'database/runtime-created.sqlite') as db:
    db.execute('INSERT INTO runtime_proof VALUES (2)')
with (root / 'storage/restricted.txt').open('a') as f:
    f.write('-developer')
with (root / 'storage/runtime-dir/nested.txt').open('a') as f:
    f.write('-developer')
''', str(plan.project))


def exercise_http(base, token, plans, users, developer, identities, attempted,
                  as_user, stop, verify, host_net, pairs):
    outside = base / 'outside-public.txt'
    outside.write_text('OUTSIDE_FIXTURE_SECRET')
    outside.chmod(0o644)
    # The gateway identity really can read this external canary on the host.
    assert as_user('www-data', '/usr/bin/cat', str(outside),
                   capture_output=True).stdout == 'OUTSIDE_FIXTURE_SECRET'
    routes = {}
    mapped_data = plans[0].mapped_data
    assert all(plan.mapped_data == mapped_data for plan in plans)
    counts = [0, 0] if mapped_data else [2, 1]
    for index, name in enumerate(('a', 'b')):
        runtime = plans[index]
        user = users[index]
        prepare_public(runtime.project, developer, name,
                       plans[1 - index].project, outside, as_user, mapped_data=mapped_data)
        # Shared ingress cannot read project source or public assets directly.
        as_user('www-data', '/usr/bin/python3', '-c', '''
import sys
try:
    with open(sys.argv[1]) as f:
        f.read()
except PermissionError:
    pass
else:
    raise SystemExit("Ingress unexpectedly reads project source")
''', str(runtime.project / 'public/build/assets/app.js'))
        config = base / ('config-' + name) / 'nginx.conf'
        sockets = base / ('http-' + name)
        sockets.mkdir(mode=0o700)
        run('setfacl', '-m', 'u:' + user + ':rwx,u:www-data:--x', str(sockets))
        run('setfacl', '-m', 'd:u:www-data:rwX,d:g::---,d:o::---,d:m::rwx', str(sockets))
        plan = ProjectHttp(runtime, config, sockets)
        config.write_text(plan.nginx_config())
        config.chmod(0o644)
        pair = RuntimePair(plan, user)
        attempted.extend(pair.units)
        pairs.append(pair)
        # A colliding unit name is not an authorization to stop it.
        if index == 0:
            run('systemd-run', '--quiet', '--collect', '--service-type=exec',
                '--unit=' + pair.php.unit, '--uid=' + user,
                '--description=Unrelated disposable fixture', '/usr/bin/sleep', '30')
            try:
                try:
                    pair.start()
                except LifecycleError:
                    pass
                else:
                    raise AssertionError('Foreign unit accepted by lifecycle')
                run('systemctl', 'is-active', '--quiet', pair.php.unit)
            finally:
                stop(pair.php.unit)
            print('PASS: Foreign unit collision refused without stopping it', flush=True)
        assert pair.status() == 'stopped'
        if mapped_data:
            # Missing publications must not silently fall back to ACL mounts.
            try:
                as_user(user, *runtime.launch_argv(), capture_output=True)
            except subprocess.CalledProcessError as exc:
                assert exc.returncode == 2, exc.returncode
            else:
                raise AssertionError('Mapped launcher accepted missing publications')
        assert pair.start() == 'running'
        # Idempotent start must not replace either transient invocation.
        def invocation(unit):
            return run('systemctl', 'show', '-p', 'InvocationID', '--value', unit,
                       capture_output=True).stdout.strip()
        before = tuple(invocation(unit) for unit in pair.units)
        assert all(before)
        assert pair.start() == 'running'
        assert tuple(invocation(unit) for unit in pair.units) == before
        if mapped_data:
            assert pair.data.state() == 'ready'
        routes[name + '.fixture.test'] = plan

    work = base / 'gateway'
    work.mkdir(mode=0o700)
    www = pwd.getpwnam('www-data')
    os.chown(work, www.pw_uid, www.pw_gid)
    cert = work / 'cert.pem'
    key = work / 'tls-key.pem'
    run('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
        '-days', '1', '-subj', '/CN=localhost',
        '-addext', 'subjectAltName=IP:127.0.0.1,DNS:a.fixture.test,DNS:b.fixture.test',
        '-keyout', str(key), '-out', str(cert), capture_output=True)
    os.chown(key, www.pw_uid, www.pw_gid)
    key.chmod(0o600)
    cert.chmod(0o644)
    with socket.socket() as reserve:
        reserve.bind(('127.0.0.1', 0))
        port = reserve.getsockname()[1]
    gateway = base / 'gateway.conf'
    gateway.write_text(gateway_config(work, cert, key, port, routes))
    gateway.chmod(0o644)
    unit = 'devstack-gateway-probe-' + token
    attempted.append(unit)
    launch(unit, 'www-data', ['/usr/sbin/nginx', '-p', str(work) + '/', '-c', str(gateway)])
    ctx = ssl.create_default_context(cafile=str(cert))

    def request(host, path, *, headers=None, method='GET', body=None):
        with_connection = http.client.HTTPSConnection('127.0.0.1', port,
                                                      context=ctx, timeout=5)
        try:
            with_connection.request(method, path, body=body,
                                    headers={'Host': host, **(headers or {})})
            response = with_connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            with_connection.close()

    deadline = time.monotonic() + 15
    while True:
        try:
            assert request('unknown.fixture.test', '/')[0] == 421
            break
        except (ConnectionError, OSError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)
    for index, name in enumerate(('a', 'b')):
        host = name + '.fixture.test'
        status, body, headers = request(host, '/build/assets/app.js')
        assert status == 200 and body == ('const project = "' + name + '";').encode(), (status, body)
        assert 'javascript' in headers['Content-Type']
        status, body, _ = request(host, '/storage/upload.txt')
        assert status == 200 and body == ('upload-' + name).encode(), (status, body)
        for path in ('/build/assets/host.txt', '/build/assets/other.txt',
                     '/build/assets/private.txt', '/build/assets/system.txt',
                     '/storage/outside.txt', '/.env', '/private.php'):
            status, body, _ = request(host, path)
            assert status in (403, 404), (host, path, status, body[:300])
        status, body, _ = request(host, '/livewire/update?fixture=1', method='POST',
                                  headers={'X-Forwarded-Proto': 'http',
                                           'X-Forwarded-For': '203.0.113.42',
                                           'Content-Type': 'application/json'},
                                  body='{"fixture":true}')
        assert status == 200, (status, body)
        payload = json.loads(body)
        assert payload['uid'] == identities[index].pw_uid and payload['own'] == name
        assert payload['request_uri'] == '/livewire/update?fixture=1'
        assert payload['request_method'] == 'POST' and payload['request_body'] == '{"fixture":true}'
        assert payload['https'] == 'on' and payload['remote_addr'] == '127.0.0.1'
        assert payload['other_hidden'] and payload['network_blocked']
        assert payload['security_policy'] is True
        counts[index] += 1
        assert payload['sqlite_count'] == counts[index], payload
        if mapped_data:
            mapped_assertions(plans[index], payload, developer)
            sibling = pairs[1-index].data.root / 'storage/app/public/upload.txt'
            as_user(users[index], '/usr/bin/python3', '-I', '-c', '''
import sys
try:
    with open(sys.argv[1]) as f:
        f.read()
except PermissionError:
    pass
else:
    raise AssertionError('Sibling mapped data readable')
''', str(sibling))
            # The original private tree is not granted runtime write access.
            as_user(users[index], '/usr/bin/python3', '-I', '-c', '''
import sys
try:
    with open(sys.argv[1], 'a') as f:
        f.write('unexpected')
except PermissionError:
    pass
else:
    raise AssertionError('Unmapped storage writable')
''', str(plans[index].project / 'storage/restricted.txt'))
            developer_edit(plans[index], developer, as_user)
            assert (plans[index].project / 'storage/runtime-dir/nested.txt').read_text() == 'runtime-developer'
            status, body, _ = request(host, '/after-developer-write')
            assert status == 200, (status, body)
            payload = json.loads(body)
            counts[index] += 1
            assert payload['sqlite_count'] == counts[index], payload
            mapped_assertions(plans[index], payload, developer, edited=True)
            status, body, _ = request(host, '/storage/runtime-upload.txt')
            assert status == 200 and body == b'runtime-upload', (status, body)
            print('PASS: IDMAP FPM creates shared SQLite and mode 0600; developer writes; FPM and HTTP observe changes', name, flush=True)
        print('PASS: HTTPS, PHP routing, static assets and published storage', name, flush=True)
        print('PASS: Host/sibling/private symlinks, hidden and PHP source denied', name, flush=True)

    a, b = pairs
    assert a.stop() == 'stopped'
    assert a.stop() == 'stopped'
    assert a.status() == 'stopped' and b.status() == 'running'
    assert request('a.fixture.test', '/build/assets/app.js')[0] == 502
    assert request('b.fixture.test', '/build/assets/app.js')[0] == 200
    print('PASS: Ingress fails closed and other project remains available', flush=True)
    if mapped_data:
        assert a.data.state() == 'absent' and b.data.state() == 'ready'
        # Force failure after two known data views were published. The
        # fixture is stopped and controlled by the test. Restore its owner
        # before any new execution; never modify a registered project.
        database = a.plan.runtime.project / 'database'
        try:
            os.chown(database, 0, 0)
            try:
                a.start()
            except StartError as exc:
                assert exc.component == 'data' and exc.rolled_back, exc
            else:
                raise AssertionError('Foreign data owner accepted')
            assert a.status() == 'stopped' and a.data.state() == 'absent'
            assert b.status() == 'running' and b.data.state() == 'ready'
        finally:
            os.chown(database, developer.pw_uid, developer.pw_gid)
        assert request('b.fixture.test', '/build/assets/app.js')[0] == 200
        print('PASS: Partial IDMAP publication rolled back; sibling preserved', flush=True)

    # Force a real Nginx startup failure after PHP has started. The control
    # file is root-owned; neither project is asked to execute test shell code.
    config = a.plan.config
    original = config.read_text()
    try:
        config.write_text('invalid_fixture_directive;\n')
        try:
            a.start()
        except StartError as exc:
            assert exc.component == 'http' and exc.rolled_back, exc
        else:
            raise AssertionError('Broken HTTP configuration unexpectedly accepted')
        assert a.status() == 'stopped' and b.status() == 'running'
        if mapped_data:
            assert a.data.state() == 'absent' and b.data.state() == 'ready'
        assert request('a.fixture.test', '/')[0] == 502
        assert request('b.fixture.test', '/build/assets/app.js')[0] == 200
    finally:
        config.write_text(original)
    print('PASS: Real partial startup rolled back; sibling remains running', flush=True)
    assert a.start() == 'running'
    status, body, _ = request('a.fixture.test', '/after-rollback')
    counts[0] += 1
    assert status == 200 and json.loads(body)['sqlite_count'] == counts[0], (status, body)
    if mapped_data:
        mapped_assertions(plans[0], json.loads(body), developer, edited=True)
    assert a.restart() == 'running'
    status, body, _ = request('a.fixture.test', '/after-restart')
    counts[0] += 1
    assert status == 200 and json.loads(body)['sqlite_count'] == counts[0], (status, body)
    if mapped_data:
        mapped_assertions(plans[0], json.loads(body), developer, edited=True)
    assert b.status() == 'running'
    counts[1] += 1
    payload = verify(plans[1], 'b', identities[1], host_net, counts[1])
    if mapped_data:
        mapped_assertions(plans[1], payload, developer, edited=True)
    print('PASS: Pair restart preserves SQLite data and independent project', flush=True)
    assert a.stop() == 'stopped'
    assert b.stop() == 'stopped'
    if mapped_data:
        assert a.data.state() == b.data.state() == 'absent'
        for plan in plans:
            import sqlite3
            with sqlite3.connect((plan.project / 'database/runtime-created.sqlite').as_uri()+'?mode=ro', uri=True) as db:
                assert db.execute('SELECT id FROM runtime_proof ORDER BY id').fetchall() == [(1,), (2,)]
            assert (plan.project / 'storage/restricted.txt').read_text() == 'private fixture-developer'
        print('PASS: Both IDMAP publications removed and physical data preserved', flush=True)
    stop(unit)
