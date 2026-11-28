#!/usr/bin/env python3
"""DevStack's root-owned local Unix-socket helper.

The browser and PHP cannot submit shell commands, paths or arbitrary Nginx rules.
Supported operations are intentionally small and typed.
"""
from __future__ import annotations

import base64
from contextlib import nullcontext
import hashlib
import importlib.util
import json
import os
import pathlib
import queue
import threading
import pwd
import re
import secrets
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
from typing import Any

# Keep imports from writing inside the root-owned, replaceable code tree.
sys.dont_write_bytecode = True

CFG_FILE = pathlib.Path('/etc/devstack/server.json')
STATE_FILE = pathlib.Path('/var/lib/devstack/projects.json')
SECRET_FILE = pathlib.Path('/var/lib/devstack/credentials.json')
PREFERENCES_FILE = pathlib.Path('/var/lib/devstack/ui-preferences.json')
CA_PUBLIC_FILE = pathlib.Path('/var/lib/devstack/ca/rootCA.pem')  # Public cert ONLY; never expose rootCA-key.pem.
SOCKET_FILE = pathlib.Path('/run/devstack/control.sock')
MAILPIT_UNIT_FILE = pathlib.Path('/etc/systemd/system/devstack-mailpit.service')
ENGINE_JOB_FILE = pathlib.Path('/var/lib/devstack/engine-job.json')
ENGINE_DB_FILE = pathlib.Path('/var/lib/devstack/engine-db-reservation.json')
AUTH_MODE_FILE = pathlib.Path('/etc/devstack/auth.mode')
ENGINE_ACCESS_FILE = pathlib.Path('/etc/devstack/engine-access.mode')
ENGINE_RUNNER = '/opt/devstack/framework-installers/runner.py'
ENGINE_CATALOG_FILE = pathlib.Path(__file__).resolve().parents[1] / 'framework-installers/catalog.json'
# Load only the small shared, deployed root-owned validation module. Never
# import an adapter or a module from the developer home into the root daemon.
_options_spec = importlib.util.spec_from_file_location('devstack_installer_common', ENGINE_CATALOG_FILE.with_name('common.py'))
_installer_common = importlib.util.module_from_spec(_options_spec)
_options_spec.loader.exec_module(_installer_common)
resolve_installer_options = _installer_common.resolve_installer_options
_services_spec = importlib.util.spec_from_file_location('devstack_managed_services', pathlib.Path(__file__).resolve().with_name('managed_services.py'))
managed_services = importlib.util.module_from_spec(_services_spec)
_services_spec.loader.exec_module(managed_services)
ENGINE_RESULTS = queue.SimpleQueue()
ENGINE_ACTIVE = {'preparing', 'scaffolding', 'configuring', 'migrating', 'checking', 'installing', 'updating'}
ENGINE_VERSION = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+\Z')
ENGINE_UNIT = re.compile(r'devstack-engine-[0-9a-f]{32}-[a-z-]+\Z')
WINDOWS_SCHTASKS = pathlib.Path('/mnt/c/Windows/System32/schtasks.exe')
WINDOWS_HOSTS_TASK = 'DevStack Hosts Event Sync'
WINDOWS_NOTIFICATION_MODE = pathlib.Path('/etc/devstack/windows-local-notifications')
WINDOWS_NOTIFICATION_ID = re.compile(r'(?:op|job|batch|service)-[a-f0-9]{32}|mail-[A-Za-z0-9]{22}')
WINDOWS_NOTIFICATION_LAUNCHER = ('DevStack', 'LocalNotifications', 'DevStack.Notifications.exe')
WINDOWS_NOTIFICATION_TARGET = pathlib.Path('/mnt/c').joinpath(*WINDOWS_NOTIFICATION_LAUNCHER)
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


