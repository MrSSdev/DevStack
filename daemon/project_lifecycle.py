"""Lifecycle of the isolated PHP/HTTP pair for protected disposable fixtures.

Not wired to the installed daemon or its RPC. Only the fixture account and
directory layout are accepted. Production mount/source and socket publication,
persistent ownership, migrations and agent authorization are separate gates.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import time

from project_http import ProjectHttp
from project_runtime_identity import USER, verify_binding

SYSTEMCTL = '/usr/bin/systemctl'
SYSTEMD_RUN = '/usr/bin/systemd-run'
ENV = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'SYSTEMD_COLORS': '0',
       'SYSTEMD_PAGER': ''}
BASE = re.compile(r'devstack-runtime-probe-[a-zA-Z0-9_]{8}\Z')
# Apply the accepted WSL socket/syscall profile to the managed runtime
# services, not to the developer, gateway, Windows agent or model.
RUNTIME_SECURITY_PROPERTIES = (
    '--property=NoNewPrivileges=yes',
    '--property=SystemCallArchitectures=native',
    '--property=RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK',
    '--property=SystemCallFilter=~io_uring_setup io_uring_enter io_uring_register',
    '--property=SystemCallErrorNumber=ENOSYS',
)

STOPPED = {'absent', 'inactive', 'failed'}
LIVE = {'active', 'activating', 'deactivating', 'reloading', 'refreshing'}


class LifecycleError(RuntimeError):
    pass


class StartError(LifecycleError):
    def __init__(self, component: str, rolled_back: bool):
        self.component = component
        self.rolled_back = rolled_back
        super().__init__('Start failed at ' + component +
                         ('; stopped both components' if rolled_back
                          else '; rollback not confirmed; inspect fixture'))


def run(argv, *, timeout=10):
    return subprocess.run(argv, env=ENV, text=True, capture_output=True,
                          timeout=timeout, check=False, close_fds=True)


@dataclass(frozen=True)
class Component:
    kind: str
    user: str
    endpoint: Path

    @property
    def unit(self):
        return 'devstack-lifecycle-probe-' + self.user + '-' + self.kind + '.service'

    @property
    def description(self):
        return 'DevStack disposable runtime ' + self.user + ' ' + self.kind


def parse_snapshot(result, component):
    values = dict(line.split('=', 1) for line in result.stdout.splitlines()
                  if '=' in line)
    if values.get('Id') != component.unit:
        raise LifecycleError('Cannot identify systemd unit')
    if values.get('LoadState') == 'not-found':
        if values.get('ActiveState') != 'inactive':
            raise LifecycleError('Inconsistent absent unit')
        return 'absent'
    if (result.returncode != 0 or values.get('LoadState') != 'loaded'
            or values.get('Transient') != 'yes'
            or values.get('User') != component.user
            or values.get('Description') != component.description):
        raise LifecycleError('Unowned or unreadable unit: ' + component.unit)
    state = values.get('ActiveState')
    if state not in (STOPPED - {'absent'}) | LIVE:
        raise LifecycleError('Unknown unit state: ' + component.unit)
    return state


def unit_state(component):
    return parse_snapshot(run([SYSTEMCTL, 'show', '--no-pager',
        '--property=Id,LoadState,ActiveState,Transient,User,Description',
        '--', component.unit]), component)


def socket_probe(component, *, cleanup=False):
    """Probe as the actual unprivileged peer, never connect/unlink as root.

    Stale socket cleanup is restricted to the known endpoint and its runtime
    account. This is not a production solution for hostile socket replacement.
    """
    account = pwd.getpwnam(component.user if cleanup else 'www-data')
    program = '''
import os, socket, stat, sys
p, cleanup = sys.argv[1:]
try:
    mode = os.lstat(p).st_mode
except FileNotFoundError:
    raise SystemExit(10)
if not stat.S_ISSOCK(mode):
    raise SystemExit(2)
with socket.socket(socket.AF_UNIX) as s:
    s.settimeout(1)
    try:
        s.connect(p)
    except (FileNotFoundError, ConnectionRefusedError):
        if cleanup == "1":
            try:
                os.unlink(p)
            except FileNotFoundError:
                pass
        raise SystemExit(10)
raise SystemExit(0)
'''
    result = run(['/usr/bin/setpriv', '--reuid=' + str(account.pw_uid),
                  '--regid=' + str(account.pw_gid), '--init-groups',
                  '--no-new-privs', '--', '/usr/bin/python3', '-I', '-c',
                  program, str(component.endpoint), '1' if cleanup else '0'])
    # Exit 10 is reserved for a verified missing/refused endpoint. Python
    # exceptions and setpriv failures (often exit 1) are not absence.
    if result.returncode not in (0, 10):
        raise LifecycleError('Cannot verify private endpoint: ' + component.kind)
    return result.returncode == 0


def start_component(component, argv):
    deadline = time.monotonic() + 8
    while unit_state(component) != 'absent':
        if time.monotonic() >= deadline:
            raise LifecycleError('Transient unit not collected')
        time.sleep(0.1)
    if socket_probe(component, cleanup=True):
        raise LifecycleError('Endpoint already accepts connections')
    result = run([SYSTEMD_RUN, '--quiet', '--collect', '--service-type=exec',
        '--unit=' + component.unit, '--description=' + component.description,
        '--uid=' + component.user, *RUNTIME_SECURITY_PROPERTIES,
        '--property=KillMode=control-group', '--property=UMask=0007',
        '--property=TimeoutStopSec=5s', '--property=RuntimeMaxSec=180s',
        *argv], timeout=15)
    if result.returncode != 0:
        raise LifecycleError('Cannot start ' + component.kind)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        state = unit_state(component)
        if state in STOPPED:
            raise LifecycleError('Component exited during startup: ' + component.kind)
        if state == 'active' and socket_probe(component):
            return
        time.sleep(0.1)
    raise LifecycleError('Endpoint readiness timeout: ' + component.kind)


def stop_component(component):
    # Never stop an unrelated unit, even if its name happens to collide.
    state = unit_state(component)
    if state != 'absent':
        result = run([SYSTEMCTL, 'stop', '--', component.unit], timeout=20)
        if result.returncode and unit_state(component) != 'absent':
            raise LifecycleError('Cannot stop ' + component.kind)
    if unit_state(component) not in STOPPED or socket_probe(component):
        raise LifecycleError('Component still running: ' + component.kind)


def protected(path, *, directory):
    s = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022:
        raise LifecycleError('Fixture control path is not root-protected')
    if path.resolve(strict=True) != path:
        raise LifecycleError('Symlinked fixture control path')


class RuntimePair:
    """A fixed pair, not a generic systemd/command execution interface."""

    def __init__(self, plan: ProjectHttp, user: str):
        m = USER.fullmatch(user)
        if not m or os.geteuid() != 0:
            raise ValueError('Expected privileged disposable fixture controller')
        slot = m.group(1)
        base = plan.runtime.project.parent
        config = base / ('config-' + slot)
        if (base.parent != Path('/var/lib') or not BASE.fullmatch(base.name)
                or plan.runtime.project != base / ('project-' + slot)
                or plan.runtime.config != config / 'fpm.conf'
                or plan.config != config / 'nginx.conf'
                or plan.runtime.sockets != base / ('sockets-' + slot)
                or plan.sockets != base / ('http-' + slot)):
            raise ValueError('Unexpected fixture layout')
        protected(base, directory=True)
        protected(config, directory=True)
        for file in (plan.runtime.config, plan.config):
            protected(file, directory=False)
        # Their ACLs intentionally grant the fixture identity write access;
        # the parent is root-protected, so the directories cannot be renamed.
        for directory in (plan.runtime.sockets, plan.sockets):
            s = directory.lstat()
            if (not stat.S_ISDIR(s.st_mode) or s.st_uid != 0
                    or directory.resolve(strict=True) != directory):
                raise ValueError('Unexpected fixture socket directory')
        self.identity = verify_binding(base, slot, expected_user=user)
        self.base = base
        self.slot = slot
        self.plan = plan
        self.php = Component('php', user, plan.runtime.sockets / 'php.sock')
        self.http = Component('http', user, plan.sockets / 'http.sock')
        self.lockfile = config / 'lifecycle.lock'

    @property
    def units(self):
        return (self.php.unit, self.http.unit)

    @contextmanager
    def locked(self):
        fd = os.open(self.lockfile, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
                     | os.O_CLOEXEC, 0o600)
        try:
            s = os.fstat(fd)
            if (s.st_uid != 0 or not stat.S_ISREG(s.st_mode)
                    or s.st_mode & 0o077 or s.st_nlink != 1):
                raise LifecycleError('Unsafe fixture lifecycle lock')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise LifecycleError('Project lifecycle operation already in progress') from None
            # Recheck the durable binding on every lifecycle operation. A
            # matching login name alone cannot authorize a reused account.
            if verify_binding(self.base, self.slot) != self.identity:
                raise LifecycleError('Runtime identity binding changed')
            yield
        finally:
            os.close(fd)

    def _status(self):
        states = [unit_state(c) for c in (self.php, self.http)]
        endpoints = [socket_probe(c) for c in (self.php, self.http)]
        if all(s == 'active' for s in states) and all(endpoints):
            return 'running'
        if all(s in STOPPED for s in states) and not any(endpoints):
            return 'stopped'
        return 'degraded'

    def status(self):
        # This is observed process/listener status, not application health.
        with self.locked():
            return self._status()

    def _stop(self):
        # Verify ownership of both first. Failure is never reported as stopped.
        for component in (self.http, self.php):
            unit_state(component)
        errors = []
        for component in (self.http, self.php):
            try:
                stop_component(component)
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                errors.append(exc)
        if errors or self._status() != 'stopped':
            raise LifecycleError('Project stop could not be confirmed')
        return 'stopped'

    def _start(self):
        state = self._status()
        if state == 'running':
            return state
        if state != 'stopped':
            raise LifecycleError('Degraded project; stop explicitly before starting')
        # Launch fixed helpers; project sources are pinned only after the
        # runtime identity has been applied by systemd.
        args = (self.plan.runtime.launch_argv(), self.plan.launch_argv())
        component = self.php
        try:
            start_component(component, args[0])
            component = self.http
            start_component(component, args[1])
            if self._status() != 'running':
                raise LifecycleError('Project readiness not confirmed')
            return 'running'
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            try:
                rolled_back = self._stop() == 'stopped'
            except (OSError, RuntimeError, subprocess.SubprocessError):
                rolled_back = False
            raise StartError(component.kind, rolled_back) from exc

    def start(self):
        with self.locked():
            return self._start()

    def stop(self):
        with self.locked():
            return self._stop()

    def restart(self):
        with self.locked():
            self._stop()
            return self._start()
