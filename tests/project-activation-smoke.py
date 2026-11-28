"""Real acceptance of the installed isolated HTTPS and Mailpit integration."""
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import time
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from playwright.sync_api import expect, sync_playwright

SLUG = 'isolation-livewire'
SIBLING = 'isolation-sibling'
BASE = f'https://{SLUG}.test'
PROJECTS = Path.home() / 'projects'
PROJECT = PROJECTS / SLUG
REGISTRY = '/var/lib/devstack/projects.json'
PILOT = '/opt/devstack/daemon/project_pilot.py'
SUBJECT = 'DevStack isolated smoke ' + secrets.token_hex(8)
CA = '/usr/local/share/ca-certificates/devstack-activation.crt'

RPC_CLIENT = '''
import socket, sys
with socket.socket(socket.AF_UNIX) as conn:
    conn.settimeout(35)
    conn.connect('/run/devstack/control.sock')
    conn.sendall(sys.stdin.buffer.read() + b'\\n')
    sys.stdout.buffer.write(conn.makefile('rb').readline())
'''

BLADE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta name="csrf-token" content="{{ csrf_token() }}">
    @vite(['resources/css/app.css', 'resources/js/app.js'])
    @fluxAppearance
    @livewireStyles
</head>
<body>
    <h1>Isolation acceptance</h1>
    <livewire:isolation-counter />
    @livewireScripts
    @fluxScripts
</body>
</html>
'''

LIVEWIRE = r'''<?php

namespace App\Livewire;

use Illuminate\Support\Facades\DB;
use Livewire\Component;

class IsolationCounter extends Component
{
    public string $proof = 'waiting';

    public function increment(): void
    {
        abort_unless(session('isolated-proof') === 'session-persisted', 409);
        DB::statement('CREATE TABLE IF NOT EXISTS isolation_proof (n INTEGER)');
        DB::table('isolation_proof')->insert(['n' => 7]);
        $this->proof = 'incremented-' . DB::table('isolation_proof')->sum('n');
    }

    public function render()
    {
        return view('livewire.isolation-counter');
    }
}
'''

ROUTES = r'''

\Illuminate\Support\Facades\Route::get('/isolation-proof', function () {
    session(['isolated-proof' => 'session-persisted']);
    return view('isolation-proof');
});

\Illuminate\Support\Facades\Route::post('/isolation-upload', function (\Illuminate\Http\Request $request) {
    abort_unless(session('isolated-proof') === 'session-persisted', 409);
    return response()->json(['file' => $request->file('upload')->storeAs('', 'fixture.txt', 'public')]);
});

\Illuminate\Support\Facades\Route::get('/isolation-probe', function (\Illuminate\Http\Request $request) {
    $reachable = [];
    foreach ([80, 443, 3306, 6379, 8025] as $port) {
        $connection = @fsockopen('127.0.0.1', $port, $errno, $error, 0.3);
        $reachable[$port] = (bool) $connection;
        if ($connection) fclose($connection);
    }
    return response()->json([
        'uid' => posix_geteuid(),
        'request_uri' => $_SERVER['REQUEST_URI'],
        'scheme' => $request->getScheme(),
        'host' => $request->getHost(),
        'forwarded' => $request->header('X-Forwarded-For'),
        'reachable' => $reachable,
        'sibling' => file_exists(dirname(base_path()) . '/isolation-sibling/public/index.php'),
        'control' => file_exists('/run/devstack/control.sock'),
        'writable_source' => is_writable(base_path('routes/web.php')),
    ], 200, ['X-Accel-Redirect' => '/isolation-hidden']);
});

\Illuminate\Support\Facades\Route::get('/isolation-mail', function (\Illuminate\Http\Request $request) {
    abort_unless(config('mail.default') === 'smtp' && config('mail.mailers.smtp.host') === '127.0.0.1'
        && (int) config('mail.mailers.smtp.port') === 1025, 409);
    $subject = $request->query('subject');
    \Illuminate\Support\Facades\Mail::raw('Laravel over private SMTP', function ($message) use ($subject) {
        $message->to('isolation@example.test')->subject($subject . ' Laravel');
    });
    return response()->json(['sendmail' => mail('isolation@example.test', $subject . ' PHP',
        'PHP mail over private SMTP', 'From: hello@isolation-livewire.test')]);
});