def check_nginx_rules(value: Any) -> dict[str, str]:
    """One editor, the same two strictly validated managed Nginx regions.

    Bare tuning directives go inside location /; the existing asset location
    blocks go at server level. No raw user text is ever used in the vhost.
    """
    if not isinstance(value, str) or len(value) > 5802 or '\x00' in value:
        raise InputError('Dodatkowa konfiguracja Nginx: wymagany tekst do 5802 znakow.')
    position = 0
    tuning = []
    locations = []
    while position < len(value):
        while position < len(value) and value[position].isspace():
            position += 1
        if position == len(value):
            break
        match = ASSET_BLOCK.match(value, position)
        if match:
            locations.append(match.group(0))
            position = match.end()
            continue
        end = value.find('\n', position)
        if end < 0:
            end = len(value)
        tuning.append(value[position:end])
        position = end + 1
    return {
        'overrides': check_overrides('\n'.join(tuning)),
        'extra_locations': check_extra_locations('\n'.join(locations)),
    }


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
        sync_parent(path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def sync_parent(path: pathlib.Path) -> None:
    fd = os.open(path.parent, os.O_DIRECTORY | os.O_RDONLY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def ensure_project_registry() -> None:
    """An empty installation must still support the existing widget reader."""
    if STATE_FILE.is_symlink() or (STATE_FILE.exists() and not STATE_FILE.is_file()):
        raise RuntimeError('Nieprawidlowy plik rejestru projektow DevStack.')
    if not STATE_FILE.exists():
        write_json_atomic(STATE_FILE, {})


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


def nginx_backup_path(slug: str) -> pathlib.Path:
    return STATE_FILE.parent / f'vhost-{slug}-previous.conf'


def defer_php_fpm_reload() -> None:
    """Schedule reload after the PHP request completes to avoid Nginx 502.

    systemd's transient timer runs independently of the current FastCGI request.
    The unit name is random and contains no user-controlled input.
    """
    unit = 'devstack-fpm-reload-' + secrets.token_hex(8)
    run(['/usr/bin/systemd-run', '--quiet', '--collect', '--on-active=8s',
         '--timer-property=AccuracySec=1s', '--unit=' + unit,
         '/usr/bin/systemctl', 'reload', 'php8.5-fpm'])


def project_frameworks(slugs: list[str]) -> dict[str, dict[str, Any]]:
    """Only the developer identity may inspect project-controlled metadata."""
    if not slugs:
        return {}
    try:
        user, home = engine_identity()
        account = pwd.getpwnam(user)
        if account.pw_uid == 0 or account.pw_gid == 0:
            return {}
        # PID 1 switches identity before applying restrictions, just like
        # the established installer and project-directory workers. Direct
        # credential changes in children of the hardened daemon can fail.
        unit = 'devstack-frameworks-' + secrets.token_hex(8)
        result = subprocess.run(
            ['/usr/bin/systemd-run', '--quiet', '--wait', '--pipe', '--collect',
             '--service-type=exec', '--unit=' + unit,
             '--uid=' + user, '--gid=' + str(account.pw_gid),
             '--working-directory=/', '--setenv=HOME=' + home,
             '--setenv=PATH=/usr/bin:/bin',
             '--property=NoNewPrivileges=yes',
             '--property=ProtectSystem=strict', '--property=ProtectHome=read-only',
             '--property=PrivateTmp=yes', '--property=PrivateDevices=yes',
             '--property=ProtectKernelTunables=yes', '--property=ProtectKernelModules=yes',
             '--property=ProtectControlGroups=yes', '--property=RestrictSUIDSGID=yes',
             '--property=InaccessiblePaths=-/etc/devstack -/var/lib/devstack -/var/lib/devstack-web -/run/devstack -/mnt/c',
             '--property=PartOf=devstackd.service', '--property=RuntimeMaxSec=5s',
             '--property=UMask=0077',
             '/usr/bin/python3', '-I', str(pathlib.Path(__file__).resolve().with_name('project_frameworks.py'))],
            input=json.dumps(slugs), capture_output=True, text=True, timeout=10,
            check=True, close_fds=True,
        )
        if len(result.stdout) > 1024 * 1024:
            raise ValueError('oversized framework response')
        raw = json.loads(result.stdout)
        if not isinstance(raw, dict):
            return {}
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as exc:
        # Keep the dashboard usable and emit only a bounded diagnostic, never
        # raw project metadata or stderr from the worker.
        code = (' (exit ' + str(exc.returncode) + ')') if isinstance(exc, subprocess.CalledProcessError) else ''
        print('Framework metadata worker unavailable: ' + type(exc).__name__ + code, file=sys.stderr, flush=True)
        return {}
    labels = {'Laravel', 'Symfony', 'CakePHP', 'CodeIgniter', 'Yii', 'Slim'}
    allowed = set(slugs)
    output = {}
    for slug, value in raw.items():
        if slug not in allowed or not isinstance(value, dict):
            continue
        label, version = value.get('name'), value.get('version')
        if not isinstance(label, str) or label not in labels:
            continue
        if version is not None and (not isinstance(version, str) or not re.fullmatch(
            r'[0-9]{1,5}\.[0-9]{1,5}(?:\.[0-9]{1,5}){0,2}(?:[-+][0-9A-Za-z][0-9A-Za-z.+-]{0,47})?', version
        )):
            continue
        output[slug] = {'name': label, 'version': version}
    return output


def public_projects() -> list[dict[str, Any]]:
    output = []
    state = read_json(STATE_FILE)
    frameworks = project_frameworks([slug for slug, p in state.items() if SLUG.fullmatch(slug) and isinstance(p, dict)])
    for slug, p in sorted(state.items()):
        if not SLUG.fullmatch(slug) or not isinstance(p, dict):
            continue
        item = dict(p)
        # Internal registration identity is not yet a public API or a grant.
        item.pop('project_id', None)
        item['isolated_https'] = item.pop('isolation_id', None) is not None
        item['slug'] = slug
        item.pop('detected_framework', None)
        if slug in frameworks:
            item['detected_framework'] = frameworks[slug]
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


def mailpit_status() -> str:
    if not MAILPIT_UNIT_FILE.is_file():
        return 'not-installed'
    try:
        result = subprocess.run(['/usr/bin/systemctl', 'is-active', 'devstack-mailpit.service'],
                                capture_output=True, text=True, timeout=2)
        state = result.stdout.strip()
        return state if state in {'active', 'inactive', 'failed', 'activating', 'deactivating', 'reloading'} else 'unknown'
    except (OSError, subprocess.TimeoutExpired):
        return 'unknown'


def require_local_mail(data: dict[str, Any]) -> bool:
    value = data.get('local_mail', False)
    if not isinstance(value, bool):
        raise InputError('Nieprawidlowe ustawienie poczty lokalnej.')
    if value and mailpit_status() != 'active':
        raise InputError('Najpierw uruchom lokalna skrzynke Mailpit w zakladce E-mail.')
    return value


def service_control(data: dict[str, Any]) -> dict[str, Any]:
    if set(data) != {'service', 'action', 'confirmed'} or data['confirmed'] is not True:
        raise InputError('Potwierdz operacje uslugi.')
    if engine_busy():
        raise InputError('Najpierw poczekaj na zakonczenie instalacji frameworka.')
    return managed_services.schedule(data['service'], data['action'])


def set_service_autostart(data: dict[str, Any]) -> dict[str, Any]:
    if set(data) != {'service', 'enabled'} or type(data['enabled']) is not bool:
        raise InputError('Nieprawidlowe ustawienie autostartu.')
    if engine_busy():
        raise InputError('Najpierw poczekaj na zakonczenie instalacji frameworka.')
    return managed_services.set_autostart(data['service'], data['enabled'])


def set_widget_services(data: dict[str, Any]) -> dict[str, Any]:
    if set(data) != {'services'}:
        raise InputError('Nieprawidlowe ustawienia widzetu.')
    rows = managed_services.catalog()
    installed = managed_services.installed_rows(rows, managed_services.units_snapshot(rows))
    return {'services': managed_services.save_selection(data['services'], installed)}


def managed_service_status(data: dict[str, Any]) -> dict[str, Any]:
    """Read only registered systemd units; never scan projects or collect notices."""
    if data:
        raise InputError('Ta operacja nie przyjmuje parametrow.')
    rows = managed_services.public_rows()
    return {'services': {r['unit'].removesuffix('.service'): r['state'] for r in rows},
            'managed_services': rows, 'service_action_pending': managed_services.action_pending(),
            'mailpit': next((r['state'] for r in rows if r['id'] == 'mailpit'), 'not-installed')}


def status(_: dict[str, Any]) -> dict[str, Any]:
    return {**managed_service_status({}), 'projects': public_projects(),
            'preferences': get_ui_preferences(), 'engine_job': public_engine_job(),
            'engines': engine_catalog(), 'engine_access_allowed': engine_access_allowed()}



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



def install_wsl_ca(data: dict[str, Any]) -> dict[str, str]:
    """Trust only the existing root CA as the configured developer."""
    if data:
        raise InputError('Ta operacja nie przyjmuje parametrow.')
    if not pathlib.Path('/usr/bin/certutil').is_file():
        raise RuntimeError('Brak narzedzia NSS. W Ubuntu wykonaj: sudo apt-get install -y libnss3-tools, a potem ponow operacje.')
    user, home = engine_identity()
    ca = public_ca_certificate({})
    message = run([
        '/usr/bin/systemd-run', '--quiet', '--wait', '--pipe', '--collect',
        '--unit=devstack-ca-' + secrets.token_hex(8), '--uid=' + user,
        '--working-directory=' + home, '--setenv=HOME=' + home,
        '--setenv=PATH=/usr/bin:/bin',
        '--property=NoNewPrivileges=yes', '--property=PrivateTmp=yes',
        '--property=PrivateDevices=yes', '--property=PrivateNetwork=yes',
        '--property=ProtectSystem=strict', '--property=ReadWritePaths=' + home,
        '--property=InaccessiblePaths=-/mnt/c -/etc/devstack -/var/lib/devstack -/var/lib/devstack-web -/run/devstack',
        '--property=RuntimeMaxSec=20s', '--property=UMask=0077',
        '/usr/bin/python3', '-I', '/opt/devstack/daemon/wsl_ca.py',
    ], input_text=ca['pem'])
    return {'message': message, 'sha256': ca['sha256']}


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
    # The freshly installed tree is developer-controlled. Never recursively
    # change its permissions as root; avoid following symlinks.
    pwd.getpwnam(dev_user)
    def developer_acl(argv: list[str]) -> None:
        run(['/usr/bin/systemd-run', '--quiet', '--wait', '--pipe', '--collect',
             '--uid=' + dev_user, '--unit=devstack-acl-' + secrets.token_hex(8),
             '--property=NoNewPrivileges=yes', '--property=RuntimeMaxSec=40s',
             '--property=ProtectSystem=strict', '--property=ReadWritePaths=' + str(base.parent),
             *argv])
    developer_acl(['/usr/bin/setfacl', '-P', '-m', 'u:www-data:--x', '--',
                   str(base.parent), str(base), str(root)])
    developer_acl(['/usr/bin/setfacl', '-P', '-R', '-m', 'u:www-data:rX', '--', str(public)])
    developer_acl(['/usr/bin/find', '-P', str(public), '-type', 'd', '-exec',
                   '/usr/bin/setfacl', '-d', '-m', 'u:www-data:rX', '--', '{}', '+'])


def php_pool(slug: str, user: str, local_mail: bool = False) -> str:
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
''' + ('php_admin_value[sendmail_path] = /opt/devstack-mailpit/mailpit sendmail -S 127.0.0.1:1025 -t -i\n' if local_mail else '')


def nginx_conf(slug: str, domain: str, public: pathlib.Path, ssl_enabled: bool,
               overrides: str = '', extra_locations: str = '', *,
               isolation_id: str | None = None) -> str:
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
    if isolation_id is not None:
        if not ssl_enabled or rules or assets or not re.fullmatch(r'[0-9a-f]{32}', isolation_id):
            raise InputError('Izolowane HTTPS wymaga standardowej konfiguracji Nginx.')
        return text + f'''    server_tokens off;
    location / {{
        proxy_pass http://unix:/run/devstack-pilot-{isolation_id}-http/sockets/http.sock;
        proxy_http_version 1.1;
        proxy_next_upstream off;
        proxy_redirect off;
        proxy_ignore_headers X-Accel-Redirect;
        proxy_set_header Host $host;
        proxy_set_header Connection "";
        proxy_set_header Forwarded "";
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Port $server_port;
        proxy_set_header X-Forwarded-Prefix "";
        proxy_set_header X-Forwarded-Proto $scheme;
    }}
}}
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


