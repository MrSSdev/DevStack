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
from project_lifecycle import LifecycleError, RuntimePair, StartError


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, timeout=30, **kwargs)


def launch(unit, user, args):
    run('systemd-run', '--quiet', '--collect', '--service-type=exec',
        '--unit=' + unit, '--uid=' + user,
        '--property=NoNewPrivileges=yes', '--property=KillMode=control-group',
        '--property=UMask=0007', '--property=TimeoutStopSec=5s',
        '--property=RuntimeMaxSec=180s', *args)


def prepare_public(root, developer, name, other, outside, as_user):
    # Simulate the normal editor adding source/build files and Laravel's
    # published storage. All new entries inherit the prepared named ACLs.
    as_user(developer.pw_name, '/usr/bin/python3', '-I', '-c', '''
from pathlib import Path
import sys
root, name, other, outside = sys.argv[1:]
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
''', str(root), name, str(other), str(outside))

def exercise_http(base, token, plans, users, developer, identities, attempted,
                  as_user, stop, verify, host_net):
    outside = base / 'outside-public.txt'
    outside.write_text('OUTSIDE_FIXTURE_SECRET')
    outside.chmod(0o644)
    # The gateway identity really can read this external canary on the host.
    assert as_user('www-data', '/usr/bin/cat', str(outside),
                   capture_output=True).stdout == 'OUTSIDE_FIXTURE_SECRET'
    routes = {}
    pairs = []
    for index, name in enumerate(('a', 'b')):
        runtime = plans[index]
        user = users[index]
        prepare_public(runtime.project, developer, name,
                       plans[1 - index].project, outside, as_user)
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
        assert pair.start() == 'running'
        # Idempotent start must not replace either transient invocation.
        def invocation(unit):
            return run('systemctl', 'show', '-p', 'InvocationID', '--value', unit,
                       capture_output=True).stdout.strip()
        before = tuple(invocation(unit) for unit in pair.units)
        assert all(before)
        assert pair.start() == 'running'
        assert tuple(invocation(unit) for unit in pair.units) == before
        pairs.append(pair)
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
        print('PASS: HTTPS, PHP routing, static assets and published storage', name, flush=True)
        print('PASS: Host/sibling/private symlinks, hidden and PHP source denied', name, flush=True)

    a, b = pairs
    assert a.stop() == 'stopped'
    assert a.stop() == 'stopped'
    assert a.status() == 'stopped' and b.status() == 'running'
    assert request('a.fixture.test', '/build/assets/app.js')[0] == 502
    assert request('b.fixture.test', '/build/assets/app.js')[0] == 200
    print('PASS: Ingress fails closed and other project remains available', flush=True)
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
        assert request('a.fixture.test', '/')[0] == 502
        assert request('b.fixture.test', '/build/assets/app.js')[0] == 200
    finally:
        config.write_text(original)
    print('PASS: Real partial startup rolled back; sibling remains running', flush=True)
    assert a.start() == 'running'
    status, body, _ = request('a.fixture.test', '/after-rollback')
    assert status == 200 and json.loads(body)['sqlite_count'] == 4, (status, body)
    assert a.restart() == 'running'
    status, body, _ = request('a.fixture.test', '/after-restart')
    assert status == 200 and json.loads(body)['sqlite_count'] == 5, (status, body)
    assert b.status() == 'running'
    verify(plans[1], 'b', identities[1], host_net, 3)
    print('PASS: Pair restart preserves SQLite data and independent project', flush=True)
    assert a.stop() == 'stopped'
    assert b.stop() == 'stopped'
    stop(unit)
