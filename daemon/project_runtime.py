"""Experimental Linux PHP runtime plan. Not wired to the installed daemon.

The caller must own the lifecycle, validate the registered project and hold
stable, trusted mount sources. This module is NOT a path authorization API.
The prototype is exercised only with protected disposable CI fixtures.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

FPM = '/usr/sbin/php-fpm8.5'
PHP_CONFIG = Path('/etc/php/8.5')
SOCKET = '/run/project-fpm/php.sock'
RUNTIME_CONFIG = '/run/project-config/fpm.conf'
WRITABLE = ('storage', 'bootstrap/cache', 'database')
SAFE_PATH = re.compile(r'/[A-Za-z0-9._/-]+\Z')


def checked_path(path: Path, *, directory: bool) -> str:
    """Reject ambiguous/symlinked sources; caller must also prevent races."""
    if (not path.is_absolute() or not SAFE_PATH.fullmatch(str(path))
            or path.resolve(strict=True) != path
            or not (path.is_dir() if directory else path.is_file())):
        raise ValueError('Unsafe runtime mount source')
    return str(path)


@dataclass(frozen=True)
class RuntimePlan:
    project: Path
    config: Path
    sockets: Path

    def argv(self) -> list[str]:
        project = checked_path(self.project, directory=True)
        config = checked_path(self.config, directory=False)
        sockets = checked_path(self.sockets, directory=True)
        # Keep the host absolute project path for framework-generated paths.
        # Only the three explicitly selected existing runtime directories
        # may be writable. No global pool configuration is included.
        if (self.project == Path('/') or not self.project.is_relative_to('/var/lib')
                or any(x == self.project or x.is_relative_to(self.project)
                       or self.project.is_relative_to(x)
                       for x in (self.config.parent, self.sockets))
                or self.config.parent == self.sockets
                or self.config.is_relative_to(self.sockets)
                or self.sockets.is_relative_to(self.config.parent)):
            raise ValueError('Overlapping or unsupported prototype mount sources')
        args = [
            '/usr/bin/bwrap',
            '--unshare-user', '--unshare-pid', '--unshare-net',
            '--unshare-ipc', '--unshare-uts', '--disable-userns',
            '--die-with-parent', '--new-session', '--cap-drop', 'ALL',
            '--ro-bind', '/usr', '/usr',
            '--symlink', 'usr/bin', '/bin',
            '--symlink', 'usr/lib', '/lib',
            '--symlink', 'usr/lib64', '/lib64',
            '--proc', '/proc', '--dev', '/dev',
            '--tmpfs', '/tmp', '--dir', '/home', '--dir', '/mnt',
            '--dir', '/run', '--dir', '/etc',
            '--ro-bind', project, project,
        ]
        for relative in WRITABLE:
            target = self.project / relative
            if target.is_symlink() or target.exists():
                source = checked_path(target, directory=True)
                args += ['--bind', source, source]
        for relative in ('fpm/php.ini', 'fpm/conf.d', 'mods-available'):
            source = str(PHP_CONFIG / relative)
            args += ['--ro-bind', source, source]
        args += [
            '--ro-bind', config, RUNTIME_CONFIG,
            '--bind', sockets, '/run/project-fpm',
            '--chdir', project, '--clearenv',
            '--setenv', 'PATH', '/usr/bin:/bin',
            '--setenv', 'HOME', '/tmp',
            '--setenv', 'TMPDIR', '/tmp',
            '--setenv', 'PHP_INI_SCAN_DIR', str(PHP_CONFIG / 'fpm/conf.d'),
            '--', FPM, '--nodaemonize', '--fpm-config', RUNTIME_CONFIG,
            '-c', str(PHP_CONFIG / 'fpm/php.ini'),
        ]
        return args


def fpm_config() -> str:
    # The systemd service sets the non-root identity before exec. The
    # non-root FPM master and its workers retain that identity; neither is
    # launched as www-data or as the developer.
    return f'''[global]
daemonize = no
error_log = /dev/stderr

[project]
listen = {SOCKET}
listen.mode = 0660
pm = ondemand
pm.max_children = 4
pm.process_idle_timeout = 10s
security.limit_extensions = .php
clear_env = yes
catch_workers_output = yes
php_admin_value[expose_php] = Off
php_admin_value[sys_temp_dir] = /tmp
'''