def isolated_site_path(project_id: str) -> pathlib.Path:
    if not isinstance(project_id, str) or not re.fullmatch(r'[0-9a-f]{32}', project_id):
        raise InputError('Nieprawidlowy identyfikator izolowanej konfiguracji.')
    return STATE_FILE.parent / f'isolated-{project_id}.conf'


def replace_host_config(path: pathlib.Path, *, text: str = '', link: pathlib.Path | None = None) -> None:
    fd, name = tempfile.mkstemp(prefix='.devstack-', dir=path.parent)
    tmp = pathlib.Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            if link is None:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
        if link is not None:
            tmp.unlink()
            tmp.symlink_to(link)
        tmp.replace(path)
        sync_parent(path)
    finally:
        tmp.unlink(missing_ok=True)


def protected_config_matches(path: pathlib.Path, expected: str) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return (stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022
            and info.st_nlink == 1 and path.read_text(encoding='utf-8') == expected)


def managed_site_link(slug: str) -> bool:
    link = site_link_path(slug)
    return link.is_symlink() and link.readlink() == site_path(slug)


def project_host_configs(slug: str, project: dict[str, Any], project_id: str) -> tuple[str, str, str]:
    domain = check_domain(project['domain'])
    public = pathlib.Path(project['path']) / 'public'
    return (nginx_conf(slug, domain, public, True),
            nginx_conf(slug, domain, public, True, isolation_id=project_id),
            php_pool(slug, config()['dev_user'], project.get('local_mail', False)))


def isolated_route_matches(slug: str, project_id: str, isolated: str) -> bool:
    site, target = site_path(slug), isolated_site_path(project_id)
    return (managed_site_link(slug) and site.is_symlink() and site.readlink() == target
            and protected_config_matches(target, isolated) and not os.path.lexists(pool_path(slug)))


def legacy_fpm_socket(slug: str) -> pathlib.Path:
    return pathlib.Path('/run/php') / f'devstack-{slug}.sock'


def retire_project_pool(slug: str) -> None:
    endpoint = legacy_fpm_socket(slug)
    try:
        info = endpoint.lstat()
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != pwd.getpwnam('www-data').pw_uid:
            raise InputError('Nieoczekiwane gniazdo zwyklej puli PHP projektu.')
        # Existing connections can drain. Retired Nginx workers must not
        # open new connections to the old pool while FPM reloads.
        endpoint.chmod(0)
    run(['/usr/bin/systemctl', 'reload', 'php8.5-fpm'])
    deadline = time.monotonic() + 30
    while os.path.lexists(endpoint):
        if time.monotonic() >= deadline:
            raise RuntimeError('Nie potwierdzono wycofania zwyklej puli PHP projektu.')
        time.sleep(0.1)


def wait_for_ordinary_pool(slug: str) -> None:
    endpoint = legacy_fpm_socket(slug)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.settimeout(1)
                listener.connect(str(endpoint))
                return
        except (FileNotFoundError, ConnectionRefusedError, TimeoutError):
            time.sleep(0.1)
    raise RuntimeError('Zwykla pula PHP projektu nie uruchomila sie.')


def test_project_host_configs() -> None:
    run(['/usr/sbin/php-fpm8.5', '-t'])
    run(['/usr/sbin/nginx', '-t'])


def set_isolated_route(slug: str, project_id: str, isolated: str) -> None:
    target = isolated_site_path(project_id)
    replace_host_config(target, text=isolated)
    replace_host_config(site_path(slug), link=target)
    pool = pool_path(slug)
    pool.unlink(missing_ok=True)
    sync_parent(pool)


def reload_isolated_route(slug: str) -> None:
    try:
        run(['/usr/bin/systemctl', 'reload', 'nginx'])
    finally:
        retire_project_pool(slug)


def change_project_isolation(slug: str, *, activate: bool) -> dict[str, Any]:
    """Root CLI only. Caller holds the shared project operations lock."""
    from project_pilot import pair_for, registered

    ensure_engine_idle()
    if managed_services.action_pending():
        raise InputError('Poczekaj na zakonczenie operacji uslugi.')
    slug = check_slug(slug)
    spec, project_id, _ = registered(slug)
    state = read_json(STATE_FILE)
    project = state[slug]
    ordinary, isolated, legacy_pool = project_host_configs(slug, project, project_id)
    if project.get('agent_grants'):
        raise InputError('Najpierw cofnij wszystkie uprawnienia agentow projektu.')
    active = spec['isolated']
    if active:
        if not isolated_route_matches(slug, project_id, isolated):
            raise InputError('Niespojna konfiguracja izolowanego projektu.')
        pair = pair_for(spec, project_id)
        if activate:
            if os.path.lexists(legacy_fpm_socket(slug)):
                retire_project_pool(slug)
            return {'project': slug, 'project_id': project_id, 'status': pair.start(),
                    'isolated_https': True}
        try:
            # An explicit administrator rollback is the only path back to
            # the canonical ordinary vhost. Keep intent until it commits.
            replace_host_config(pool_path(slug), text=legacy_pool)
            replace_host_config(site_path(slug), text=ordinary)
            test_project_host_configs()
            run(['/usr/bin/systemctl', 'reload', 'php8.5-fpm'])
            wait_for_ordinary_pool(slug)
            run(['/usr/bin/systemctl', 'reload', 'nginx'])
            pair.stop()
            del project['isolation_id']
            write_json_atomic(STATE_FILE, state)
        except Exception:
            set_isolated_route(slug, project_id, isolated)
            project['isolation_id'] = project_id
            write_json_atomic(STATE_FILE, state)
            test_project_host_configs()
            reload_isolated_route(slug)
            raise
        discard_isolated_site(project_id)
        return {'project': slug, 'project_id': project_id, 'status': 'ordinary',
                'isolated_https': False}
    if not activate:
        return {'project': slug, 'project_id': project_id, 'status': 'ordinary',
                'isolated_https': False}
    if (not managed_site_link(slug) or not protected_config_matches(site_path(slug), ordinary)
            or not protected_config_matches(pool_path(slug), legacy_pool)):
        raise InputError('Zwykla konfiguracja projektu odbiega od konfiguracji DevStack.')
    pair = pair_for(spec, project_id, provision=True)
    pair.stop()
    project['isolation_id'] = project_id
    write_json_atomic(STATE_FILE, state)
    route_staged = False
    try:
        pair.start()
        route_staged = True
        set_isolated_route(slug, project_id, isolated)
        test_project_host_configs()
    except Exception:
        if route_staged:
            replace_host_config(site_path(slug), text=ordinary)
            replace_host_config(pool_path(slug), text=legacy_pool)
        pair.stop()
        del project['isolation_id']
        write_json_atomic(STATE_FILE, state)
        if route_staged:
            discard_isolated_site(project_id)
        raise
    # From the first attempted live cutover onward, failures retain the
    # isolated route and intent. No fallback to the old FPM pool.
    reload_isolated_route(slug)
    return {'project': slug, 'project_id': project_id, 'status': 'running',
            'isolated_https': True}