\Illuminate\Support\Facades\Route::get('/isolation-smtp-state', function () {
    $connection = @fsockopen('127.0.0.1', 1025, $errno, $error, 2);
    if (!$connection) return 'unavailable';
    stream_set_timeout($connection, 2);
    $banner = fgets($connection);
    fclose($connection);
    return str_starts_with((string) $banner, '220 ') ? 'ready' : 'unavailable';
});
'''

SIBLING_PHP = r'''<?php
$client = @fsockopen('127.0.0.1', 1025, $errno, $error, 1);
echo json_encode(['uid' => posix_geteuid(), 'smtp' => (bool) $client]);
'''


def run(*args, check=True, timeout=90, **kwargs):
    return subprocess.run(args, text=True, capture_output=True, check=check,
                          timeout=timeout, **kwargs)


def until(call, *, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = call()
        if result:
            return result
        time.sleep(0.25)
    raise AssertionError('Expected state did not become ready')


def rpc(op, *, success=True, **payload):
    result = run('sudo', '-u', 'devstackweb', '/usr/bin/python3', '-I', '-c', RPC_CLIENT,
                 input=json.dumps({'op': op, 'data': payload}))
    reply = json.loads(result.stdout)
    assert reply['ok'] == success, reply
    return reply['result'] if success else reply['error']


def pilot(action, slug=SLUG, *, success=True):
    result = run('sudo', '/usr/bin/python3', '-I', PILOT, action, slug, check=False, timeout=120)
    assert (result.returncode == 0) == success, (action, result.stdout, result.stderr)
    return json.loads(result.stdout) if success else result.stderr


def registry():
    return json.loads(run('sudo', 'cat', REGISTRY).stdout)


def read_status(path='/', *, slug=SLUG, headers=()):
    args = ['curl', '--silent', '--show-error', '--noproxy', '*', '--cacert', CA,
            '--max-time', '12', '-w', '\n%{http_code}']
    for header in headers:
        args += ['-H', header]
    response = run(*args, f'https://{slug}.test{path}').stdout
    body, status = response.rsplit('\n', 1)
    return int(status), body


def http(path='/', *, slug=SLUG, headers=()):
    code, text = read_status(path, slug=slug, headers=headers)
    assert code == 200, (path, code, text[-3000:])
    return text


def mailpit(path='/messages', *, ids=None):
    data = json.dumps({'IDs': ids}).encode() if ids is not None else None
    request = Request('http://127.0.0.1:8025/api/v1' + path, data=data,
                      method='DELETE' if ids is not None else 'GET',
                      headers={'Content-Type': 'application/json'})
    with urlopen(request, timeout=5) as response:
        body = response.read()
    return json.loads(body) if body else None


def test_messages():
    return [message for message in mailpit()['messages'] or []
            if message['Subject'] in (SUBJECT + ' Laravel', SUBJECT + ' PHP')]


def install_routes():
    with (PROJECT / 'routes/web.php').open('a') as stream:
        stream.write(ROUTES)
    (PROJECT / 'app/Livewire').mkdir(exist_ok=True)
    (PROJECT / 'app/Livewire/IsolationCounter.php').write_text(LIVEWIRE)
    (PROJECT / 'resources/views/isolation-proof.blade.php').write_text(BLADE)
    views = PROJECT / 'resources/views/livewire'
    views.mkdir(exist_ok=True)
    (views / 'isolation-counter.blade.php').write_text('''
        <div><flux:button wire:click="increment">Increment</flux:button>
        <span id="isolation-count">{{ $proof }}</span></div>
    ''')
    (PROJECT / 'storage/app/public').mkdir(exist_ok=True, parents=True)
    if not (PROJECT / 'public/storage').is_symlink():
        run('php', 'artisan', 'storage:link', cwd=PROJECT)


def browser_acceptance():
    with sync_playwright() as play:
        browser = play.chromium.launch()
        page = browser.new_page()
        page.context.route(re.compile(r'^https?://(?!isolation-livewire\.test(?:/|$))'),
                           lambda route: route.abort())
        assert page.goto(BASE).ok
        responses = []
        page.on('response', responses.append)
        assert page.goto(BASE + '/isolation-proof').ok
        expect(page.get_by_role('heading', name='Isolation acceptance')).to_be_visible()
        with page.expect_response(lambda r: r.request.method == 'POST'
                                  and 'livewire' in urlparse(r.url).path):
            page.get_by_role('button', name='Increment').click()
        expect(page.locator('#isolation-count')).to_have_text('incremented-7')
        assert any(cookie['name'].endswith('_session') for cookie in page.context.cookies())
        assert any('/build/assets/' in response.url and response.ok for response in responses)
        assert any('/flux/' in response.url and '.js' in urlparse(response.url).path
                   and response.ok for response in responses)
        token = page.locator('meta[name=csrf-token]').get_attribute('content')
        upload = page.request.post(BASE + '/isolation-upload',
            headers={'X-CSRF-TOKEN': token},
            multipart={'upload': {'name': 'proof.txt', 'mimeType': 'text/plain', 'buffer': b'upload-proof'}})
        assert upload.ok, upload.text()
        assert upload.json() == {'file': 'fixture.txt'}
        assert page.request.get(BASE + '/storage/fixture.txt').body() == b'upload-proof'
        browser.close()


def create(slug, *, laravel=False):
    return rpc('create', slug=slug, domain=slug + '.test', name=slug, webroot='public',
               database=False, https=True, local_mail=laravel, engine='laravel' if laravel else 'none',
               starter_kit='livewire' if laravel else 'none', installer_options={})


def delete(slug):
    rpc('delete_project', slug=slug, confirmation=slug, delete_database=False)


def wait_manager():
    until(lambda: read_status('/api/session', slug='devstack')[0] == 200)
    # The static dashboard may be available before the helper socket.
    def ready():
        try:
            rpc('status')
            return True
        except (subprocess.SubprocessError, AssertionError, json.JSONDecodeError):
            return False
    until(ready)


def update_and_rollback():
    source = PROJECTS / 'devstack-source'
    wait_manager()
    worker = source / 'scripts/notifications-worker.php'
    original = worker.read_bytes()
    try:
        # Valid PHP, failed real systemd health check after the staged swap.
        worker.write_text('<?php exit(23);\n')
        failure = run('sudo', 'bash', 'scripts/apply-update.sh', cwd=source, check=False, timeout=120)
        assert failure.returncode != 0 and 'restoring previous' in failure.stderr, failure
    finally:
        worker.write_bytes(original)
    wait_manager()
    assert json.loads(http('/isolation-probe'))['uid'] != os.getuid()
    assert run('sudo', 'cat', '/opt/devstack/scripts/notifications-worker.php').stdout.encode() == original
    result = run('sudo', 'bash', 'scripts/apply-update.sh', cwd=source, check=False, timeout=120)
    assert result.returncode == 0, (result.stdout, result.stderr)
    wait_manager()
    assert json.loads(http('/isolation-probe'))['uid'] != os.getuid()
    run('systemctl', 'is-active', '--quiet', 'devstack-notifications.timer')


def main():
    assert os.environ.get('GITHUB_ACTIONS') == 'true' and os.getuid() != 0
    for port in (80, 443, 3306, 6379, 8025, 1025):
        with socket.create_connection(('127.0.0.1', port), timeout=3):
            pass
    create(SIBLING)
    sibling = PROJECTS / SIBLING
    (sibling / 'public/index.php').write_text(SIBLING_PHP)
    for path in ('storage/app/public', 'database', 'bootstrap/cache'):
        (sibling / path).mkdir(parents=True)
    assert json.loads(until(lambda: (lambda r: r[1] if r[0] == 200 else '')(
        read_status(slug=SIBLING)))) == {'uid': os.getuid(), 'smtp': True}

    create(SLUG, laravel=True)
    def scaffolded():
        result = registry().get(SLUG)
        if result and result.get('engine_status') == 'ready':
            return result
        job = json.loads(run('sudo', 'cat', '/var/lib/devstack/engine-job.json').stdout)
        if job['state'] == 'failed':
            log = job.get('log')
            if log:
                print((Path.home() / '.cache/devstack/engines' / log).read_text()[-7000:])
            raise AssertionError(job['message'])
        return None
    original = until(scaffolded, timeout=650)
    key = original['project_id']
    legacy_pool = Path(f'/etc/php/8.5/fpm/pool.d/devstack-{SLUG}.conf')
    legacy_socket = Path(f'/run/php/devstack-{SLUG}.sock')
    available = Path(f'/etc/nginx/sites-available/devstack-{SLUG}.conf')
    protected_vhost = f'/var/lib/devstack/isolated-{key}.conf'
    run_dirs = [Path(f'/run/devstack-pilot-{key}-{kind}') for kind in ('php', 'http')]
    certificate = f'/etc/devstack/certs/{SLUG}.pem'
    cert = run('sudo', 'sha256sum', certificate).stdout
    env = (PROJECT / '.env').read_bytes()
    assert all(value in env for value in (b'MAIL_MAILER=smtp', b'MAIL_HOST=127.0.0.1', b'MAIL_PORT=1025'))
    install_routes()
    run('php', 'artisan', 'migrate:status', '--no-interaction', cwd=PROJECT)
    run('composer', 'check-platform-reqs', cwd=PROJECT)
    acl_paths = [str(PROJECT / name) for name in ('', 'public', 'storage', 'database')]
    acl = run('getfacl', '-p', *acl_paths).stdout

    assert 'isolation_id' not in original and legacy_pool.exists()
    assert pilot('status')['status'] == 'not-enrolled'
    assert json.loads(http('/isolation-probe'))['uid'] == os.getuid()
    ordinary_config = run('sudo', 'cat', str(available)).stdout
    rpc('update_config', slug=SLUG, rules='client_max_body_size 32m;')
    assert 'HTTPS/public' in pilot('activate', success=False)
    rpc('update_full_config', slug=SLUG, full_config=ordinary_config)
    # Real failure during systemd PHP preparation. The legacy route and pool
    # remain operational because the isolated cutover has not begun.
    required_cache = PROJECT / 'bootstrap/cache'
    displaced = required_cache.with_name('cache-displaced')
    required_cache.rename(displaced)
    try:
        pilot('activate', success=False)
    finally:
        displaced.rename(required_cache)
    assert registry()[SLUG] == original
    assert legacy_pool.exists() and not available.is_symlink()
    assert all(not path.exists() for path in run_dirs)
    assert json.loads(http('/isolation-probe'))['uid'] == os.getuid()

    assert pilot('activate')['isolated_https']
    assert pilot('activate')['project_id'] == key
    assert pilot('start')['status'] == 'running'
    assert not legacy_pool.exists() and not legacy_socket.exists()
    assert available.readlink() == Path(protected_vhost)
    text = run('sudo', 'cat', protected_vhost).stdout
    assert f'proxy_pass http://unix:{run_dirs[1]}/sockets/http.sock;' in text
    assert 'fastcgi_pass' not in text and 'root ' not in text
    redirect = run('curl', '-sSI', '--noproxy', '*', f'http://{SLUG}.test/isolation-probe?x=1').stdout
    assert f'Location: {BASE}/isolation-probe?x=1' in redirect
    probe = json.loads(http('/isolation-probe?value=a%2Fb', headers=('X-Forwarded-For: 198.51.100.99',
                      'X-Forwarded-Proto: http', 'X-Forwarded-Host: forged.test')))
    assert probe['uid'] != os.getuid()
    assert probe['request_uri'] == '/isolation-probe?value=a%2Fb'
    assert (probe['scheme'], probe['host'], probe['forwarded']) == ('https', SLUG + '.test', '127.0.0.1')
    assert not any(probe['reachable'].values()), probe
    assert not any(probe[name] for name in ('sibling', 'control', 'writable_source')), probe
    assert 'izolowanego HTTPS' in rpc('update_config', success=False, slug=SLUG, rules='')
    assert 'izolowanego HTTPS' in rpc('update_full_config', success=False, slug=SLUG, full_config=ordinary_config)

    browser_acceptance()
    assert (PROJECT / 'storage/app/public/fixture.txt').stat().st_uid == os.getuid()
    run('php', '-r', '''
