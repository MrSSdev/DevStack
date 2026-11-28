#!/usr/bin/env python3
"""Administrator controls and private mounting for registered PHP/HTTP."""
from __future__ import annotations

from contextlib import contextmanager
import errno
import fcntl
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys
import time

# The root-owned installation is swapped by the updater.
sys.dont_write_bytecode = True

INSTALLED = Path('/opt/devstack/daemon/project_pilot.py')
MODULES = (
    'project_pilot.py', 'project_isolation_preflight.py',
    'project_runtime_identity.py', 'project_idmap.py', 'project_mounts.py',
    'project_access.py', 'project_data_views.py', 'project_runtime.py',
    'project_http.py', 'project_lifecycle.py',
)


def check_installation():
    if (os.geteuid() != 0 or not sys.flags.isolated
            or Path(__file__).absolute() != INSTALLED):
        raise ValueError('Run the installed pilot with sudo python3 -I')
    for path in (*reversed(INSTALLED.parents), *(INSTALLED.parent / n for n in MODULES)):
        info = path.lstat()
        kind = stat.S_ISDIR if path in INSTALLED.parents else stat.S_ISREG
        if not kind(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Unprotected pilot installation')


if __name__ == '__main__':
    check_installation()
    sys.path.insert(0, str(INSTALLED.parent))

from project_isolation_preflight import (CONFIG, REGISTRY, package_info, project_spec,
                                        protected_control, protected_parents,
                                        read_control_json)
from project_runtime_identity import PROJECT_ID, provision_pilot, verify_binding
from project_idmap import IdMapping, publish_idmapped
from project_mounts import (DIR_FLAGS, PinnedProject, SHARED_DATA, exec_pinned,
                            mount_id, open_directory, verify_data_view, view_name)
from project_data_views import mount_command, validate_shared_tree
from project_runtime import (MAILPIT, SMTP_ADDRESS, SOCKET_ACTIVATE, SOCKET_PROXY,
                             RuntimePlan, fpm_config)
from project_http import ProjectHttp
from project_lifecycle import Component, RuntimePair

ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C'}


@contextmanager
def registry_lock():
    # Both the installed root CLI and daemon serialize with the project
    # registry writer. ExecStart only reads atomic snapshots.
    path = REGISTRY.parent / 'project-operations.lock'
    protected_parents(path)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid or info.st_mode & 0o077 or info.st_nlink != 1:
            raise ValueError('Unsafe project operations lock')
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def journal(project_id):
    if not isinstance(project_id, str) or not PROJECT_ID.fullmatch(project_id):
        raise ValueError('Invalid registration ID')
    base = REGISTRY.parent / ('pilot-' + project_id)
    protected_parents(base)
    return base


def registered(slug, *, project_id=None):
    cfg = read_control_json(CONFIG)
    registry = read_control_json(REGISTRY)
    if project_id is not None:
        journal(project_id)
        matches = [key for key, value in registry.items()
                   if isinstance(value, dict) and value.get('project_id') == project_id]
        if len(matches) != 1:
            raise ValueError('Pilot registration no longer exists')
        slug = matches[0]
    spec = project_spec(cfg, registry, slug)
    if spec['identity'] != 'registered':
        raise ValueError('Project requires a valid registration ID')
    record = registry[slug]
    spec['isolated'] = 'isolation_id' in record
    if spec['isolated'] and record['isolation_id'] != record['project_id']:
        raise ValueError('Isolated route registration mismatch')
    settings = spec['settings']
    if (spec['webroot'] != 'public' or settings['database']
            or settings['overrides'] or settings['extra_locations']
            or not settings['https']):
        raise ValueError('Preview requires standard HTTPS/public without MariaDB or custom Nginx rules')
    account = pwd.getpwnam(spec['developer'])
    if account.pw_uid == 0 or account.pw_gid == 0 or account.pw_dir != '/home/' + account.pw_name:
        raise ValueError('Invalid developer account')
    return spec, record['project_id'], account


def require_backend():
    package = package_info()
    if (package['package'] != 'installed' or package['binary'] != 'ordinary-root-owned'
            or subprocess.run(['/usr/bin/dpkg', '--compare-versions', package['version'],
                               'ge', '0.13.1'], env=ENV, timeout=5).returncode):
        raise ValueError('Reviewed Bubblewrap 0.13.1 or later required')
    try:
        cap = os.getxattr('/usr/bin/bwrap', 'security.capability')
    except OSError as exc:
        if exc.errno not in (errno.ENODATA, errno.ENOTSUP):
            raise
    else:
        if cap:
            raise ValueError('Bubblewrap file capabilities not supported')


def runtime_dir(project_id, kind):
    journal(project_id)
    if kind not in ('php', 'http'):
        raise ValueError('Invalid pilot component')
    return Path('/run') / ('devstack-pilot-' + project_id + '-' + kind)


def plan_for(spec, project_id, *, sendmail=False):
    project = Path('/home') / spec['developer'] / 'projects' / spec['slug']
    php, http = (runtime_dir(project_id, k) for k in ('php', 'http'))
    smtp = php / 'smtp/smtp.sock' if spec['isolated'] and spec['settings']['local_mail'] else None
    return ProjectHttp(RuntimePlan(project, php / 'config/fpm.conf', php / 'sockets',
                                   mapped_data=True, registration=project_id,
                                   smtp_socket=smtp, sendmail=sendmail),
                       http / 'config/nginx.conf', http / 'sockets')


def pair_for(spec, project_id, *, provision=False):
    base = journal(project_id)
    if provision:
        require_backend()
        base.mkdir(mode=0o700, exist_ok=True)
        protected_control(base, directory=True)
        if not os.path.lexists(base / 'identity-p.json'):
            provision_pilot(base, project_id)
    return RegisteredPair(project_id, plan_for(spec, project_id))


class RegisteredPair(RuntimePair):
    """Reuse the existing lifecycle; mounts belong to systemd processes."""
    def __init__(self, project_id, plan=None):
        self.base = journal(project_id)
        self.slot = 'p'
        self.identity = verify_binding(self.base, self.slot)
        self.plan = plan
        self.lockfile = self.base / 'lifecycle.lock'
        user = self.identity['account']
        self.php = Component('php', user, runtime_dir(project_id, 'php') / 'sockets/php.sock',
                             project_id)
        self.http = Component('http', user, runtime_dir(project_id, 'http') / 'sockets/http.sock',
                              project_id)
        self.data = None


class PrivateSources(PinnedProject):
    """Read-only mapped code, explicit mapped writable roots; no host ACL edits."""
    def __init__(self, path):
        super().__init__(path)
        self.writable = {}

    def bind(self, relative, *, writable=False, optional=False):
        if not writable:
            return super().bind(relative, optional=optional)
        if relative not in self.writable:
            raise ValueError('Required writable mapped data is absent')
        fd = os.dup(self.writable[relative])
        self.fds.append(fd)
        return ['--bind-fd', str(fd), str(self.path / relative)]

    def close(self):
        for fd in self.writable.values():
            os.close(fd)
        self.writable.clear()
        super().close()


def mapped_view(source_fd, target, mapping, *, readonly):
    target.mkdir(mode=0o700)
    target_fd = os.open(target, DIR_FLAGS)
    try:
        publish_idmapped(source_fd, target_fd, mapping, readonly=readonly)
    finally:
        os.close(target_fd)
    fd = os.open(target, DIR_FLAGS)
    try:
        verify_data_view(source_fd, fd, mapping.runtime)
        if bool(os.fstatvfs(fd).f_flag & os.ST_RDONLY) != readonly:
            raise ValueError('Unexpected mapped access flags')
        return fd
    except BaseException:
        os.close(fd)
        raise


def launch_stage(name):
    # Fixed stages only: the journal can identify a failed system call
    # without logging .env, source paths, database contents or credentials.
    print('Registered pilot preparation: ' + name, file=sys.stderr, flush=True)


@contextmanager
def prepare_runtime(project_id, kind):
    # Internal preparation shared by the fixed launcher and the disposable
    # mount-only integration test. Never execute project code here.
    if os.geteuid() != 0:
        raise ValueError('Trusted administrator required for mount preparation')
    launch_stage('backend-and-registration')
    spec, _, developer = registered(None, project_id=project_id)
    identity = verify_binding(journal(project_id), 'p')
    mapping = IdMapping(developer.pw_uid, developer.pw_gid, identity['uid'], identity['gid'])
    work = runtime_dir(project_id, kind)
    protected_parents(work)
    protected_control(work, directory=True)
    if stat.S_IMODE(work.stat().st_mode) != 0o711 or any(work.iterdir()):
        raise ValueError('Expected empty systemd runtime directory')
    launch_stage('private-namespace')
    if os.path.samestat(os.stat('/proc/self/ns/mnt'), os.stat('/proc/1/ns/mnt')):
        raise ValueError('PrivateMounts is required')
    mount_command('/usr/bin/mount', '-n', '--make-rprivate', '/')
    if any(any(word.startswith(('shared:', 'master:', 'propagate_from:'))
               for word in line.split(' - ', 1)[0].split()[6:])
           for line in Path('/proc/self/mountinfo').read_text().splitlines()):
        raise ValueError('Mount propagation is not private')

    launch_stage('socket-and-config')
    sendmail = (kind == 'php' and spec['isolated'] and spec['settings']['local_mail']
                and os.path.lexists(MAILPIT))
    if sendmail:
        protected_parents(MAILPIT)
        protected_control(MAILPIT, directory=False)
    plan = plan_for(spec, project_id, sendmail=sendmail)
    if kind == 'php' and plan.runtime.smtp_socket is not None:
        smtp_dir = plan.runtime.smtp_socket.parent
        smtp_dir.mkdir(mode=0o700)
        os.chown(smtp_dir, mapping.runtime_uid, mapping.runtime_gid)
    active = plan.runtime if kind == 'php' else plan
    sockets = work / 'sockets'
    sockets.mkdir(mode=0o700)
    mount_command('/usr/bin/setfacl', '-m',
                  'u:' + identity['account'] + ':rwx,u:www-data:--x', str(sockets))
    mount_command('/usr/bin/setfacl', '-d', '-m',
                  'u:www-data:rwX,g::---,o::---,m::rwx', str(sockets))
    active.config.parent.mkdir(mode=0o755)
    active.config.parent.chmod(0o755)
    active.config.write_text(fpm_config(sendmail=sendmail) if kind == 'php' else plan.nginx_config(),
                             encoding='utf-8')
    active.config.chmod(0o644)
    views = work / 'views'
    views.mkdir(mode=0o711)
    views.chmod(0o711)
    # The original tree is pinned and never chmod/chowned by root. The
    # full mapped root is read-only even before the Bubblewrap bind. PHP
    # receives only the three separate writable clones. HTTP receives
    # only public and published storage descriptors from the read-only root.
    sources = PrivateSources(plan.runtime.project)
    try:
        launch_stage('pin-source')
        with PinnedProject(plan.runtime.project) as original:
            launch_stage('map-code-readonly')
            sources.root = mapped_view(original.root, views / 'code', mapping, readonly=True)
            sources.device = mount_id(sources.root)
            if kind == 'php':
                for relative in SHARED_DATA:
                    launch_stage('map-data-' + view_name(relative))
                    fd = open_directory(original.root, relative, original.device)
                    try:
                        validate_shared_tree(fd, mapping, original.device)
                        sources.writable[relative] = mapped_view(
                            fd, views / view_name(relative), mapping, readonly=False)
                    finally:
                        os.close(fd)
        launch_stage('runtime-argv')
        args = active.argv(sources)
        yield args, sources, mapping
    finally:
        sources.close()


def launch(project_id, kind):
    # Must be the actual ExecStart process, not ExecStartPre: systemd gives
    # each command its own mount namespace. Never attach a mount in PID 1's.
    require_backend()
    endpoint = runtime_dir(project_id, 'php') / 'smtp/smtp.sock' if kind == 'php' else None
    with prepare_runtime(project_id, kind) as (args, sources, mapping):
        # Root only prepares mounts and root-owned config; no project code
        # is evaluated. Irreversibly drop all IDs before bwrap or PHP/Nginx.
        launch_stage('drop-groups')
        os.setgroups([])
        launch_stage('drop-gid')
        os.setresgid(mapping.runtime_gid, mapping.runtime_gid, mapping.runtime_gid)
        launch_stage('drop-uid')
        os.setresuid(mapping.runtime_uid, mapping.runtime_uid, mapping.runtime_uid)
        launch_stage('verify-runtime-credentials')
        values = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines()
                      if ':' in line)
        if (os.getresuid() != (mapping.runtime_uid,) * 3
                or os.getresgid() != (mapping.runtime_gid,) * 3 or os.getgroups()
                or int(values['CapEff'], 16) or int(values['CapPrm'], 16)
                or values['NoNewPrivs'].strip() != '1' or values['Seccomp'].strip() != '2'):
            raise ValueError('Runtime credentials or policy not confirmed')
        launch_stage('exec-bubblewrap')
        os.chdir('/')
        if endpoint is not None and endpoint.parent.is_dir():
            start_smtp_listener(endpoint)
        exec_pinned(args, sources)