def recover_isolated_projects() -> None:
    from project_pilot import pair_for, registered

    for slug, project in read_json(STATE_FILE).items():
        if not isinstance(project, dict) or 'isolation_id' not in project:
            continue
        try:
            spec, project_id, _ = registered(slug)
            ordinary, isolated, legacy_pool = project_host_configs(slug, project, project_id)
            if not isolated_route_matches(slug, project_id, isolated):
                # Recover only known transaction artifacts. A foreign vhost,
                # enabled link or pool needs an administrator's attention.
                site, pool = site_path(slug), pool_path(slug)
                target = isolated_site_path(project_id)
                enabled = site_link_path(slug)
                linked = site.is_symlink() and site.readlink() == target
                interrupted_delete = (not os.path.lexists(enabled) and not os.path.lexists(site)
                                      and protected_config_matches(target, isolated))
                if (not (interrupted_delete or (managed_site_link(slug)
                        and (linked or protected_config_matches(site, ordinary))))
                        or (os.path.lexists(pool) and not protected_config_matches(pool, legacy_pool))):
                    raise InputError('Unrecognized isolated hosting configuration')
                set_isolated_route(slug, project_id, isolated)
                if interrupted_delete:
                    replace_host_config(enabled, link=site)
                test_project_host_configs()
                reload_isolated_route(slug)
            elif os.path.lexists(legacy_fpm_socket(slug)):
                retire_project_pool(slug)
            pair = pair_for(spec, project_id)
            if pair.status() == 'degraded':
                pair.restart()
            else:
                pair.start()
        except (InputError, OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as exc:
            print(f'Isolated project recovery refused for {slug!r}: {exc}', file=sys.stderr)


def discard_isolated_site(project_id: str) -> None:
    try:
        target = isolated_site_path(project_id)
        target.unlink(missing_ok=True)
        sync_parent(target)
    except OSError:
        print('Warning: leftover protected isolated vhost for ' + project_id, file=sys.stderr)


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


def require_mariadb_available() -> None:
    """Check only for operations explicitly requiring a project MariaDB."""
    try:
        available = (os.access('/usr/bin/mariadb', os.X_OK) and
                     subprocess.run(['/usr/bin/systemctl', 'is-active', '--quiet', 'mariadb.service'],
                                    capture_output=True, timeout=5, check=False).returncode == 0)
    except (OSError, subprocess.TimeoutExpired):
        available = False
    if not available:
        raise InputError('MariaDB nie jest zainstalowana lub uruchomiona. Uruchom ja przed operacja projektu.')


def mariadb_execute(sql: str) -> None:
    try:
        run(['/usr/bin/mariadb', '--protocol=socket', '--batch', '--skip-column-names'], input_text=sql)
    except (OSError, RuntimeError):
        # Avoid reporting an SQL statement containing the freshly created password.
        raise RuntimeError('MariaDB nie wykonala operacji. Sprawdz log serwera.') from None


def create_db(slug: str, *, password: str | None = None) -> tuple[str, str, str]:
    name, user = db_identifiers(slug)
    password = password if password is not None else secrets.token_urlsafe(36)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', password):
        raise RuntimeError('Nieprawidlowe haslo generowanej bazy.')
    # Do not adopt an existing database or drop a pre-existing account if
    # CREATE USER fails. Roll back only resources we know we created.
    created_user = False
    mariadb_execute(f'CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;')
    try:
        mariadb_execute(f"CREATE USER '{user}'@'127.0.0.1' IDENTIFIED BY '{password}';")
        created_user = True
        mariadb_execute(f"GRANT ALL PRIVILEGES ON `{name}`.* TO '{user}'@'127.0.0.1';")
    except Exception:
        try:
            mariadb_execute(f"DROP DATABASE IF EXISTS `{name}`;")
            if created_user:
                mariadb_execute(f"DROP USER IF EXISTS '{user}'@'127.0.0.1';")
        except Exception:
            print('Failed database cleanup for ' + slug, file=sys.stderr)
        raise
    return name, user, password


def drop_db(name: str, user: str) -> None:
    if not re.fullmatch(r'[a-z0-9_]{1,64}', name) or not re.fullmatch(r'[a-z0-9_]{1,64}', user):
        raise InputError('Nieprawidlowe nazwy zasobow MariaDB.')
    # First drop database; a database error must not revoke user access.
    mariadb_execute(f"DROP DATABASE IF EXISTS `{name}`; DROP USER IF EXISTS '{user}'@'127.0.0.1';")


def new_project_id(state: dict[str, Any]) -> str:
    """Assign a registration identity, not a Linux UID or an authorization.

    A slug, domain and retained source directory may all be reused after
    deletion. New registrations must not inherit the previous identity.
    Only the serialized, privileged registry writer generates this value.
    Existing registrations are not rewritten as part of creating a project.
    """
    used = {item['project_id'] for item in state.values()
            if isinstance(item, dict) and isinstance(item.get('project_id'), str)}
    for _ in range(8):
        candidate = secrets.token_hex(16)
        if candidate not in used:
            return candidate
    raise RuntimeError('Nie mozna nadac unikalnej tozsamosci projektu.')


def create_environment(data: dict[str, Any], *, prepared_db: tuple[str, str, str] | None = None) -> dict[str, str]:
    cfg = config()
    slug = check_slug(data.get('slug'))
    name = check_name(data.get('name'))
    domain = check_domain(data.get('domain'))
    webroot = check_webroot(data.get('webroot'))
    local_mail = require_local_mail(data)
    database = data.get('database') is True
    if prepared_db is not None and (not database or prepared_db[:2] != db_identifiers(slug)):
        raise RuntimeError('Niespojna rezerwacja MariaDB.')
    https = data.get('https') is True
    state = read_json(STATE_FILE)
    secrets_state = read_json(SECRET_FILE)
    if slug in state or any(p.get('domain') == domain for p in state.values()):
        raise InputError('Projekt lub domena juz istnieje.')
    project_id = new_project_id(state)
    root = pathlib.Path(cfg['projects_dir']) / slug
    site_file = site_path(slug)
    site_link = site_link_path(slug)
    pool_file = pool_path(slug)
    if any(p.exists() or p.is_symlink() for p in [site_file, site_link, pool_file]):
        raise InputError('Istnieja juz pliki konfiguracyjne dla tego projektu.')
    if database:
        require_mariadb_available()
    generated: list[pathlib.Path] = []
    db_info = None
    pool_created = site_created = link_created = False
    try:
        public = ensure_project_paths(root, webroot, cfg['dev_user'])
        grant_public_access(root, public, cfg['dev_user'])
        if database:
            db_info = prepared_db if prepared_db is not None else create_db(slug)
        if https:
            generated = mkcert_for(slug, domain)
        pool_file.write_text(php_pool(slug, cfg['dev_user'], local_mail), encoding='utf-8')
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
            'project_id': project_id,
            'name': name, 'domain': domain, 'path': str(root), 'webroot': webroot,
            'https': https, 'database': database, 'local_mail': local_mail,
            'db_name': db_info[0] if db_info else '',
            'db_user': db_info[1] if db_info else '',
            'overrides': '', 'extra_locations': '',
        }
        if data.get('engine', 'none') != 'none':
            state[slug].update(engine=data['engine'], starter_kit=data['starter_kit'],
                               starter_kit_label=data.get('starter_kit_label', data['starter_kit']),
                               installer_options=data.get('installer_options', {}),
                               installer_option_labels=data.get('installer_option_labels', []),
                               engine_status='configuring')
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
        if db_info and prepared_db is None:
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

    require_mariadb_available()
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
        except (OSError, RuntimeError):
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
    isolated_pair = None
    if 'isolation_id' in project:
        from project_pilot import pair_for, registered

        if project.get('agent_grants'):
            raise InputError('Najpierw cofnij wszystkie uprawnienia agentow projektu.')
        spec, project_id, _ = registered(slug)
        _, isolated, _ = project_host_configs(slug, project, project_id)
        if not isolated_route_matches(slug, project_id, isolated):
            raise InputError('Niespojna konfiguracja izolowanego projektu. Niczego nie usunieto.')
        isolated_pair = pair_for(spec, project_id)
    elif (not site_file.is_file() or site_file.is_symlink()
          or not pool_file.is_file() or pool_file.is_symlink() or not managed_site_link(slug)):
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
        require_mariadb_available()

    with tempfile.TemporaryDirectory(prefix='.delete-' + slug + '-', dir=STATE_FILE.parent) as work:
        staged = pathlib.Path(work)
        saved_site = staged / 'nginx.conf'
        saved_pool = staged / 'fpm.conf'
        link_removed = False
        restore_runtime = False
        db_dropped = False
        original_state = dict(state)
        original_secrets = dict(secrets_state)
        try:
            if isolated_pair is not None:
                restore_runtime = isolated_pair.status() != 'stopped'
                isolated_pair.stop()
            site_link.unlink()
            link_removed = True
            site_file.rename(saved_site)
            if isolated_pair is None:
                pool_file.rename(saved_pool)
            else:
                sync_parent(site_file)
                sync_parent(site_link)
            run(['/usr/sbin/nginx', '-t'])
            run(['/usr/bin/systemctl', 'reload', 'nginx'])

            # A delayed reload avoids interrupting this live API request.
            if isolated_pair is None:
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
                if restore_runtime:
                    isolated_pair.start()
            except Exception:
                print('Critical: DevStack deletion rollback needs manual Nginx recovery', file=sys.stderr)
            if db_dropped:
                print('Critical: MariaDB was removed before metadata rollback; restore a database backup.', file=sys.stderr)
            raise

    # Remove generated TLS files only AFTER the project is committed as gone.
    # On filesystem errors, leave orphaned certs rather than endangering user code.
    if isolated_pair is not None:
        discard_isolated_site(project_id)
    for certificate in cert_files:
        try:
            certificate.unlink()
        except OSError:
            print('Warning: leftover generated project certificate ' + slug, file=sys.stderr)

    # The editor's previous-version snapshot belongs to this project.
    # Do not remove it before the deletion commits (including SQL removal).
    backup_warning = ''
    try:
        nginx_backup_path(slug).unlink(missing_ok=True)
    except OSError:
        # Do not report a committed project deletion as a failed transaction.
        print('Warning: leftover generated project Nginx backup ' + slug, file=sys.stderr)
        backup_warning = ' Nie udalo sie usunac kopii poprzedniej konfiguracji Nginx.'

    # The committed state is authoritative; queue reconciliation once after deletion.
    hosts_warning = request_windows_hosts_sync()
    return {'message': 'Projekt usuniety z Nginx, PHP-FPM i DevStack' +
            (' wraz z baza MariaDB.' if database else '.') +
            ' Pliki projektu pozostaly na dysku. Windows hosts zsynchronizuje sie automatycznie.' + backup_warning + hosts_warning}


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
    if 'rules' in data:
        if set(data) != {'slug', 'rules'}:
            raise InputError('Nieprawidlowe pola konfiguracji Nginx.')
        validated = check_nginx_rules(data['rules'])
    else:
        # Preserve the existing API and full-file editor for older clients.
        if not {'slug', 'overrides'}.issubset(data) or set(data) - {'slug', 'overrides', 'extra_locations'}:
            raise InputError('Nieprawidlowe pola konfiguracji Nginx.')
        validated = None
    slug = check_slug(data.get('slug'))
    state = read_json(STATE_FILE)
    if slug not in state:
        raise InputError('Nie ma takiego projektu.')
    p = state[slug]
    if 'isolation_id' in p:
        raise InputError('Konfiguracje izolowanego HTTPS moze zmienic administrator po dezaktywacji.')
    if validated is None:
        validated = {
            'overrides': check_overrides(data.get('overrides')),
            'extra_locations': check_extra_locations(data.get('extra_locations', p.get('extra_locations', ''))),
        }
    overrides = validated['overrides']
    assets = validated['extra_locations']
    target = site_path(slug)
    if not target.is_file() or target.is_symlink():
        raise InputError('Plik Nginx nie jest dostepny.')
    before = target.read_text(encoding='utf-8')
    root = pathlib.Path(p['path'])
    pub = root / p['webroot']
    after = nginx_conf(slug, p['domain'], pub, p['https'], overrides, assets)
    if after == before:
        return {'message': 'Brak zmian w konfiguracji.', **validated}
    backup = nginx_backup_path(slug)
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
    return {'message': 'Konfiguracja przetestowana i zastosowana. Kopia poprzedniej wersji zostala zachowana.', **validated}


