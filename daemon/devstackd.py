#!/usr/bin/env python3
"""DevStack's root-owned local Unix-socket helper.

The browser and PHP cannot submit shell commands, paths or arbitrary Nginx rules.
Supported operations are intentionally small and typed.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import queue
import threading
import pwd
import re
import secrets
import socket
import struct
import subprocess
import sys
import tempfile
from typing import Any

CFG_FILE = pathlib.Path('/etc/devstack/server.json')
STATE_FILE = pathlib.Path('/var/lib/devstack/projects.json')
SECRET_FILE = pathlib.Path('/var/lib/devstack/credentials.json')
PREFERENCES_FILE = pathlib.Path('/var/lib/devstack/ui-preferences.json')
CA_PUBLIC_FILE = pathlib.Path('/var/lib/devstack/ca/rootCA.pem')  # Public cert ONLY; never expose rootCA-key.pem.
SOCKET_FILE = pathlib.Path('/run/devstack/control.sock')
ENGINE_JOB_FILE = pathlib.Path('/var/lib/devstack/engine-job.json')
ENGINE_RUNNER = '/opt/devstack/engines/runner.py'
ENGINE_CATALOG_FILE = pathlib.Path(__file__).resolve().parents[1] / 'engines/catalog.json'
ENGINE_RESULTS = queue.SimpleQueue()
ENGINE_ACTIVE = {'scaffolding', 'configuring', 'migrating', 'checking', 'installing', 'updating'}
ENGINE_VERSION = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+\Z')
ENGINE_UNIT = re.compile(r'devstack-engine-[0-9a-f]{32}-[a-z-]+\Z')
WINDOWS_SCHTASKS = pathlib.Path('/mnt/c/Windows/System32/schtasks.exe')
WINDOWS_HOSTS_TASK = 'DevStack Hosts Event Sync'
SLUG = re.compile(r'[a-z][a-z0-9-]{1,39}\Z')
DOMAIN = re.compile(r'[a-z][a-z0-9-]{0,39}\.test\Z')
WEBROOT = re.compile(r'[a-zA-Z0-9_][a-zA-Z0-9_/-]{0,99}\Z')
LOCATION_START = '        # devstack:location:start\n'
LOCATION_END = '        # devstack:location:end\n'
EXTRA_START = '    # devstack:asset-locations:start\n'
EXTRA_END = '    # devstack:asset-locations:end\n'
# Asset-only regex routes. This is not an arbitrary Nginx configuration editor.
# Route must be anchored inside a named directory and end in .js or .css.
ASSET_ROUTE = re.compile(
    r'\^/[a-z][a-z0-9-]{0,39}/'
    r'(?:[a-z][a-z0-9-]*|\([a-z][a-z0-9-]*(?:\|[a-z][a-z0-9-]*)+\))'
    r'(?:\(\\\.min\)\?)?'
    r'\\\.(?:js|css|\((?:js\|css|css\|js)\))'
    r'\$\Z'
)
ASSET_BLOCK = re.compile(r'\s*location\s+~\*\s+(\S+)\s*\{\s*(.*?)\s*\}', re.DOTALL)
ASSET_DIRECTIVES = re.compile(
    r'(?:expires\s+off\s*;\s*)?'
    r'try_files\s+\$uri\s+\$uri/\s+/index\.php\?\$query_string\s*;'
)

# Only a limited set of directives is permitted in location /.
OVERRIDE_RULES = {
    'client_max_body_size': re.compile(r'(?:[1-9][0-9]{0,2})[mM]\Z'),
    'client_body_timeout': re.compile(r'(?:[1-9][0-9]{0,2})s\Z'),
    'send_timeout': re.compile(r'(?:[1-9][0-9]{0,2})s\Z'),
}


class InputError(Exception):
    pass


def ensure_string(value: Any, *, limit: int = 100) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise InputError('Nieprawidlowa lub zbyt dluga wartosc pola.')
    return value


def check_slug(value: Any) -> str:
    text = ensure_string(value, limit=40)
    if not SLUG.fullmatch(text):
        raise InputError('Id projektu: 2-40 znakow, male litery, cyfry i myslniki.')
    return text


def check_domain(value: Any) -> str:
    text = ensure_string(value, limit=45)
    if not DOMAIN.fullmatch(text) or text == 'devstack.test':
        raise InputError('Domena musi miec format projekt.test (poza devstack.test).')
    return text


def check_webroot(value: Any) -> str:
    text = ensure_string(value, limit=100)
    parts = pathlib.PurePosixPath(text).parts
    if not WEBROOT.fullmatch(text) or text.startswith('/') or '//' in text or any(p in {'.', '..'} for p in parts):
        raise InputError('Katalog publiczny: tylko wzgledna sciezka bez ../ lub linkow.')
    return text


def check_name(value: Any) -> str:
    text = ensure_string(value, limit=70).strip()
    if not text or any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise InputError('Nazwa projektu jest nieprawidlowa.')
    return text


def check_overrides(value: Any) -> str:
    """Parse a tiny allowlist of server-level tuning, not raw Nginx syntax."""
    if not isinstance(value, str) or len(value) > 1800 or '\x00' in value:
        raise InputError('Konfiguracja musi byc tekstem do 1800 znakow.')
    clean = []
    seen = set()
    for raw in value.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'([a-z_]+)\s+([^\s;]+);', line)
        if match is None or match.group(1) not in OVERRIDE_RULES:
            raise InputError('W location / dozwolone sa tylko: client_max_body_size, client_body_timeout i send_timeout.')
        name, val = match.groups()
        if name in seen or not OVERRIDE_RULES[name].fullmatch(val):
            raise InputError('Nieprawidlowa lub powtorzona dyrektywa: ' + name)
        seen.add(name)
        clean.append(f'{name} {val};')
    return '\n'.join(clean)


def check_extra_locations(value: Any) -> str:
    """Normalize a small set of asset-only regex locations, e.g. Flux.

    Never allow arbitrary directives such as alias, root, proxy_pass,
    fastcgi_pass, rewrite or nested location blocks.
    """
    if not isinstance(value, str) or len(value) > 4000 or '\x00' in value:
        raise InputError('Bloki location: wymagany tekst do 4000 znakow.')
    position = 0
    normalized = []
    seen = set()
    while position < len(value):
        if not value[position:].strip():
            break
        match = ASSET_BLOCK.match(value, position)
        if not match:
            raise InputError('Dozwolone sa tylko osobne bloki: location ~* ^/pakiet/zasob\\.(js|css)$ { ... }.')
        route, body = match.groups()
        if not ASSET_ROUTE.fullmatch(route):
            raise InputError('Regula location musi dotyczyc konkretnych zasobow JS/CSS w katalogu pakietu.')
        if not ASSET_DIRECTIVES.fullmatch(body.strip()):
            raise InputError('W bloku dozwolone sa tylko: expires off; oraz try_files $uri $uri/ /index.php?$query_string;')
        if route in seen or len(normalized) >= 6:
            raise InputError('Powtorzony location albo za duzo blokow (maksymalnie 6).')
        seen.add(route)
        expires = bool(re.match(r'expires\s+off\s*;', body.strip()))
        lines = [f'location ~* {route} {{']
        if expires:
            lines.append('    expires off;')
        lines.append('    try_files $uri $uri/ /index.php?$query_string;')
        lines.append('}')
        normalized.append('\n'.join(lines))
        position = match.end()
    return '\n\n'.join(normalized)


def read_json(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise RuntimeError('Stan DevStack jest nieprawidlowy.')
    return data


def write_json_atomic(path: pathlib.Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.devstack-', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=True, sort_keys=True, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def run(argv: list[str], input_text: str | None = None, env: dict[str, str] | None = None) -> str:
    try:
        result = subprocess.run(
            argv, input=input_text, capture_output=True, text=True, env=env,
            timeout=45, check=True, close_fds=True,
        )
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        raise RuntimeError('Operacja systemowa przekroczyla limit czasu.') from None
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or 'Operacja systemowa nie powiodla sie.').strip()
        raise RuntimeError(detail[-1400:]) from None


def request_windows_hosts_sync() -> str:
    """Ask the preinstalled Windows task to reconcile hosts; never run code from a project.

    This is a fixed, no-argument signal. Windows keeps the privileged sync logic
    and hosts-file validation in its existing protected PowerShell scripts.
    A missing Windows interop executable is normal outside WSL.
    """
    if not WINDOWS_SCHTASKS.is_file():
        return ''
    try:
        run([str(WINDOWS_SCHTASKS), '/Run', '/TN', WINDOWS_HOSTS_TASK])
        return ''
    except RuntimeError as exc:
        print('Warning: DevStack Windows hosts task could not be triggered: ' + str(exc), file=sys.stderr)
        return ' Uwaga: nie udalo sie zlecic synchronizacji Windows hosts; sprawdz zadanie DevStack w Windows.'


def config() -> dict[str, Any]:
    cfg = read_json(CFG_FILE)
    name = cfg.get('dev_user')
    if not isinstance(name, str) or not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', name):
        raise RuntimeError('Brak poprawnej konfiguracji dev_user.')
    expected = pathlib.Path('/home') / name / 'projects'
    if cfg.get('projects_dir') != str(expected):
        raise RuntimeError('Nieprawidlowy katalog projektow.')
    return cfg


def service_status() -> dict[str, str]:
    states = {}
    for service in ('nginx', 'php8.5-fpm', 'mariadb', 'redis-server'):
        try:
            res = subprocess.run(['/usr/bin/systemctl', 'is-active', service], capture_output=True, text=True, timeout=5)
            states[service] = res.stdout.strip()[:30] or 'unknown'
        except subprocess.TimeoutExpired:
            states[service] = 'timeout'
    return states


def site_path(slug: str) -> pathlib.Path:
    return pathlib.Path('/etc/nginx/sites-available') / f'devstack-{slug}.conf'


def site_link_path(slug: str) -> pathlib.Path:
    return pathlib.Path('/etc/nginx/sites-enabled') / f'devstack-{slug}.conf'


def pool_path(slug: str) -> pathlib.Path:
    return pathlib.Path('/etc/php/8.5/fpm/pool.d') / f'devstack-{slug}.conf'


def defer_php_fpm_reload() -> None:
    """Schedule reload after the PHP request completes to avoid Nginx 502.

    systemd's transient timer runs independently of the current FastCGI request.
    The unit name is random and contains no user-controlled input.
    """
    unit = 'devstack-fpm-reload-' + secrets.token_hex(8)
    run(['/usr/bin/systemd-run', '--quiet', '--collect', '--on-active=8s',
         '--timer-property=AccuracySec=1s', '--unit=' + unit,
         '/usr/bin/systemctl', 'reload', 'php8.5-fpm'])


def public_projects() -> list[dict[str, Any]]:
    output = []
    for slug, p in sorted(read_json(STATE_FILE).items()):
        if not SLUG.fullmatch(slug) or not isinstance(p, dict):
            continue
        item = dict(p)
        item['slug'] = slug
        path = site_path(slug)
        item['nginx_config'] = path.read_text(encoding='utf-8') if path.is_file() else ''
        output.append(item)
    return output


def get_ui_preferences() -> dict[str, str]:
    """One shared UI theme for the local DevStack installation."""
    stored = read_json(PREFERENCES_FILE)
    theme = stored.get('theme', 'light')
    if theme not in ('light', 'dark') or not isinstance(theme, str):
        raise RuntimeError('Nieprawidlowe ustawienia interfejsu DevStack.')
    return {'theme': theme}


def set_ui_preferences(data: dict[str, Any]) -> dict[str, str]:
    """Only allow an explicit light/dark value, never arbitrary files or commands."""
    if set(data) != {'theme'} or not isinstance(data['theme'], str) or data['theme'] not in ('light', 'dark'):
        raise InputError('Motyw musi miec wartosc light lub dark.')
    result = {'theme': data['theme']}
    write_json_atomic(PREFERENCES_FILE, result)
    return result


def preferences(_: dict[str, Any]) -> dict[str, str]:
    """Read theme only; safe to call during initial HTML document response."""
    return get_ui_preferences()


def status(_: dict[str, Any]) -> dict[str, Any]:
    return {'services': service_status(), 'projects': public_projects(),
            'preferences': get_ui_preferences(), 'engine_job': public_engine_job(),
            'engines': engine_catalog()}



def public_ca_certificate(data: dict[str, Any]) -> dict[str, str]:
    """Return only this installation's public mkcert root certificate.

    Not an arbitrary path read: never expose rootCA-key.pem or private state.
    The Windows trust decision remains a separate interactive admin action.
    """
    if data:
        raise InputError('Ta operacja nie przyjmuje parametrow.')
    if CA_PUBLIC_FILE.is_symlink() or not CA_PUBLIC_FILE.is_file():
        raise RuntimeError('Publiczny certyfikat CA DevStack nie jest dostepny.')
    content = CA_PUBLIC_FILE.read_bytes()
    if not 100 <= len(content) <= 16384:
        raise RuntimeError('Nieprawidlowy rozmiar certyfikatu CA.')
    try:
        pem = content.decode('ascii')
    except UnicodeDecodeError:
        raise RuntimeError('Publiczny certyfikat CA nie jest w formacie PEM.') from None
    if not re.fullmatch(
        r'-----BEGIN CERTIFICATE-----\r?\n(?:[A-Za-z0-9+/=]{1,76}\r?\n)+'
        r'-----END CERTIFICATE-----\r?\n?', pem
    ):
        raise RuntimeError('Nieprawidlowy format publicznego certyfikatu CA.')
    return {'pem': pem, 'sha256': hashlib.sha256(content).hexdigest().upper()}



def mkdir_as_developer(path: pathlib.Path, dev_user: str) -> None:
    """Ask systemd (PID 1) to create a folder as the validated developer.

    The root helper runs with NoNewPrivileges=yes, so setuid in a child
    via runuser may fail with EPERM. A short-lived systemd service gets
    its User= identity BEFORE applying NoNewPrivileges to /usr/bin/mkdir.
    The privileged helper never writes into the developer tree as root.
    """
    pwd.getpwnam(dev_user)  # Fail if the configured developer does not exist.
    unit = 'devstack-mkdir-' + secrets.token_hex(8)
    run(['/usr/bin/systemd-run', '--quiet', '--wait', '--pipe',
         '--collect', '--service-type=exec',
         '--property=NoNewPrivileges=yes',
         '--uid=' + dev_user, '--unit=' + unit,
         '/usr/bin/mkdir', '--', str(path)])


def ensure_project_paths(root: pathlib.Path, webroot: str, dev_user: str) -> pathlib.Path:
    base = pathlib.Path('/home') / dev_user / 'projects'
    if base.is_symlink() or not base.is_dir():
        raise InputError('Katalog ~/projects musi juz istniec, bez linkow symbolicznych.')
    if root.parent != base or root.is_symlink() or (root.exists() and not root.is_dir()):
        raise InputError('Projekt musi byc katalogiem bezposrednio w ~/projects.')
    # Mutations inside user-controlled directories run *as the developer*, never as root.
    pwd.getpwnam(dev_user)
    if not root.exists():
        mkdir_as_developer(root, dev_user)
    public = root
    for part in pathlib.PurePosixPath(webroot).parts:
        public = public / part
        if public.is_symlink() or (public.exists() and not public.is_dir()):
            raise InputError('Nie mozna uzyc linku lub pliku jako folderu publicznego.')
        if not public.exists():
            mkdir_as_developer(public, dev_user)
    if root.resolve() != base.resolve() / root.name or not public.resolve().is_relative_to(root.resolve()):
        raise InputError('Sciezka wychodzi poza ~/projects.')
    return public


def grant_public_access(root: pathlib.Path, public: pathlib.Path, dev_user: str) -> None:
    base = pathlib.Path('/home') / dev_user / 'projects'
    for path in [base.parent, base, root]:
        run(['/usr/bin/setfacl', '-m', 'u:www-data:--x', str(path)])
    run(['/usr/bin/setfacl', '-R', '-m', 'u:www-data:rX', str(public)])
    for folder, dirs, files in os.walk(public, followlinks=False):
        run(['/usr/bin/setfacl', '-d', '-m', 'u:www-data:rX', folder])


def php_pool(slug: str, user: str) -> str:
    return f'''[devstack-{slug}]
user = {user}
group = {user}
listen = /run/php/devstack-{slug}.sock
listen.owner = www-data
listen.group = www-data
listen.mode = 0660
pm = ondemand
pm.max_children = 4
pm.process_idle_timeout = 10s
pm.max_requests = 500
security.limit_extensions = .php
clear_env = yes
php_admin_value[expose_php] = Off
'''


def nginx_conf(slug: str, domain: str, public: pathlib.Path, ssl_enabled: bool,
               overrides: str = '', extra_locations: str = '') -> str:
    """Generate a protected virtual host with two narrowly editable regions."""
    rules = check_overrides(overrides)
    assets = check_extra_locations(extra_locations)
    location_opts = ''.join('        ' + line + '\n' for line in rules.splitlines())
    asset_opts = ''.join('    ' + line + '\n' if line else '\n' for line in assets.splitlines())
    text = f'''# Generated by DevStack. Protected directives are managed by DevStack.
server {{
    listen 127.0.0.1:80;
    server_name {domain};
'''
    if ssl_enabled:
        text += '''    return 308 https://$host$request_uri;
}
'''
        text += f'''server {{
    listen 127.0.0.1:443 ssl;
    server_name {domain};
    ssl_certificate /etc/devstack/certs/{slug}.pem;
    ssl_certificate_key /etc/devstack/certs/{slug}-key.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
'''
    text += f'''    root {public};
    index index.php index.html;
    server_tokens off;
    location / {{
{LOCATION_START}{location_opts}{LOCATION_END}        try_files $uri $uri/ /index.php?$query_string;
    }}
{EXTRA_START}{asset_opts}{EXTRA_END}    location ~ ^/index\\.php$ {{
        include snippets/fastcgi-php.conf;
        fastcgi_pass unix:/run/php/devstack-{slug}.sock;
    }}
    location ~ \\.php$ {{ return 404; }}
    location ~ /\\. {{ deny all; }}
}}
'''
    return text


def check_full_config(raw: Any, slug: str, project: dict[str, Any]) -> dict[str, str]:
    """Allow edits only between the exact two generated protected-region markers."""
    if not isinstance(raw, str) or not raw or len(raw) > 18000 or '\x00' in raw:
        raise InputError('Nieprawidlowy plik Nginx.')
    root = pathlib.Path(project['path']) / project['webroot']
    canonical = nginx_conf(slug, project['domain'], root, project['https'])
    before, remainder = canonical.split(LOCATION_START, 1)
    _, remainder = remainder.split(LOCATION_END, 1)
    static_between, remainder = remainder.split(EXTRA_START, 1)
    _, after = remainder.split(EXTRA_END, 1)
    prefix = before + LOCATION_START
    separator = LOCATION_END + static_between + EXTRA_START
    suffix = EXTRA_END + after
    if not raw.startswith(prefix) or not raw.endswith(suffix):
        raise InputError('Chronione elementy Nginx nie moga byc edytowane. Zmieniaj tylko obszary miedzy znacznikami DevStack.')
    middle = raw[len(prefix):len(raw)-len(suffix)]
    if middle.count(separator) != 1:
        raise InputError('Nieprawidlowa struktura obszarow edycji Nginx.')
    overrides, extra = middle.split(separator, 1)
    return {
        'overrides': check_overrides('\n'.join(line.strip() for line in overrides.splitlines())),
        'extra_locations': check_extra_locations(extra),
    }


def mkcert_for(slug: str, domain: str) -> list[pathlib.Path]:
    cert_dir = pathlib.Path('/etc/devstack/certs')
    ca_dir = pathlib.Path('/var/lib/devstack/ca')
    cert_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    ca_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    cert_file = cert_dir / (slug + '.pem')
    key_file = cert_dir / (slug + '-key.pem')
    if cert_file.exists() or key_file.exists():
        raise InputError('Certyfikat tej nazwy juz istnieje.')
    env = {**os.environ, 'CAROOT': str(ca_dir)}
    run(['/usr/bin/mkcert', '-cert-file', str(cert_file), '-key-file', str(key_file), domain], env=env)
    os.chmod(cert_file, 0o644)
    os.chmod(key_file, 0o600)
    for ca_file in ca_dir.iterdir():
        if ca_file.name.endswith('-key.pem'):
            os.chmod(ca_file, 0o600)
    return [cert_file, key_file]


def db_identifiers(slug: str) -> tuple[str, str]:
    # GRANT treats '_' and '%' as SQL wildcards even inside backticks.
    # Generate names using only letters and numbers to avoid cross-database grants.
    short = slug.replace('-', '')[:21]
    suffix = hashlib.sha256(slug.encode('ascii')).hexdigest()[:8]
    return 'dev' + short + suffix, 'ds' + short + suffix


def mariadb_execute(sql: str) -> None:
    try:
        run(['/usr/bin/mariadb', '--protocol=socket', '--batch', '--skip-column-names'], input_text=sql)
    except RuntimeError:
        # Avoid reporting an SQL statement containing the freshly created password.
        raise RuntimeError('MariaDB nie wykonala operacji. Sprawdz log serwera.') from None


def create_db(slug: str) -> tuple[str, str, str]:
    name, user = db_identifiers(slug)
    password = secrets.token_urlsafe(36)
    mariadb_execute(f'CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;')
    try:
        mariadb_execute(f"CREATE USER '{user}'@'127.0.0.1' IDENTIFIED BY '{password}';")
        mariadb_execute(f"GRANT ALL PRIVILEGES ON `{name}`.* TO '{user}'@'127.0.0.1';")
    except Exception:
        try:
            drop_db(name, user)
        except Exception:
            print('Failed database cleanup for ' + slug, file=sys.stderr)
        raise
    return name, user, password


def drop_db(name: str, user: str) -> None:
    if not re.fullmatch(r'[a-z0-9_]{1,64}', name) or not re.fullmatch(r'[a-z0-9_]{1,64}', user):
        raise InputError('Nieprawidlowe nazwy zasobow MariaDB.')
    # First drop database; a database error must not revoke user access.
    mariadb_execute(f"DROP DATABASE IF EXISTS `{name}`; DROP USER IF EXISTS '{user}'@'127.0.0.1';")


def create_environment(data: dict[str, Any]) -> dict[str, str]:
    cfg = config()
    slug = check_slug(data.get('slug'))
    name = check_name(data.get('name'))
    domain = check_domain(data.get('domain'))
    webroot = check_webroot(data.get('webroot'))
    database = data.get('database') is True
    https = data.get('https') is True
    state = read_json(STATE_FILE)
    secrets_state = read_json(SECRET_FILE)
    if slug in state or any(p.get('domain') == domain for p in state.values()):
        raise InputError('Projekt lub domena juz istnieje.')
    root = pathlib.Path(cfg['projects_dir']) / slug
    site_file = site_path(slug)
    site_link = site_link_path(slug)
    pool_file = pool_path(slug)
    if any(p.exists() or p.is_symlink() for p in [site_file, site_link, pool_file]):
        raise InputError('Istnieja juz pliki konfiguracyjne dla tego projektu.')
    generated: list[pathlib.Path] = []
    db_info = None
    pool_created = site_created = link_created = False
    try:
        public = ensure_project_paths(root, webroot, cfg['dev_user'])
        grant_public_access(root, public, cfg['dev_user'])
        if database:
            db_info = create_db(slug)
        if https:
            generated = mkcert_for(slug, domain)
        pool_file.write_text(php_pool(slug, cfg['dev_user']), encoding='utf-8')
        pool_created = True
        run(['/usr/sbin/php-fpm8.5', '-t'])
        site_file.write_text(nginx_conf(slug, domain, public, https), encoding='utf-8')
        site_created = True
        site_link.symlink_to(site_file)
        link_created = True
        run(['/usr/sbin/nginx', '-t'])
        run(['/usr/bin/systemctl', 'reload', 'nginx'])
        # A synchronous FPM reload kills the live FastCGI request (HTTP 502).
        # Validate now, schedule the actual reload for after the API response.
        defer_php_fpm_reload()
        state[slug] = {
            'name': name, 'domain': domain, 'path': str(root), 'webroot': webroot,
            'https': https, 'database': database,
            'db_name': db_info[0] if db_info else '',
            'db_user': db_info[1] if db_info else '',
            'overrides': '', 'extra_locations': '',
        }
        if data.get('engine', 'none') != 'none':
            state[slug].update(engine=data['engine'], starter_kit=data['starter_kit'], engine_status='configuring')
        if db_info:
            secrets_state[slug] = {'password': db_info[2]}
        write_json_atomic(SECRET_FILE, secrets_state)
        write_json_atomic(STATE_FILE, state)
    except Exception:
        if link_created:
            site_link.unlink(missing_ok=True)
        if site_created:
            site_file.unlink(missing_ok=True)
        if pool_created:
            pool_file.unlink(missing_ok=True)
        for file in generated:
            file.unlink(missing_ok=True)
        if db_info:
            try:
                drop_db(db_info[0], db_info[1])
            except Exception:
                print('Warning: manual database cleanup needed for ' + slug, file=sys.stderr)
        # The failed pool was never reloaded synchronously, so no FPM reload
        # is needed. In particular, do NOT kill the PHP request which must
        # deliver the real error to the UI (instead of an HTTP 502).
        if site_created or link_created:
            try:
                run(['/usr/bin/systemctl', 'reload', 'nginx'])
            except Exception:
                print('Warning: Nginx rollback reload failed', file=sys.stderr)
        raise
    # Request the silent Windows task only after the project metadata is committed.
    hosts_warning = request_windows_hosts_sync()
    return {'message': 'Projekt zapisany. PHP-FPM uruchomi nowa pule za kilka sekund. Jezeli integracja Windows jest skonfigurowana, domena zostanie zsynchronizowana automatycznie.' + hosts_warning}


def test_project_database(data: dict[str, Any]) -> dict[str, str]:
    """Check the project's real MariaDB user via TCP, including write access.

    Passwords are passed only through a short-lived root-owned 0600 option
    file, never through argv, API responses or subprocess error messages.
    The TEMPORARY table disappears when the connection closes.
    """
    if set(data) != {'slug'}:
        raise InputError('Nieprawidlowe dane testu bazy.')
    slug = check_slug(data.get('slug'))
    project = read_json(STATE_FILE).get(slug)
    if not isinstance(project, dict) or project.get('database') is not True:
        raise InputError('Projekt nie posiada zarzadzanej bazy MariaDB.')
    expected_name, expected_user = db_identifiers(slug)
    if project.get('db_name') != expected_name or project.get('db_user') != expected_user:
        raise InputError('Zapisane identyfikatory bazy sa niespojne.')
    credential = read_json(SECRET_FILE).get(slug)
    if not isinstance(credential, dict):
        raise InputError('Brakuje danych polaczenia z baza.')
    password = credential.get('password')
    if not isinstance(password, str) or not re.fullmatch(r'[A-Za-z0-9_-]{40,80}', password):
        raise InputError('Nieprawidlowe dane polaczenia z baza.')

    with tempfile.TemporaryDirectory(prefix='db-check-', dir='/run/devstack') as temp_dir:
        opts = pathlib.Path(temp_dir) / 'client.cnf'
        fd = os.open(opts, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='ascii') as handle:
            handle.write('[client]\nuser=' + expected_user + '\npassword=' + password + '\n')
        sql = ('CREATE TEMPORARY TABLE devstack_connection_check (v INTEGER NOT NULL); '
               'INSERT INTO devstack_connection_check (v) VALUES (1); '
               'SELECT v FROM devstack_connection_check; '
               'DROP TEMPORARY TABLE devstack_connection_check;')
        try:
            result = run(['/usr/bin/mariadb', '--defaults-extra-file=' + str(opts),
                          '--protocol=TCP', '--host=127.0.0.1', '--port=3306',
                          '--database=' + expected_name, '--batch',
                          '--skip-column-names', '--silent'], input_text=sql)
        except RuntimeError:
            raise RuntimeError('Test MariaDB nie powiodl sie. Sprawdz stan uslugi i uprawnienia konta.') from None
    if result.strip() != '1':
        raise RuntimeError('MariaDB nie zwrocila oczekiwanej wartosci testowej.')
    return {'message': 'MariaDB: polaczenie TCP, zapis i odczyt danych dzialaja poprawnie.'}


def delete_project(data: dict[str, Any]) -> dict[str, str]:
    """Remove only managed hosting resources; NEVER delete the source tree.

    A MariaDB project requires explicit acknowledgement of permanent database
    removal. Staged Nginx/PHP configs are restored if a pre-commit step fails.
    """
    ensure_engine_idle()
    if set(data) != {'slug', 'confirmation', 'delete_database'}:
        raise InputError('Nieprawidlowe dane usuwania projektu.')
    slug = check_slug(data.get('slug'))
    if data.get('confirmation') != slug:
        raise InputError('Wpisz dokladny identyfikator projektu, aby potwierdzic usuniecie.')
    delete_database = data.get('delete_database')
    if not isinstance(delete_database, bool):
        raise InputError('Nieprawidlowe potwierdzenie usuniecia bazy.')

    cfg = config()
    state = read_json(STATE_FILE)
    project = state.get(slug)
    if not isinstance(project, dict):
        raise InputError('Nie ma takiego projektu.')
    expected_root = pathlib.Path(cfg['projects_dir']) / slug
    if project.get('path') != str(expected_root):
        raise InputError('Sciezka projektu w stanie DevStack jest nieprawidlowa.')
    check_domain(project.get('domain'))
    check_webroot(project.get('webroot'))
    database = project.get('database') is True
    if database != delete_database:
        if database:
            raise InputError('Aby usunac ten projekt, zaznacz potwierdzenie trwalego usuniecia jego bazy MariaDB.')
        raise InputError('Ten projekt nie ma bazy MariaDB.')

    site_file = site_path(slug)
    site_link = site_link_path(slug)
    pool_file = pool_path(slug)
    if (not site_file.is_file() or site_file.is_symlink()
            or not pool_file.is_file() or pool_file.is_symlink()
            or not site_link.is_symlink() or site_link.readlink() != site_file):
        raise InputError('Niepelna lub zmieniona konfiguracja projektu. Niczego nie usunieto.')

    cert_files = []
    if project.get('https') is True:
        cert_dir = pathlib.Path('/etc/devstack/certs')
        cert_files = [cert_dir / (slug + '.pem'), cert_dir / (slug + '-key.pem')]
        if any(not path.is_file() or path.is_symlink() for path in cert_files):
            raise InputError('Brakuje zarzadzanego certyfikatu projektu. Niczego nie usunieto.')

    secrets_state = read_json(SECRET_FILE)
    if database:
        expected_name, expected_user = db_identifiers(slug)
        if (project.get('db_name') != expected_name
                or project.get('db_user') != expected_user
                or slug not in secrets_state):
            raise InputError('Niespojne dane MariaDB. Niczego nie usunieto.')

    with tempfile.TemporaryDirectory(prefix='.delete-' + slug + '-', dir=STATE_FILE.parent) as work:
        staged = pathlib.Path(work)
        saved_site = staged / 'nginx.conf'
        saved_pool = staged / 'fpm.conf'
        link_removed = False
        db_dropped = False
        original_state = dict(state)
        original_secrets = dict(secrets_state)
        try:
            site_link.unlink()
            link_removed = True
            site_file.rename(saved_site)
            pool_file.rename(saved_pool)
            run(['/usr/sbin/nginx', '-t'])
            run(['/usr/bin/systemctl', 'reload', 'nginx'])

            # A delayed reload avoids interrupting this live API request.
            defer_php_fpm_reload()
            del state[slug]
            secrets_state.pop(slug, None)
            # Complete metadata writes before irreversible SQL removal.
            write_json_atomic(SECRET_FILE, secrets_state)
            write_json_atomic(STATE_FILE, state)

            if database:
                drop_db(expected_name, expected_user)
                db_dropped = True
        except Exception:
            # Roll back the hosting configuration without touching user files.
            if saved_site.is_file():
                saved_site.rename(site_file)
            if saved_pool.is_file():
                saved_pool.rename(pool_file)
            if link_removed and not site_link.exists() and not site_link.is_symlink():
                site_link.symlink_to(site_file)
            if STATE_FILE.exists() and read_json(STATE_FILE) != original_state:
                write_json_atomic(STATE_FILE, original_state)
            if SECRET_FILE.exists() and read_json(SECRET_FILE) != original_secrets:
                write_json_atomic(SECRET_FILE, original_secrets)
            try:
                run(['/usr/sbin/nginx', '-t'])
                run(['/usr/bin/systemctl', 'reload', 'nginx'])
            except Exception:
                print('Critical: DevStack deletion rollback needs manual Nginx recovery', file=sys.stderr)
            if db_dropped:
                print('Critical: MariaDB was removed before metadata rollback; restore a database backup.', file=sys.stderr)
            raise

    # Remove generated TLS files only AFTER the project is committed as gone.
    # On filesystem errors, leave orphaned certs rather than endangering user code.
    for certificate in cert_files:
        try:
            certificate.unlink()
        except OSError:
            print('Warning: leftover generated project certificate ' + slug, file=sys.stderr)

    # The committed state is authoritative; queue reconciliation once after deletion.
    hosts_warning = request_windows_hosts_sync()
    return {'message': 'Projekt usuniety z Nginx, PHP-FPM i DevStack' +
            (' wraz z baza MariaDB.' if database else '.') +
            ' Pliki projektu pozostaly na dysku. Windows hosts zsynchronizuje sie automatycznie.' + hosts_warning}


def reveal(data: dict[str, Any]) -> dict[str, str]:
    slug = check_slug(data.get('slug'))
    if slug not in read_json(STATE_FILE):
        raise InputError('Nie ma takiego projektu.')
    item = read_json(SECRET_FILE).get(slug)
    if not isinstance(item, dict) or not isinstance(item.get('password'), str):
        raise InputError('Projekt nie ma zapisanego hasla bazy.')
    return {'password': item['password']}


def update_config(data: dict[str, Any]) -> dict[str, str]:
    """Validate both editable regions, test Nginx, reload, or roll back."""
    if not {'slug', 'overrides'}.issubset(data) or set(data) - {'slug', 'overrides', 'extra_locations'}:
        raise InputError('Nieprawidlowe pola konfiguracji Nginx.')
    slug = check_slug(data.get('slug'))
    overrides = check_overrides(data.get('overrides'))
    state = read_json(STATE_FILE)
    if slug not in state:
        raise InputError('Nie ma takiego projektu.')
    p = state[slug]
    assets = check_extra_locations(data.get('extra_locations', p.get('extra_locations', '')))
    target = site_path(slug)
    if not target.is_file() or target.is_symlink():
        raise InputError('Plik Nginx nie jest dostepny.')
    before = target.read_text(encoding='utf-8')
    root = pathlib.Path(p['path'])
    pub = root / p['webroot']
    after = nginx_conf(slug, p['domain'], pub, p['https'], overrides, assets)
    if after == before:
        return {'message': 'Brak zmian w konfiguracji.'}
    backup = pathlib.Path('/var/lib/devstack') / f'vhost-{slug}-previous.conf'
    backup.write_text(before, encoding='utf-8')
    target.write_text(after, encoding='utf-8')
    try:
        run(['/usr/sbin/nginx', '-t'])
        run(['/usr/bin/systemctl', 'reload', 'nginx'])
        p['overrides'] = overrides
        p['extra_locations'] = assets
        write_json_atomic(STATE_FILE, state)
    except Exception:
        target.write_text(before, encoding='utf-8')
        try:
            run(['/usr/sbin/nginx', '-t'])
            run(['/usr/bin/systemctl', 'reload', 'nginx'])
        except Exception:
            print('Critical: nginx rollback may require manual intervention', file=sys.stderr)
        raise
    return {'message': 'Konfiguracja przetestowana i zastosowana. Kopia poprzedniej wersji zostala zachowana.'}


def update_full_config(data: dict[str, Any]) -> dict[str, str]:
    slug = check_slug(data.get('slug'))
    state = read_json(STATE_FILE)
    if slug not in state:
        raise InputError('Nie ma takiego projektu.')
    validated = check_full_config(data.get('full_config'), slug, state[slug])
    outcome = update_config({'slug': slug, **validated})
    return {**outcome, **validated}



# The request handler is intentionally short. Only the developer-identity
# process runs in a worker thread; all privileged provisioning and JSON state
# mutations remain serialized on the daemon's main thread.
def engine_catalog() -> dict[str, Any]:
    # This catalog is deployed with the root-owned application; the browser
    # supplies only a registered identifier, never a module or executable.
    return read_json(ENGINE_CATALOG_FILE)


def engine_definition(engine: Any) -> dict[str, Any]:
    if not isinstance(engine, str) or not SLUG.fullmatch(engine):
        raise InputError('Nieobslugiwany silnik projektu.')
    definition = engine_catalog().get(engine)
    if not isinstance(definition, dict):
        raise InputError('Nieobslugiwany silnik projektu.')
    return definition


def engine_job() -> dict[str, Any]:
    return read_json(ENGINE_JOB_FILE) if ENGINE_JOB_FILE.exists() else {}


def engine_busy() -> bool:
    return engine_job().get('state') in ENGINE_ACTIVE


def ensure_engine_idle() -> None:
    if engine_busy():
        raise InputError('Trwa operacja instalatora. Zaczekaj na jej zakonczenie.')


def public_engine_job() -> dict[str, Any] | None:
    job = engine_job()
    if not job:
        return None
    # Never return private request state or a database credential.
    return {key: job[key] for key in
            ('id', 'engine', 'action', 'state', 'message', 'slug', 'log', 'tool')
            if key in job}


def engine_identity() -> tuple[str, str]:
    cfg = config()
    user = cfg['dev_user']
    account = pwd.getpwnam(user)
    home = '/home/' + user
    if account.pw_uid == 0 or user == 'devstackweb' or account.pw_dir != home:
        raise RuntimeError('Nieprawidlowe konto instalatora.')
    return user, home


def engine_unit_args(unit: str, action: str, args: list[str], timeout: int,
                     engine: str = 'laravel') -> list[str]:
    engine_definition(engine)
    if not ENGINE_UNIT.fullmatch(unit):
        raise RuntimeError('Nieprawidlowa jednostka instalatora.')
    user, home = engine_identity()
    # systemd, not the root daemon, switches identity. No shell or inherited
    # root HOME/PATH. Third-party scripts cannot use sudo or write system files.
    return [
        '/usr/bin/systemd-run', '--quiet', '--wait', '--pipe', '--collect',
        '--unit=' + unit, '--uid=' + user, '--working-directory=' + home,
        '--setenv=HOME=' + home,
        '--setenv=PATH=' + home + '/.local/bin:/usr/local/bin:/usr/bin:/bin',
        '--property=NoNewPrivileges=yes', '--property=PrivateTmp=yes',
        '--property=PrivateDevices=yes', '--property=ProtectSystem=strict',
        '--property=ReadWritePaths=' + home,
        '--property=InaccessiblePaths=-/mnt/c -/etc/devstack -/var/lib/devstack -/var/lib/devstack-web -/run/devstack',
        # Keep /mnt/wsl accessible: WSL may use it for the generated DNS resolver.
        '--property=ProtectKernelTunables=yes', '--property=ProtectKernelModules=yes',
        '--property=ProtectControlGroups=yes', '--property=RestrictSUIDSGID=yes',
        '--property=KillMode=control-group', '--property=UMask=0077',
        '--property=PartOf=devstackd.service',
        '--property=RuntimeMaxSec=' + str(timeout) + 's',
        '/usr/bin/python3', '-I', ENGINE_RUNNER, engine, action, *args,
    ]


def stop_engine_unit(unit: Any) -> None:
    if not isinstance(unit, str) or not ENGINE_UNIT.fullmatch(unit):
        return
    try:
        subprocess.run(['/usr/bin/systemctl', 'stop', unit + '.service'],
                       capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pass


def sanitize_tool_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get('composer'), bool):
        raise RuntimeError('Nieprawidlowy wynik instalatora.')
    result = {'composer': value['composer']}
    for key in ('installed', 'latest'):
        if key not in value and key == 'latest':
            continue
        item = value.get(key)
        if item is not None and (not isinstance(item, str) or not ENGINE_VERSION.fullmatch(item)):
            raise RuntimeError('Nieprawidlowa wersja instalatora.')
        result[key] = item
    return result


ENGINE_FAILURE_MESSAGES = {
    'frontend_node': 'Laravel wymaga Node.js 20.19+ (20.x) lub 22.12+. Sprawdz Node.js w Ubuntu.',
    'frontend_npm': 'Laravel w DevStack wymaga npm 11+. Zaktualizuj npm w Ubuntu przed utworzeniem projektu.',
}


def adapter_call(unit: str, action: str, args: list[str],
                 settings: dict[str, str] | None = None, timeout: int = 950,
                 engine: str = 'laravel') -> dict[str, Any]:
    try:
        proc = subprocess.run(engine_unit_args(unit, action, args, timeout, engine),
                              input=json.dumps(settings) if settings is not None else '',
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              text=True, timeout=timeout + 5, check=False, close_fds=True)
        if len(proc.stdout) > 8192:
            raise ValueError('oversized response')
        envelope = json.loads(proc.stdout)
        if not isinstance(envelope, dict):
            raise ValueError('invalid response')
        if proc.returncode == 0 and envelope.get('ok') is True and isinstance(envelope.get('result'), dict):
            return {'ok': True, 'result': envelope['result']}
        log = envelope.get('log')
        code = envelope.get('code')
        return {'ok': False, 'log': log if isinstance(log, str) and
                re.fullmatch(re.escape(engine) + r'-[A-Za-z0-9_-]+\.log', log) else None,
                'code': code if isinstance(code, str) and code in ENGINE_FAILURE_MESSAGES else None}
    except subprocess.TimeoutExpired:
        stop_engine_unit(unit)
    except (OSError, ValueError, RuntimeError):
        pass
    return {'ok': False}


def engine_tool_status(data: dict[str, Any]) -> dict[str, Any]:
    if set(data) != {'engine'}:
        raise InputError('Nieobslugiwany instalator.')
    engine = data.get('engine')
    engine_definition(engine)
    ensure_engine_idle()
    result = adapter_call('devstack-engine-' + secrets.token_hex(16) + '-status',
                          'status', [], timeout=20, engine=engine)
    if not result.get('ok'):
        raise RuntimeError('Nie udalo sie odczytac wersji instalatora.')
    return sanitize_tool_result(result['result'])


def start_engine_phase(job: dict[str, Any], state: str, action: str, args: list[str],
                       message: str, settings: dict[str, str] | None = None) -> None:
    unit = 'devstack-engine-' + job['id'] + '-' + action
    job.update(state=state, unit=unit, message=message)
    write_json_atomic(ENGINE_JOB_FILE, job)
    # The thread receives only a validated argument vector and, for configure,
    # the credentials through private stdin. It never writes root-owned state.
    job_id = job['id']
    engine = job['engine']
    def worker():
        try:
            result = adapter_call(unit, action, args, settings, engine=engine)
        except Exception:
            result = {'ok': False}
        ENGINE_RESULTS.put((job_id, unit, action, result))
    threading.Thread(target=worker, daemon=True).start()


def create(data: dict[str, Any]) -> dict[str, Any]:
    ensure_engine_idle()
    engine = data.get('engine', 'none')
    if engine == 'none':
        return create_environment(data)
    definition = engine_definition(engine)
    slug = check_slug(data.get('slug'))
    name = check_name(data.get('name'))
    domain = check_domain(data.get('domain'))
    options = {item['id'] for item in definition['options']}
    kit = data.get('starter_kit')
    if data.get('webroot') != definition['webroot'] or not isinstance(kit, str) or kit not in options:
        raise InputError('Nieprawidlowy katalog publiczny lub wariant instalacji.')
    if not isinstance(data.get('database'), bool) or not isinstance(data.get('https'), bool):
        raise InputError('Nieprawidlowe ustawienia projektu.')
    cfg = config()
    state = read_json(STATE_FILE)
    if slug in state or any(p.get('domain') == domain for p in state.values()):
        raise InputError('Projekt lub domena juz istnieje.')
    root = pathlib.Path(cfg['projects_dir']) / slug
    if root.exists() or root.is_symlink():
        raise InputError('Katalog juz istnieje. Instalator nie nadpisuje plikow.')
    if any(p.exists() or p.is_symlink() for p in (site_path(slug), site_link_path(slug), pool_path(slug))):
        raise InputError('Istnieje konfiguracja serwera dla tego projektu.')
    # Root-owned reservation (one operation at a time) precedes any install.
    request = {'slug': slug, 'name': name, 'domain': domain, 'webroot': definition['webroot'],
               'database': data['database'], 'https': data['https'],
               'engine': engine, 'starter_kit': kit}
    job = {'id': secrets.token_hex(16), 'action': 'create', 'engine': engine,
           'slug': slug, 'request': request}
    start_engine_phase(job, 'scaffolding', 'scaffold', [slug, request['starter_kit']],
                       'Instalowanie ' + definition['label'] + ' i budowanie zasobow...')
    return {'message': job['message'], 'job_id': job['id']}


def engine_tool_action(data: dict[str, Any]) -> dict[str, Any]:
    ensure_engine_idle()
    if set(data) != {'engine', 'action'}:
        raise InputError('Nieobslugiwany instalator.')
    engine = data.get('engine')
    definition = engine_definition(engine)
    actions = {'check': ('checking', 'Sprawdzanie aktualizacji...'),
               'install-tool': ('installing', 'Instalowanie ' + definition['tool'] + '...'),
               'update-tool': ('updating', 'Aktualizowanie ' + definition['tool'] + '...')}
    action = data.get('action')
    if not isinstance(action, str) or action not in actions:
        raise InputError('Nieobslugiwana operacja instalatora.')
    state, message = actions[action]
    job = {'id': secrets.token_hex(16), 'action': action, 'engine': engine}
    start_engine_phase(job, state, action, [], message)
    return {'message': message, 'job_id': job['id']}


def finish_engine_job(job: dict[str, Any], *, success: bool, message: str,
                      log: str | None = None) -> None:
    if job.get('action') == 'create':
        slug = job['slug']
        state = read_json(STATE_FILE)
        project = state.get(slug)
        if isinstance(project, dict) and project.get('engine') == job.get('engine'):
            project['engine_status'] = 'ready' if success else 'failed'
            write_json_atomic(STATE_FILE, state)
    job.update(state='succeeded' if success else 'failed', message=message)
    if log:
        job['log'] = log
    job.pop('unit', None)
    job.pop('request', None)
    write_json_atomic(ENGINE_JOB_FILE, job)


def poll_engine_results() -> None:
    while True:
        try:
            job_id, unit, action, outcome = ENGINE_RESULTS.get_nowait()
        except queue.Empty:
            return
        job = engine_job()
        if job.get('id') != job_id or job.get('unit') != unit or job.get('state') not in ENGINE_ACTIVE:
            continue
        if not outcome.get('ok'):
            finish_engine_job(job, success=False,
                              message=ENGINE_FAILURE_MESSAGES.get(outcome.get('code'),
                                  'Operacja nie powiodla sie. Istniejace pliki pozostawiono bez usuwania.'),
                              log=outcome.get('log'))
            continue
        try:
            result = outcome['result']
            if action == 'scaffold':
                if result.get('scaffolded') is not True:
                    raise RuntimeError('Invalid scaffold result')
                request = job['request']
                slug = job['slug']
                # Reuse the normal infrastructure transaction only after the
                # installer has completed. It creates the public ACL, pool,
                # database, certificate and managed hosts entry.
                create_environment(request)
                settings = {'url': ('https://' if request['https'] else 'http://') + request['domain']}
                if request['database']:
                    project = read_json(STATE_FILE)[slug]
                    settings.update(db_name=project['db_name'], db_user=project['db_user'],
                                    db_password=read_json(SECRET_FILE)[slug]['password'])
                start_engine_phase(job, 'configuring', 'configure', [slug],
                                   'Konfigurowanie aplikacji...', settings)
            elif action == 'configure':
                if result.get('configured') is not True:
                    raise RuntimeError('Invalid configuration result')
                start_engine_phase(job, 'migrating', 'migrate', [job['slug']],
                                   'Uruchamianie migracji...')
            elif action == 'migrate':
                if result.get('migrated') is not True:
                    raise RuntimeError('Invalid migration result')
                finish_engine_job(job, success=True, message=engine_definition(job['engine'])['label'] + ' zainstalowany i skonfigurowany.')
            elif action in ('check', 'install-tool', 'update-tool'):
                job['tool'] = sanitize_tool_result(result)
                if action != 'check' and job['tool']['installed'] is None:
                    raise RuntimeError('Missing installed version')
                finish_engine_job(job, success=True, message='Operacja instalatora zakonczona.')
            else:
                raise RuntimeError('Unknown job phase')
        except Exception:
            # Do not leak database settings or arbitrary command output.
            finish_engine_job(job, success=False,
                              message='Nie zakonczono konfiguracji. Pliki i ewentualny utworzony projekt zachowano.')


def recover_engine_job() -> None:
    job = engine_job()
    if job.get('state') in ENGINE_ACTIVE:
        stop_engine_unit(job.get('unit'))
        finish_engine_job(job, success=False,
                          message='Operacja przerwana przez restart DevStack. Nie zostala automatycznie ponowiona.')


OPS = {'status': status, 'create': create, 'reveal': reveal,
       'engine_tool_status': engine_tool_status, 'engine_tool_action': engine_tool_action,
       'test_project_database': test_project_database, 'delete_project': delete_project,
       'update_config': update_config, 'update_full_config': update_full_config,
       'set_preferences': set_ui_preferences, 'get_preferences': preferences,
       'public_ca_certificate': public_ca_certificate}


def handle(raw: bytes) -> bytes:
    try:
        if len(raw) > 65536:
            raise InputError('Zbyt dlugie zadanie.')
        request = json.loads(raw.decode('utf-8'))
        if not isinstance(request, dict) or not isinstance(request.get('data'), dict):
            raise InputError('Nieprawidlowy format zapytania.')
        operation = request.get('op')
        if operation not in OPS:
            raise InputError('Nieobslugiwana operacja.')
        response = {'ok': True, 'result': OPS[operation](request['data'])}
    except (InputError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        response = {'ok': False, 'error': str(exc)[:1200]}
    except Exception:
        print('Unexpected DevStack helper error', file=sys.stderr)
        response = {'ok': False, 'error': 'Nieoczekiwany blad pomocnika systemowego.'}
    return (json.dumps(response, ensure_ascii=True) + '\n').encode('utf-8')


def serve() -> None:
    config()
    recover_engine_job()
    uid = pwd.getpwnam('devstackweb').pw_uid
    gid = pwd.getpwnam('devstackweb').pw_gid
    SOCKET_FILE.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    os.chown(SOCKET_FILE.parent, 0, gid)
    os.chmod(SOCKET_FILE.parent, 0o750)
    SOCKET_FILE.unlink(missing_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(SOCKET_FILE))
    os.chown(SOCKET_FILE, 0, gid)
    os.chmod(SOCKET_FILE, 0o660)
    server.listen(12)
    # Reconcile once at WSL service start (e.g. after a missed notification).
    # This does not run while Ubuntu is stopped and creates no Windows listener.
    request_windows_hosts_sync()
    print('DevStack local helper listening', flush=True)
    while True:
        poll_engine_results()
        server.settimeout(1 if engine_busy() else None)
        try:
            conn, _ = server.accept()
        except socket.timeout:
            continue
        with conn:
            conn.settimeout(30)
            pid, peer_uid, peer_gid = struct.unpack('3i', conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
            if peer_uid != uid:
                conn.sendall(b'{"ok":false,"error":"Unauthorized local process"}\n')
                continue
            data = bytearray()
            try:
                while len(data) < 65537:
                    part = conn.recv(4096)
                    if not part:
                        break
                    data += part
                    if b'\n' in part:
                        break
                conn.sendall(handle(bytes(data).split(b'\n', 1)[0]))
            except (OSError, socket.timeout):
                continue


if __name__ == '__main__':
    if os.geteuid() != 0:
        sys.exit('Run only as root from the dedicated systemd unit.')
    serve()
