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
SOCKET_FILE = pathlib.Path('/run/devstack/control.sock')
SLUG = re.compile(r'[a-z][a-z0-9-]{1,39}\Z')
DOMAIN = re.compile(r'[a-z][a-z0-9-]{0,39}\.test\Z')
WEBROOT = re.compile(r'[a-zA-Z0-9_][a-zA-Z0-9_/-]{0,99}\Z')
LOCATION_START = '        # devstack:location:start\n'
LOCATION_END = '        # devstack:location:end\n'
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
            'preferences': get_ui_preferences()}


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


def nginx_conf(slug: str, domain: str, public: pathlib.Path, ssl_enabled: bool, overrides: str = '') -> str:
    """Generate a complete virtual host with a small editable region inside location /."""
    rules = check_overrides(overrides)
    location_opts = ''.join('        ' + line + '\n' for line in rules.splitlines())
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
    location ~ ^/index\\.php$ {{
        include snippets/fastcgi-php.conf;
        fastcgi_pass unix:/run/php/devstack-{slug}.sock;
    }}
    location ~ \\.php$ {{ return 404; }}
    location ~ /\\. {{ deny all; }}
}}
'''
    return text


def check_full_config(raw: Any, slug: str, project: dict[str, Any]) -> str:
    """Allow modifying only the marked location / block in the full editor."""
    if not isinstance(raw, str) or not raw or len(raw) > 16000 or '\x00' in raw:
        raise InputError('Nieprawidlowy plik Nginx.')
    root = pathlib.Path(project['path']) / project['webroot']
    canonical = nginx_conf(slug, project['domain'], root, project['https'])
    before, remainder = canonical.split(LOCATION_START, 1)
    _, after = remainder.split(LOCATION_END, 1)
    prefix = before + LOCATION_START
    suffix = LOCATION_END + after
    if not raw.startswith(prefix) or not raw.endswith(suffix):
        raise InputError('Pelny plik zawiera sekcje chronione. Mozna zmieniac tylko obszar location / pomiedzy znacznikami DevStack.')
    between = raw[len(prefix):len(raw)-len(suffix)]
    return check_overrides('\n'.join(line.strip() for line in between.splitlines()))


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
    mariadb_execute(f"DROP USER IF EXISTS '{user}'@'127.0.0.1'; DROP DATABASE IF EXISTS `{name}`;")


def create(data: dict[str, Any]) -> dict[str, str]:
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
            'overrides': '',
        }
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
    return {'message': 'Projekt zapisany. PHP-FPM uruchomi nowa pule za kilka sekund. Jezeli integracja Windows jest skonfigurowana, domena zostanie zsynchronizowana automatycznie.'}


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
            handle.write('[client]\\nuser=' + expected_user + '\\npassword=' + password + '\\n')
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
            if database:
                drop_db(expected_name, expected_user)
                db_dropped = True

            del state[slug]
            secrets_state.pop(slug, None)
            write_json_atomic(STATE_FILE, state)
            write_json_atomic(SECRET_FILE, secrets_state)
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

    return {'message': 'Projekt usuniety z Nginx, PHP-FPM i DevStack' +
            (' wraz z baza MariaDB.' if database else '.') +
            ' Pliki projektu pozostaly na dysku. Windows hosts zsynchronizuje sie automatycznie.'}


def reveal(data: dict[str, Any]) -> dict[str, str]:
    slug = check_slug(data.get('slug'))
    if slug not in read_json(STATE_FILE):
        raise InputError('Nie ma takiego projektu.')
    item = read_json(SECRET_FILE).get(slug)
    if not isinstance(item, dict) or not isinstance(item.get('password'), str):
        raise InputError('Projekt nie ma zapisanego hasla bazy.')
    return {'password': item['password']}


def update_config(data: dict[str, Any]) -> dict[str, str]:
    """Validate bounded directives; replace vhost, nginx -t, rollback on failure."""
    slug = check_slug(data.get('slug'))
    overrides = check_overrides(data.get('overrides'))
    state = read_json(STATE_FILE)
    if slug not in state:
        raise InputError('Nie ma takiego projektu.')
    p = state[slug]
    target = site_path(slug)
    if not target.is_file() or target.is_symlink():
        raise InputError('Plik Nginx nie jest dostepny.')
    before = target.read_text(encoding='utf-8')
    root = pathlib.Path(p['path'])
    pub = root / p['webroot']
    after = nginx_conf(slug, p['domain'], pub, p['https'], overrides)
    if after == before:
        return {'message': 'Brak zmian w konfiguracji.'}
    # Preserve previous content for a failed syntax check and enable manual recovery.
    backup = pathlib.Path('/var/lib/devstack') / f'vhost-{slug}-previous.conf'
    backup.write_text(before, encoding='utf-8')
    target.write_text(after, encoding='utf-8')
    try:
        run(['/usr/sbin/nginx', '-t'])
        run(['/usr/bin/systemctl', 'reload', 'nginx'])
        p['overrides'] = overrides
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
    rules = check_full_config(data.get('full_config'), slug, state[slug])
    outcome = update_config({'slug': slug, 'overrides': rules})
    return {**outcome, 'overrides': rules}


OPS = {'status': status, 'create': create, 'reveal': reveal,
       'test_project_database': test_project_database, 'delete_project': delete_project,
       'update_config': update_config, 'update_full_config': update_full_config,
       'set_preferences': set_ui_preferences, 'get_preferences': preferences}


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
    print('DevStack local helper listening', flush=True)
    while True:
        conn, _ = server.accept()
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