def update_full_config(data: dict[str, Any]) -> dict[str, str]:
    slug = check_slug(data.get('slug'))
    state = read_json(STATE_FILE)
    if slug not in state:
        raise InputError('Nie ma takiego projektu.')
    if 'isolation_id' in state[slug]:
        raise InputError('Konfiguracje izolowanego HTTPS moze zmienic administrator po dezaktywacji.')
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
        raise InputError('Nieobslugiwany framework.')
    definition = engine_catalog().get(engine)
    if not isinstance(definition, dict):
        raise InputError('Nieobslugiwany framework.')
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


def stop_engine_unit(unit: Any) -> bool:
    if not isinstance(unit, str) or not ENGINE_UNIT.fullmatch(unit):
        return False
    try:
        stopped = subprocess.run(['/usr/bin/systemctl', 'stop', unit + '.service'],
                                 capture_output=True, timeout=10, check=False)
        if stopped.returncode == 0:
            return True
        state = subprocess.run(['/usr/bin/systemctl', 'show', '-p', 'ActiveState',
                                '--value', unit + '.service'], capture_output=True,
                               text=True, timeout=5, check=False)
        return state.returncode == 0 and state.stdout.strip() in ('inactive', 'failed')
    except (OSError, subprocess.TimeoutExpired):
        return False