$db = new PDO("sqlite:database/database.sqlite");
if ((int)$db->query("SELECT sum(n) FROM isolation_proof")->fetchColumn() !== 7) exit(1);
$db->exec("INSERT INTO isolation_proof VALUES (3)");
''', cwd=PROJECT)
    assert (PROJECT / '.env').read_bytes() == env
    assert run('getfacl', '-p', *acl_paths).stdout == acl
    assert run('sudo', 'sha256sum', certificate).stdout == cert
    print('PASS: HTTPS, Laravel, Livewire, Flux/Vite, session, upload, SQLite and restricted host access', flush=True)

    from urllib.parse import quote
    assert until(lambda: http('/isolation-smtp-state') == 'ready')
    try:
        assert json.loads(http('/isolation-mail?subject=' + quote(SUBJECT)))['sendmail']
        delivered = until(lambda: (messages if len(messages := test_messages()) == 2 else None))
        assert all(item['From']['Address'] == 'hello@isolation-livewire.test' for item in delivered)
        assert len({item['ID'] for item in delivered}) == 2
    finally:
        ids = [item['ID'] for item in test_messages()]
        if ids:
            mailpit(ids=ids)
    host_smtp = run('sudo', 'ss', '-Hlnpt', 'sport', '=', ':1025').stdout
    assert len(host_smtp.splitlines()) == 1 and '127.0.0.1:1025' in host_smtp and 'mailpit' in host_smtp
    run('sudo', 'systemctl', 'stop', 'devstack-mailpit.service')
    try:
        pilot('restart')
        assert http('/isolation-smtp-state') == 'unavailable'
        assert run('systemctl', 'is-active', 'devstack-mailpit', check=False).stdout.strip() == 'inactive'
    finally:
        run('sudo', 'systemctl', 'start', 'devstack-mailpit.service')
    until(lambda: http('/isolation-smtp-state') == 'ready')
    assert 'messages' in mailpit()
    print('PASS: Laravel SMTP and PHP mail use the shared Mailpit; stopped service stays stopped', flush=True)

    # Failure of the actual FPM unit leaves the isolated vhost in place.
    run('sudo', 'systemctl', 'stop', run_dirs[0].name + '.service')
    assert read_status('/isolation-probe')[0] in (502, 503, 504)
    assert not legacy_socket.exists()
    assert json.loads(http(slug=SIBLING))['uid'] == os.getuid()
    pilot('stop')
    assert all(not path.exists() for path in run_dirs)
    identity_path = f'/var/lib/devstack/pilot-{key}/identity-p.json'
    identity = run('sudo', 'cat', identity_path).stdout
    changed = json.loads(identity)
    changed['uid'] += 1
    try:
        run('sudo', 'tee', identity_path, input=json.dumps(changed))
        pilot('start', success=False)
        assert read_status('/isolation-probe')[0] in (502, 503, 504)
    finally:
        run('sudo', 'tee', identity_path, input=identity)

    # Files in /run and the transient units are gone, as after WSL exits.
    # Also reproduce a known interrupted transaction on disk.
    run('sudo', 'rm', str(available))
    run('sudo', 'tee', str(available), input=ordinary_config)
    assert pilot('activate', success=False)
    run('sudo', 'systemctl', 'restart', 'devstackd')
    wait_manager()
    assert available.is_symlink()
    assert json.loads(http('/isolation-probe'))['uid'] == probe['uid']
    assert all(str(path) + '/' not in Path('/proc/self/mountinfo').read_text() for path in run_dirs)
    update_and_rollback()

    # A project with no local-mail opt-in has neither an SMTP listener nor
    # access to another registration's transport.
    assert pilot('activate', SIBLING)['isolated_https']
    assert json.loads(http(slug=SIBLING))['smtp'] is False
    sibling_key = registry()[SIBLING]['project_id']
    assert not Path(f'/run/devstack-pilot-{sibling_key}-php/smtp').exists()
    assert pilot('deactivate', SIBLING)['status'] == 'ordinary'
    assert until(lambda: read_status(slug=SIBLING)[0] == 200)

    # Explicit return to ordinary, then a fresh activation and managed delete.
    assert pilot('deactivate')['status'] == 'ordinary'
    assert 'isolation_id' not in registry()[SLUG]
    assert not available.is_symlink() and legacy_pool.exists()
    assert until(lambda: read_status('/isolation-probe')[0] == 200)
    assert json.loads(http('/isolation-probe'))['uid'] == os.getuid()
    assert pilot('activate')['isolated_https']
    delete(SLUG)
    assert SLUG not in registry() and not available.exists() and not legacy_pool.exists()
    assert all(not path.exists() for path in run_dirs)
    assert (PROJECT / '.env').read_bytes() == env
    assert (PROJECT / 'database/database.sqlite').is_file()
    create(SLUG)
    assert registry()[SLUG]['project_id'] != key and 'isolation_id' not in registry()[SLUG]
    assert until(lambda: read_status('/isolation-probe')[0] == 200)
    assert json.loads(http('/isolation-probe'))['uid'] == os.getuid()
    print('PASS: fail-closed failure, identity/drift recovery, real update/rollback, deactivation and delete/recreate',
          flush=True)


if __name__ == '__main__':
    main()