def start_smtp_listener(endpoint):
    # Only this fixed, unprivileged process sees the host network. No
    # dependency on Mailpit is started or enabled by socket activation.
    process = subprocess.Popen(
        [SOCKET_ACTIVATE, '--listen=' + str(endpoint), SOCKET_PROXY, SMTP_ADDRESS],
        env=ENV, stdin=subprocess.DEVNULL, close_fds=True)
    deadline = time.monotonic() + 3
    try:
        while process.poll() is None and time.monotonic() < deadline:
            try:
                info = endpoint.lstat()
            except FileNotFoundError:
                time.sleep(0.02)
                continue
            if stat.S_ISSOCK(info.st_mode) and info.st_uid == os.geteuid():
                return
            break
        raise ValueError('Private SMTP listener did not start')
    except BaseException:
        process.terminate()
        process.wait(timeout=5)
        raise


def control(action, target):
    if action == 'stop-id':
        pair = RegisteredPair(target)
        return {'project_id': target, 'status': pair.stop()}
    spec, project_id, _ = registered(target)
    base = journal(project_id)
    if action != 'start' and not os.path.lexists(base):
        return {'project': target, 'project_id': project_id, 'status': 'not-enrolled'}
    pair = pair_for(spec, project_id, provision=action == 'start' and not spec['isolated'])
    state = {'start': pair.start, 'stop': pair.stop, 'status': pair.status,
             'restart': pair.restart}[action]()
    return {'project': target, 'project_id': project_id, 'status': state,
            'preview_socket': str(pair.http.endpoint),
            'isolated_https': spec['isolated'],
            'public_domain_unchanged': not spec['isolated'],
            'smtp_enabled': pair.plan.runtime.smtp_socket is not None,
            'local_mail_setting': spec['settings']['local_mail'],
            'smtp_in_preview': False, 'agent_access': False}


def main():
    if len(sys.argv) == 4 and sys.argv[1] == 'launch':
        launch(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 3:
        action, target = sys.argv[1:]
        with registry_lock():
            if action in ('activate', 'deactivate'):
                protected_control(INSTALLED.with_name('devstackd.py'), directory=False)
                from devstackd import InputError, change_project_isolation
                try:
                    result = change_project_isolation(target, activate=action == 'activate')
                except InputError as exc:
                    raise ValueError(str(exc)) from None
            elif action in ('start', 'stop', 'status', 'stop-id', 'restart'):
                result = control(action, target)
            else:
                raise ValueError('Unknown isolation action')
        print(json.dumps(result, sort_keys=True))
    else:
        raise ValueError('Usage: project_pilot.py activate|deactivate|start|stop|restart|status SLUG; stop-id ID')


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        # Fixed messages are sufficient; never log .env or application data.
        print('Registered pilot refused: ' + type(exc).__name__ + ': ' + str(exc)[:400],
              file=sys.stderr)
        raise SystemExit(2)