def sanitize_installer_fields(fields: Any, kit_ids: set[str]) -> list[dict[str, Any]]:
    """Bound the worker's presentation-only schema; flags never cross the RPC."""
    if not isinstance(fields, list) or len(fields) > 12:
        raise RuntimeError('Nieprawidlowe opcje instalatora.')
    known: dict[str, set[str]] = {}
    def token(value: Any) -> bool:
        return isinstance(value, str) and SLUG.fullmatch(value) is not None
    def label(value: Any) -> bool:
        return isinstance(value, str) and 1 <= len(value) <= 100 and value.isprintable()
    for field in fields:
        if (not isinstance(field, dict) or set(field) != {'id', 'label', 'default', 'choices', 'kits', 'when'}
                or not token(field['id']) or field['id'] in known or not label(field['label'])
                or not isinstance(field['choices'], list) or not 1 <= len(field['choices']) <= 8
                or not isinstance(field['kits'], list) or len(field['kits']) > 33
                or any(not token(kit) or kit not in kit_ids for kit in field['kits'])
                or not isinstance(field['when'], dict)):
            raise RuntimeError('Nieprawidlowa definicja opcji instalatora.')
        choices = set()
        for choice in field['choices']:
            if (not isinstance(choice, dict) or set(choice) != {'id', 'label'}
                    or not token(choice['id']) or choice['id'] in choices or not label(choice['label'])):
                raise RuntimeError('Nieprawidlowa wartosc opcji instalatora.')
            choices.add(choice['id'])
        if not token(field['default']) or field['default'] not in choices:
            raise RuntimeError('Nieprawidlowa domyslna opcja instalatora.')
        for key, allowed in field['when'].items():
            if (key not in known or not isinstance(allowed, list) or not 1 <= len(allowed) <= 9
                    or any(not isinstance(v, str) or v not in known[key] | {''} for v in allowed)):
                raise RuntimeError('Nieprawidlowa zaleznosc opcji instalatora.')
        known[field['id']] = choices
    return fields


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
    if 'starter_kits' in value:
        kits = value['starter_kits']
        if kits is not None:
            if not isinstance(kits, list) or len(kits) > 33:
                raise RuntimeError('Nieprawidlowa lista starter kitow.')
            seen = set()
            for item in kits:
                if (not isinstance(item, dict) or set(item) != {'id', 'label'}
                        or not isinstance(item['id'], str) or not SLUG.fullmatch(item['id'])
                        or item['id'] in seen or not isinstance(item['label'], str)
                        or not 1 <= len(item['label']) <= 80 or not item['label'].isprintable()):
                    raise RuntimeError('Nieprawidlowy starter kit.')
                seen.add(item['id'])
            if (result['installed'] is not None and 'none' not in seen
                    or result['installed'] is None and kits):
                raise RuntimeError('Niespojna lista starter kitow.')
        result['starter_kits'] = kits
    if 'installer_options' in value:
        fields = value['installer_options']
        kits = result.get('starter_kits')
        if fields is None:
            if kits:
                raise RuntimeError('Brak definicji opcji instalatora.')
        else:
            fields = sanitize_installer_fields(fields, {kit['id'] for kit in kits or []})
            if not kits and fields:
                raise RuntimeError('Niespojne opcje instalatora.')
        result['installer_options'] = fields
    return result


ENGINE_FAILURE_MESSAGES = {
    'installer_options': 'Wybrane opcje instalatora sa niedostepne lub nie zostaly poprawnie wykonane. Sprawdz formularz i prywatny log.',
    'starter_kits': 'Nie mozna odczytac starter kitow z aktualnego instalatora. Sprawdz jego wersje.',
    'starter_kit_unavailable': 'Wybrany starter kit nie jest juz dostepny. Odswiez formularz i wybierz ponownie.',
    'frontend_node': 'Laravel wymaga Node.js 20.19+ (20.x) lub 22.12+. Sprawdz Node.js w Ubuntu.',
    'frontend_npm': 'Laravel w DevStack wymaga npm 11+. Zaktualizuj npm w Ubuntu przed utworzeniem projektu.',
}


def adapter_call(unit: str, action: str, args: list[str],
                 settings: dict[str, Any] | None = None, timeout: int = 950,
                 engine: str = 'laravel') -> dict[str, Any]:
    try:
        proc = subprocess.run(engine_unit_args(unit, action, args, timeout, engine),
                              input=json.dumps(settings) if settings is not None else '',
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              text=True, timeout=timeout + 5, check=False, close_fds=True)
        if len(proc.stdout) > 16384:
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
        if not stop_engine_unit(unit):
            return {'ok': False, 'keep_db': True}
    except (OSError, ValueError, RuntimeError):
        pass
    return {'ok': False}


def engine_access_allowed() -> bool:
    # The web peer still enforces the authenticated session/CSRF boundary.
    # Only a root-owned explicit policy can enable the passwordless exception.
    try:
        mode = AUTH_MODE_FILE.read_text(encoding='utf-8').strip()
    except FileNotFoundError:
        return True  # Same as PHP: missing auth.mode requires login.
    except (OSError, UnicodeError):
        return False
    if mode != 'disabled':
        return True
    try:
        return ENGINE_ACCESS_FILE.read_text(encoding='utf-8').strip() == 'allow-passwordless'
    except (OSError, UnicodeError):
        return False


def require_engine_auth() -> None:
    if not engine_access_allowed():
        raise InputError('Instalatory wymagaja logowania lub jednorazowej zgody administratora na prace bez hasla.')


def reserve_engine_database(job: dict[str, Any]) -> tuple[str, str, str]:
    if ENGINE_DB_FILE.exists():
        raise InputError('Poprzednia rezerwacja MariaDB wymaga sprawdzenia przez administratora.')
    slug = job['slug']
    name, user = db_identifiers(slug)
    password = secrets.token_urlsafe(36)
    # Private root-owned reservation, separate from the publicly projected job.
    # A crash in the preparing phase is uncertain: do not delete any resource
    # based only on a guessed name. An administrator must reconcile it.
    record = {'id': job['id'], 'slug': slug, 'name': name, 'user': user,
              'password': password, 'phase': 'preparing'}
    write_json_atomic(ENGINE_DB_FILE, record)
    result = create_db(slug, password=password)
    if result != (name, user, password):
        raise RuntimeError('Niespojna rezerwacja MariaDB.')
    record['phase'] = 'ready'
    write_json_atomic(ENGINE_DB_FILE, record)
    return result


