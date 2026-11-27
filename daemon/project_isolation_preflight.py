#!/usr/bin/env python3
"""Read-only administrator preflight for registered legacy projects.

Not an enrollment, permission grant, migration, or sandbox launcher. The
registry is authoritative; project metadata is inspected as the developer.
No application files are read, no project code runs, no services are started.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import stat
import subprocess
import sys

INSTALLED = Path('/opt/devstack/daemon/project_isolation_preflight.py')
CONFIG = Path('/etc/devstack/server.json')
REGISTRY = Path('/var/lib/devstack/projects.json')
SLUG = re.compile(r'[a-z][a-z0-9-]{1,39}\Z')
USER = re.compile(r'[a-z_][a-z0-9_-]{0,31}\Z')
WEBROOT = re.compile(r'[a-zA-Z0-9_][a-zA-Z0-9_/-]{0,99}\Z')
DOMAIN = re.compile(r'[a-z][a-z0-9-]{0,39}\.test\Z')
ENV = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'SYSTEMD_PAGER': '', 'SYSTEMD_COLORS': '0'}
DIR_FLAGS = os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
MAX_JSON = 4 * 1024 * 1024
ADVISORY = 'https://ubuntu.com/security/CVE-2026-87766'
LAYOUT_KEYS = ('project', 'webroot', 'front_controller', 'storage',
               'bootstrap_cache', 'database_dir', 'published_storage')
LAYOUT_STATES = frozenset({'present', 'missing', 'unsafe', 'unavailable'})


def validate_webroot(value):
    if (not isinstance(value, str) or not WEBROOT.fullmatch(value)
            or any(p in ('', '.', '..') for p in value.split('/'))):
        raise ValueError('Invalid registered webroot')
    return value


def project_spec(config, registry, slug):
    if not isinstance(slug, str) or not SLUG.fullmatch(slug):
        raise ValueError('Invalid project identifier')
    if slug == 'devstack-source':
        raise ValueError('Administrative source checkout is not an isolation target')
    if not isinstance(config, dict) or not isinstance(registry, dict):
        raise ValueError('Invalid control state')
    user = config.get('dev_user')
    if not isinstance(user, str) or not USER.fullmatch(user) or user in ('root', 'devstackweb', 'www-data'):
        raise ValueError('Invalid developer account')
    base = Path('/home') / user / 'projects'
    if config.get('projects_dir') != str(base):
        raise ValueError('Invalid configured project root')
    project = registry.get(slug)
    if not isinstance(project, dict) or project.get('path') != str(base / slug):
        raise ValueError('Project is not registered at its canonical path')
    domain = project.get('domain')
    if not isinstance(domain, str) or not DOMAIN.fullmatch(domain) or domain == 'devstack.test':
        raise ValueError('Invalid registered domain')
    webroot = validate_webroot(project.get('webroot'))
    flags = {}
    for key in ('https', 'database', 'local_mail'):
        value = project.get(key, False if key == 'local_mail' else None)
        if type(value) is not bool:
            raise ValueError('Invalid registered project settings')
        flags[key] = value
    for key in ('overrides', 'extra_locations'):
        value = project.get(key, '')
        if not isinstance(value, str):
            raise ValueError('Invalid registered Nginx settings')
        flags[key] = bool(value.strip())
    return {'slug': slug, 'developer': user, 'webroot': webroot, 'settings': flags}


def protected_control(path, *, directory):
    s = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022:
        raise ValueError('Control file or directory is not root-protected')


def protected_parents(path):
    for parent in reversed(path.parents):
        protected_control(parent, directory=True)


def read_control_json(path):
    protected_parents(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as stream:
        s = os.fstat(stream.fileno())
        if (not stat.S_ISREG(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022
                or s.st_size > MAX_JSON):
            raise ValueError('Invalid protected control file')
        data = stream.read(MAX_JSON + 1)
    if len(data) > MAX_JSON:
        raise ValueError('Oversized control file')
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError('Invalid protected control data')
    return value


def mount_id(fd):
    # st_dev alone cannot detect a same-filesystem bind mount.
    for line in Path('/proc/self/fdinfo/' + str(fd)).read_text().splitlines():
        if line.startswith('mnt_id:'):
            value = line.split(':', 1)[1].strip()
            if value.isascii() and value.isdigit():
                return int(value)
    raise OSError('Mount identity unavailable')


@contextmanager
def directory_chain(start_fd, parts, expected_mount=None):
    fd = os.dup(start_fd)
    try:
        for part in parts:
            if part in ('', '.', '..') or '/' in part:
                raise ValueError('Invalid path component')
            next_fd = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = next_fd
            if expected_mount is not None and mount_id(fd) != expected_mount:
                raise ValueError('Unexpected mount boundary')
        yield fd
    finally:
        os.close(fd)


def directory_state(fd, parts, expected_mount, uid):
    try:
        with directory_chain(fd, parts, expected_mount) as target:
            return 'present' if os.fstat(target).st_uid == uid else 'unsafe'
    except FileNotFoundError:
        return 'missing'
    except (OSError, ValueError):
        return 'unsafe'


def index_state(fd, public, expected_mount):
    try:
        with directory_chain(fd, public, expected_mount) as parent:
            child = os.open('index.php', os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC,
                            dir_fd=parent)
            try:
                s = os.fstat(child)
                return ('present' if stat.S_ISREG(s.st_mode) and s.st_nlink == 1
                        and mount_id(child) == expected_mount else 'unsafe')
            finally:
                os.close(child)
    except FileNotFoundError:
        return 'missing'
    except (OSError, ValueError):
        return 'unsafe'


def inspect_layout(base, slug, webroot, uid):
    """Metadata snapshot only. No recursive walk, mount, chmod or content read."""
    if not SLUG.fullmatch(slug) or slug == 'devstack-source' or not base.is_absolute():
        raise ValueError('Invalid inspection target')
    public = validate_webroot(webroot).split('/')
    result = dict.fromkeys(LAYOUT_KEYS, 'unavailable')
    root_fd = os.open('/', DIR_FLAGS)
    try:
        try:
            with directory_chain(root_fd, base.parts[1:]) as base_fd:
                device = mount_id(base_fd)
                result['project'] = directory_state(base_fd, [slug], device, uid)
                if result['project'] != 'present':
                    return result
                with directory_chain(base_fd, [slug], device) as project_fd:
                    if os.fstat(project_fd).st_uid != uid:
                        result['project'] = 'unsafe'
                        return result
                    paths = {'webroot': public, 'storage': ['storage'],
                             'bootstrap_cache': ['bootstrap', 'cache'],
                             'database_dir': ['database'],
                             'published_storage': ['storage', 'app', 'public']}
                    for key, parts in paths.items():
                        result[key] = directory_state(project_fd, parts, device, uid)
                    if result['webroot'] == 'present':
                        result['front_controller'] = index_state(project_fd, public, device)
        except FileNotFoundError:
            result = dict.fromkeys(LAYOUT_KEYS, 'unavailable')
            result['project'] = 'missing'
        except (OSError, ValueError):
            result = dict.fromkeys(LAYOUT_KEYS, 'unavailable')
            result['project'] = 'unsafe'
        return result
    finally:
        os.close(root_fd)


def package_info():
    version = None
    status = 'unknown'
    try:
        result = subprocess.run(['/usr/bin/dpkg-query', '-W',
            '-f=${db:Status-Abbrev}\t${Version}\n', 'bubblewrap'],
            env=ENV, text=True, capture_output=True, timeout=5, check=False)
        if result.returncode == 0:
            fields = result.stdout.strip().split('\t')
            if (len(fields) == 2 and fields[0].strip() == 'ii'
                    and re.fullmatch(r'[A-Za-z0-9.+:~_-]{1,100}', fields[1])):
                version = fields[1]
                status = 'installed'
        elif result.returncode == 1:
            status = 'not-found'
    except (OSError, subprocess.SubprocessError):
        pass
    binary = 'missing'
    try:
        s = Path('/usr/bin/bwrap').lstat()
        binary = ('ordinary-root-owned' if stat.S_ISREG(s.st_mode) and s.st_uid == 0
                  and s.st_mode & 0o111 and not s.st_mode & 0o6022 else 'review-required')
    except FileNotFoundError:
        pass
    except OSError:
        binary = 'unknown'
    return {'package': status, 'version': version, 'binary': binary}


def build_report(spec, layout, package):
    if (not isinstance(layout, dict) or set(layout) != set(LAYOUT_KEYS)
            or any(not isinstance(v, str) or v not in LAYOUT_STATES for v in layout.values())):
        raise ValueError('Invalid metadata worker result')
    settings = spec['settings']
    # No production backend is approved. A functional fixture PASS, an
    # arbitrary version string, or a later package upgrade cannot clear this.
    blockers = ['sandbox_backend_security_review', 'production_runtime_not_connected']
    if (any(layout[k] != 'present' for k in ('project', 'webroot', 'front_controller'))
            or any(v in ('unsafe', 'unavailable') for v in layout.values())):
        blockers.append('project_layout_review')
    if spec['webroot'] != 'public':
        blockers.append('custom_webroot_review')
    if settings['database'] or settings['local_mail']:
        blockers.append('project_service_access_review')
    if settings['overrides'] or settings['extra_locations']:
        blockers.append('custom_nginx_rules_review')
    if not settings['https']:
        blockers.append('http_only_configuration_review')
    return {'schema': 1, 'project': spec['slug'], 'status': 'blocked',
            'blockers': blockers, 'layout': layout, 'settings': settings,
            'sandbox': {'package': package, 'advisory': ADVISORY,
                        'reviewed_on': '2026-11-27', 'approved': False},
            'scope': 'metadata-only; no enrollment, activation or migration'}


def inspect_as_developer(spec):
    account = pwd.getpwnam(spec['developer'])
    if account.pw_uid == 0 or account.pw_gid == 0 or account.pw_dir != '/home/' + account.pw_name:
        raise ValueError('Invalid developer identity')
    result = subprocess.run(['/usr/bin/systemd-run', '--quiet', '--wait', '--pipe',
        '--collect', '--service-type=exec',
        '--unit=devstack-isolation-inspect-' + secrets.token_hex(8),
        '--uid=' + account.pw_name, '--gid=' + str(account.pw_gid),
        '--working-directory=/', '--property=NoNewPrivileges=yes',
        '--property=ProtectSystem=strict', '--property=ProtectHome=read-only',
        '--property=PrivateTmp=yes', '--property=PrivateDevices=yes',
        '--property=PrivateNetwork=yes', '--property=RestrictSUIDSGID=yes',
        '--property=ProtectKernelTunables=yes', '--property=ProtectKernelModules=yes',
        '--property=ProtectControlGroups=yes',
        '--property=InaccessiblePaths=-/etc/devstack -/var/lib/devstack -/var/lib/devstack-web -/run/devstack -/mnt/c',
        '--property=RuntimeMaxSec=10s', '--property=UMask=0077',
        '/usr/bin/python3', '-I', str(INSTALLED), '--inspect',
        spec['slug'], spec['webroot']], env=ENV, text=True, capture_output=True,
        timeout=20, check=False)
    if result.returncode != 0 or len(result.stdout) > 4096:
        raise ValueError('Metadata worker unavailable')
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--project', metavar='SLUG', help='Inspect a registered project; requires administrator')
    mode.add_argument('--inspect', nargs=2, metavar=('SLUG', 'WEBROOT'), help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if Path(__file__).absolute() != INSTALLED or not sys.flags.isolated:
            raise ValueError('Run only the installed helper using python3 -I')
        protected_parents(INSTALLED)
        protected_control(INSTALLED, directory=False)
        if args.inspect:
            account = pwd.getpwuid(os.geteuid())
            if (account.pw_uid == 0 or account.pw_gid == 0
                    or account.pw_name in ('devstackweb', 'www-data')
                    or account.pw_dir != '/home/' + account.pw_name):
                raise ValueError('Expected ordinary developer')
            slug, webroot = args.inspect
            data = inspect_layout(Path(account.pw_dir) / 'projects',
                                  slug, webroot, account.pw_uid)
        else:
            if os.geteuid() != 0:
                raise ValueError('Administrator required')
            spec = project_spec(read_control_json(CONFIG), read_control_json(REGISTRY), args.project)
            data = build_report(spec, inspect_as_developer(spec), package_info())
        print(json.dumps(data, ensure_ascii=True, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, RecursionError):
        # Do not echo raw registry values, project paths, command output or secrets.
        print('Isolation preflight could not complete; verify registration and installed helper.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
