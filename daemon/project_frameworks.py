"""Read installed Composer metadata as the unprivileged project owner.

No PHP, Composer plugins, autoload files or project code are executed.
"""
from __future__ import annotations

import json
import os
import pathlib
import pwd
import re
import stat
import sys

MAX_METADATA = 8 * 1024 * 1024
SLUG = re.compile(r'[a-z][a-z0-9-]{1,39}\Z')
PACKAGES = {
    'laravel/framework': 'Laravel',
    'symfony/framework-bundle': 'Symfony',
    'cakephp/cakephp': 'CakePHP',
    'codeigniter4/framework': 'CodeIgniter',
    'yiisoft/yii2': 'Yii',
    'slim/slim': 'Slim',
}
VERSION = re.compile(r'v?([0-9]{1,5}\.[0-9]{1,5}(?:\.[0-9]{1,5}){0,2}(?:[-+][0-9A-Za-z][0-9A-Za-z.+-]{0,47})?)\Z')


def parse_installed(metadata):
    # Composer 2 uses an object, Composer 1 a list. Use installed metadata,
    # not composer.json constraints, composer.lock or the installer version.
    packages = metadata.get('packages') if isinstance(metadata, dict) else metadata
    if not isinstance(packages, list):
        return None
    found = {}
    for package in packages:
        if not isinstance(package, dict):
            continue
        name = package.get('name')
        if not isinstance(name, str) or name not in PACKAGES or name in found:
            continue
        version = package.get('version')
        match = VERSION.fullmatch(version) if isinstance(version, str) else None
        found[name] = match.group(1) if match else None
    for package, label in PACKAGES.items():
        if package in found:
            return {'name': label, 'version': found[package]}
    return None


def read_project(base: pathlib.Path, slug: str):
    if not isinstance(slug, str) or not SLUG.fullmatch(slug):
        return None
    # Open each path segment relative to a verified directory descriptor.
    # Reject links, special files and oversized metadata, including a FIFO.
    dirs = []
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        dirs.append(os.open(base, flags))
        for part in (slug, 'vendor', 'composer'):
            dirs.append(os.open(part, flags, dir_fd=dirs[-1]))
        fd = os.open('installed.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dirs[-1])
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_METADATA:
                return None
            raw = stream.read(MAX_METADATA + 1)
        if len(raw) > MAX_METADATA:
            return None
        return parse_installed(json.loads(raw))
    except (OSError, ValueError, UnicodeError, RecursionError):
        return None
    finally:
        for fd in reversed(dirs):
            os.close(fd)


def main():
    if os.geteuid() == 0:
        return 1
    account = pwd.getpwuid(os.geteuid())
    if account.pw_name == 'devstackweb':
        return 1
    slugs = json.load(sys.stdin)
    if not isinstance(slugs, list) or len(slugs) > 4096:
        return 1
    base = pathlib.Path(account.pw_dir) / 'projects'
    detected = {}
    for slug in slugs:
        framework = read_project(base, slug)
        if framework is not None:
            detected[slug] = framework
    print(json.dumps(detected, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