def engine_database_record(job: dict[str, Any]) -> dict[str, Any]:
    record = read_json(ENGINE_DB_FILE)
    if not record or record.get('id') != job.get('id'):
        return {}
    slug = check_slug(job.get('slug'))
    if (record.get('slug') != slug
            or (record.get('name'), record.get('user')) != db_identifiers(slug)
            or not isinstance(record.get('password'), str)
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', record['password'])):
        raise RuntimeError('Niespojna rezerwacja MariaDB.')
    return record


def prepared_engine_database(job: dict[str, Any]) -> tuple[str, str, str]:
    record = engine_database_record(job)
    if record.get('phase') != 'ready':
        raise RuntimeError('Brak zakonczonej rezerwacji MariaDB.')
    return record['name'], record['user'], record['password']


def release_engine_database(job: dict[str, Any], *, allow_cleanup: bool) -> str:
    """Keep the registered database, or roll back only a known fresh reservation."""
    try:
        record = engine_database_record(job)
        if not record:
            return ''
        slug = job['slug']
        state = read_json(STATE_FILE)
        secret_state = read_json(SECRET_FILE)
        project = state.get(slug)
        credential = secret_state.get(slug)
        if isinstance(project, dict):
            if (project.get('db_name'), project.get('db_user')) != (record['name'], record['user']) or credential != {'password': record['password']}:
                raise RuntimeError('Niespojna rezerwacja MariaDB.')
            ENGINE_DB_FILE.unlink(missing_ok=True)
            return ''
        if not allow_cleanup or record.get('phase') != 'ready':
            return ' Rezerwacja MariaDB wymaga sprawdzenia przez administratora.'
        if credential is not None and credential != {'password': record['password']}:
            raise RuntimeError('Niespojna rezerwacja MariaDB.')
        drop_db(record['name'], record['user'])
        if credential is not None:
            secret_state.pop(slug)
            write_json_atomic(SECRET_FILE, secret_state)
        ENGINE_DB_FILE.unlink(missing_ok=True)
        return ' Niezarejestrowana baza testowa instalatora zostala wycofana.'
    except Exception:
        return ' Rezerwacja MariaDB wymaga sprawdzenia przez administratora.'


def engine_tool_status(data: dict[str, Any]) -> dict[str, Any]:
    require_engine_auth()
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
                       message: str, settings: dict[str, Any] | None = None) -> None:
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
    require_engine_auth()
    local_mail = require_local_mail(data)
    definition = engine_definition(engine)
    slug = check_slug(data.get('slug'))
    name = check_name(data.get('name'))
    domain = check_domain(data.get('domain'))
    kit = data.get('starter_kit')
    webroot = check_webroot(definition['webroot'])
    if data.get('webroot') not in (None, webroot) or not isinstance(kit, str) or not SLUG.fullmatch(kit):
        raise InputError('Katalog publiczny wynika z frameworka; nieprawidlowy katalog lub wariant instalacji.')
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
    # Discover through the same developer-identity worker as the form.
    # Validate again before creating any database or job. The scaffold worker
    # independently resolves the selected ID against its current CLI help.
    tool = engine_tool_status({'engine': engine})
    if not tool.get('installed'):
        raise InputError('Zainstaluj oficjalny instalator w ustawieniach.')
    if not tool.get('starter_kits'):
        raise InputError('Nie mozna odczytac listy starter kitow z instalatora.')
    choices = {item['id']: item['label'] for item in tool['starter_kits']}
    if kit not in choices:
        raise InputError('Wybrany starter kit nie jest juz dostepny. Odswiez liste.')
    fields = tool.get('installer_options')
    if not isinstance(fields, list):
        raise InputError('Nie mozna odczytac opcji instalatora.')
    try:
        selected = resolve_installer_options(kit, fields, data.get('installer_options', {}))
    except ValueError:
        raise InputError('Wybrane opcje instalatora sa niedostepne lub niezgodne. Odswiez formularz.') from None
    labels = {field['id']: {'label': field['label'],
              'value': next(choice['label'] for choice in field['choices'] if choice['id'] == selected[field['id']])}
              for field in fields if field['id'] in selected}
    # Root-owned reservation (one operation at a time) precedes any install.
    request = {'slug': slug, 'name': name, 'domain': domain, 'webroot': webroot,
               'database': data['database'], 'https': data['https'], 'local_mail': local_mail,
               'engine': engine, 'starter_kit': kit, 'starter_kit_label': choices[kit],
               'installer_options': selected, 'installer_option_labels': list(labels.values())}
    job = {'id': secrets.token_hex(16), 'action': 'create', 'engine': engine,
           'slug': slug, 'request': request}
    if request['database']:
        if ENGINE_DB_FILE.exists():
            raise InputError('Poprzednia rezerwacja MariaDB wymaga sprawdzenia przez administratora.')
        require_mariadb_available()
    driver = 'mariadb' if request['database'] else 'sqlite'
    settings = {'url': ('https://' if request['https'] else 'http://') + domain,
                'installer_options': selected}
    if local_mail:
        settings['local_mail'] = True
    job.update(state='preparing', message='Przygotowywanie zasobow instalatora...')
    write_json_atomic(ENGINE_JOB_FILE, job)
    try:
        if request['database']:
            db_name, db_user, db_password = reserve_engine_database(job)
            settings.update(db_name=db_name, db_user=db_user, db_password=db_password)
        start_engine_phase(job, 'scaffolding', 'scaffold', [slug, request['starter_kit'], driver],
                           'Instalowanie ' + definition['label'] + ' (' + driver + ')...', settings)
    except Exception:
        finish_engine_job(job, success=False, message='Nie udalo sie przygotowac instalacji.')
        raise InputError(engine_job().get('message', 'Nie udalo sie przygotowac instalacji.')) from None
    return {'message': job['message'], 'job_id': job['id']}


def engine_tool_action(data: dict[str, Any]) -> dict[str, Any]:
    require_engine_auth()
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
                      log: str | None = None, allow_db_cleanup: bool = True) -> None:
    if job.get('action') == 'create':
        slug = job['slug']
        state = read_json(STATE_FILE)
        project = state.get(slug)
        if isinstance(project, dict) and project.get('engine') == job.get('engine'):
            project['engine_status'] = 'ready' if success else 'failed'
            write_json_atomic(STATE_FILE, state)
        message += release_engine_database(job, allow_cleanup=allow_db_cleanup)
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
                              log=outcome.get('log'), allow_db_cleanup=not outcome.get('keep_db', False))
            continue
        try:
            result = outcome['result']
            if action == 'scaffold':
                request = job['request']
                driver = 'mariadb' if request['database'] else 'sqlite'
                if (result.get('scaffolded') is not True or result.get('driver') != driver
                        or result.get('webroot') != request['webroot']
                        or result.get('local_mail', False) != request.get('local_mail', False)
                        or result.get('installer_options') != request['installer_options']):
                    raise RuntimeError('Invalid scaffold result')
                # The official installer already completed the default migrations
                # and persisted .env. Publish hosting only after that success.
                prepared = prepared_engine_database(job) if request['database'] else None
                create_environment(request, prepared_db=prepared)
                message = engine_definition(job['engine'])['label'] + ' zainstalowany (' + driver + '). Srodowisko gotowe.'
                if request['installer_options'].get('assets') == 'no':
                    message += ' Zgodnie z wyborem pominieto budowanie zasobow; zbuduj je przed otwarciem aplikacji.'
                finish_engine_job(job, success=True, message=message)
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
        stopped = stop_engine_unit(job['unit']) if job.get('unit') else job.get('state') == 'preparing'
        finish_engine_job(job, success=False, allow_db_cleanup=stopped,
                          message='Operacja przerwana przez restart DevStack. Nie zostala automatycznie ponowiona.')


def notification_snapshot(_: dict[str, Any]) -> dict[str, Any]:
    # Read-only, inexpensive: never scan project trees or reveal credentials.
    try:
        completions = managed_services.completion_results()
    except Exception:
        # A receipt-store fault must not suppress ordinary service monitoring.
        print('DevStack service completion receipts unavailable', file=sys.stderr)
        completions = []
    return {'engine_job': public_engine_job(), 'services': managed_services.public_rows(),
            'service_results': completions}


def show_windows_notification(data: dict[str, Any]) -> dict[str, Any]:
    if set(data) != {'id', 'category', 'kind'} or not all(isinstance(value, str) for value in data.values()):
        raise InputError('Nieprawidlowe powiadomienie Windows.')
    if (not WINDOWS_NOTIFICATION_ID.fullmatch(data['id'])
            or data['category'] not in ('mail', 'project', 'installer', 'service', 'settings')
            or data['kind'] not in ('success', 'error', 'info')):
        raise InputError('Nieprawidlowe powiadomienie Windows.')
    if not WINDOWS_NOTIFICATION_MODE.is_file():
        return {'accepted': False}
    unit = None
    try:
        raw_path = WINDOWS_NOTIFICATION_MODE.read_text(encoding='utf-8').rstrip('\n')
        launcher = pathlib.Path(raw_path)
        # Accept the new canonical C:\\DevStack installation, and retain
        # the old profile layout during an explicit migration/rollback.
        parts = launcher.parts
        legacy = (len(parts) >= 7 and parts[:2] == ('/', 'mnt')
                  and re.fullmatch(r'[a-z]', parts[2])
                  and parts[-3:] == WINDOWS_NOTIFICATION_LAUNCHER)
        if (re.search(r'[\x00-\x1f\x7f]', raw_path) or '..' in parts
                or not (launcher == WINDOWS_NOTIFICATION_TARGET or legacy)
                or not launcher.is_file()):
            return {'accepted': False}
        # As with the framework reader, let PID 1 set the developer's
        # credentials before applying the service restrictions. Changing
        # UID/GID/groups in a child of the hardened helper failed with EPERM
        # on Ubuntu 26.04/WSL. Never execute the Windows user's files as root.
        account = pwd.getpwnam(config()['dev_user'])
        if account.pw_uid == 0 or account.pw_gid == 0:
            return {'accepted': False}
        encoded = base64.b64encode(json.dumps(data, ensure_ascii=True).encode('ascii')).decode('ascii')
        unit = 'devstack-windows-notification-' + secrets.token_hex(8) + '.service'
        # Let PID 1 retain the exit status until we have checked it. A
        # synchronous Type=oneshot start waits for ExecStart to finish;
        # RemainAfterExit retains only unit state, not a running process.
        # Avoid --pipe/--wait/--collect: CI saw ambiguous completion.
        # WorkingDirectory=/mnt/c would add a hard dependency on every
        # ancestor mount, including an optional, absent Azure /mnt disk
        # on CI. Only enter the already validated launcher directory after
        # PID 1 has applied the developer identity; missing paths still fail.
        result = subprocess.run(
            ['/usr/bin/systemd-run', '--system', '--quiet',
             '--service-type=oneshot', '--unit=' + unit,
             '--description=DevStack local Windows notification',
             '--uid=' + str(account.pw_uid), '--gid=' + str(account.pw_gid),
             '--working-directory=/',
             '--expand-environment=no',
             '--property=NoNewPrivileges=yes', '--property=PrivateTmp=yes',
             '--property=PrivateDevices=yes', '--property=ProtectKernelTunables=yes',
             '--property=ProtectKernelModules=yes', '--property=ProtectControlGroups=yes',
             '--property=PartOf=devstackd.service',
             '--property=RemainAfterExit=yes', '--property=CollectMode=inactive',
             '--property=TimeoutStartSec=10s', '--property=TimeoutStopSec=2s',
             '--property=StandardInput=null', '--property=StandardOutput=null',
             '--property=StandardError=null', '--property=UMask=0077',
             '/usr/bin/env', '-i', '--chdir=' + str(launcher.parent),
             'PATH=/usr/bin:/bin', 'LANG=C.UTF-8', str(launcher), encoded],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=15, close_fds=True,
        )
        expected = {'LoadState': 'loaded', 'ActiveState': 'active',
                    'SubState': 'exited', 'Result': 'success',
                    'ExecMainCode': '1', 'ExecMainStatus': '0'}
        state = subprocess.run(
            ['/usr/bin/systemctl', '--system', 'show', '--no-pager',
             *('--property=' + key for key in expected), '--', unit],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, timeout=3, close_fds=True,
        )
        properties = dict(line.split('=', 1) for line in state.stdout.splitlines() if '=' in line)
        accepted = (result.returncode == 0 and state.returncode == 0
                    and all(properties.get(key) == value for key, value in expected.items()))
        if not accepted:
            # Only fixed property names and short, safe status tokens.
            detail = ' '.join(key + '=' + (value if re.fullmatch(r'[a-zA-Z0-9_-]{1,40}', value) else 'unknown')
                              for key in expected for value in [properties.get(key, 'missing')])
            print(f'Windows notification worker failed: start={result.returncode} query={state.returncode} {detail}',
                  file=sys.stderr, flush=True)
        return {'accepted': accepted}
    except (KeyError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        # Never log event content, raw child output, or local account paths.
        print('Windows notification worker unavailable: ' + type(exc).__name__,
              file=sys.stderr, flush=True)
        return {'accepted': False}
    finally:
        # Even a start timeout or a failed unit must not leave a retained
        # transient service. PartOf also ties its lifetime to the daemon.
        if unit is not None:
            for action, timeout in (('stop', 4), ('reset-failed', 3)):
                try:
                    subprocess.run(
                        ['/usr/bin/systemctl', '--system', action, '--', unit],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, timeout=timeout, close_fds=True,
                    )
                except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                    print('Windows notification worker cleanup unavailable: ' + action + ' ' + type(exc).__name__,
                          file=sys.stderr, flush=True)


_instruction_manager = None


def instruction_manager():
    global _instruction_manager
    if _instruction_manager is None:
        from extension_instructions import Manager

        _instruction_manager = Manager(sys.modules[__name__])
    return _instruction_manager


def extension_navigation(data):
    return instruction_manager().navigation(data)


def extension_detail(data):
    return instruction_manager().detail(data)


def extension_preview(data):
    return instruction_manager().preview(data)


def extension_projects(data):
    return instruction_manager().projects(data)


def extension_project_cards(data):
    return instruction_manager().project_cards(data)


def extension_summary(data):
    return instruction_manager().summary(data)


def extension_operation(data):
    return instruction_manager().operate(data)


OPS = {'extension_navigation': extension_navigation, 'extension_detail': extension_detail,
       'extension_preview': extension_preview, 'extension_projects': extension_projects,
       'extension_operation': extension_operation, 'extension_summary': extension_summary,
       'extension_project_cards': extension_project_cards,
       'notification_snapshot': notification_snapshot, 'show_windows_notification': show_windows_notification,
       'status': status, 'service_status': managed_service_status, 'create': create, 'reveal': reveal,
       'engine_tool_status': engine_tool_status, 'engine_tool_action': engine_tool_action,
       'test_project_database': test_project_database, 'delete_project': delete_project,
       'update_config': update_config, 'update_full_config': update_full_config,
       'set_preferences': set_ui_preferences, 'get_preferences': preferences,
       'service_control': service_control, 'set_service_autostart': set_service_autostart,
       'set_widget_services': set_widget_services,
       'public_ca_certificate': public_ca_certificate, 'install_wsl_ca': install_wsl_ca}


PROJECT_WRITERS = {'create', 'delete_project', 'update_config', 'update_full_config', 'engine_tool_action'}


def handle(raw: bytes, *, serialize=False) -> bytes:
    try:
        if len(raw) > 65536:
            raise InputError('Zbyt dlugie zadanie.')
        request = json.loads(raw.decode('utf-8'))
        if not isinstance(request, dict) or not isinstance(request.get('data'), dict):
            raise InputError('Nieprawidlowy format zapytania.')
        operation = request.get('op')
        if operation not in OPS:
            raise InputError('Nieobslugiwana operacja.')
        lock = nullcontext()
        if serialize and operation in PROJECT_WRITERS | {'service_control', 'extension_operation'}:
            from project_pilot import registry_lock

            lock = registry_lock()
        with lock:
            if operation in PROJECT_WRITERS and managed_services.action_pending():
                raise InputError('Poczekaj na zakonczenie operacji uslugi.')
            response = {'ok': True, 'result': OPS[operation](request['data'])}
            if _instruction_manager is not None and operation in PROJECT_WRITERS:
                _instruction_manager.dirty = True
    except (InputError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        response = {'ok': False, 'error': str(exc)[:1200]}
    except Exception:
        print('Unexpected DevStack helper error', file=sys.stderr)
        response = {'ok': False, 'error': 'Nieoczekiwany blad pomocnika systemowego.'}
    return (json.dumps(response, ensure_ascii=True) + '\n').encode('utf-8')


def serve() -> None:
    from project_pilot import registry_lock

    config()
    ensure_project_registry()
    with registry_lock():
        recover_engine_job()
        recover_isolated_projects()
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
    manager = instruction_manager()
    while True:
        if engine_busy():
            with registry_lock():
                poll_engine_results()
            manager.dirty |= not engine_busy()
        manager.tick()
        server.settimeout(1 if engine_busy() else 3)
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
                conn.sendall(handle(bytes(data).split(b'\n', 1)[0], serialize=True))
            except (OSError, socket.timeout):
                continue


if __name__ == '__main__':
    if os.geteuid() != 0:
        sys.exit('Run only as root from the dedicated systemd unit.')
    if sys.argv[1:] == ['--trust-wsl-ca']:
        if os.environ.get('SUDO_USER') != config()['dev_user']:
            sys.exit('Run as the configured developer via sudo.')
        try:
            print(install_wsl_ca({})['message'])
        except (RuntimeError, InputError, OSError) as exc:
            sys.exit(str(exc))
    elif len(sys.argv) == 1:
        serve()
    else:
        sys.exit('Unknown helper arguments.')
