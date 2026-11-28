#!/usr/bin/env python3
"""Administrator-only preview of a registered project's isolated PHP/HTTP.

The existing domain/pool and project settings are not switched. No agent
grant is issued. This command uses systemd's service-private mount namespace
and RuntimeDirectory, not a persistent host mount publication broker.
"""
from __future__ import annotations

from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys

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
from project_runtime import RuntimePlan, fpm_config
from project_http import ProjectHttp
from project_lifecycle import Component, RuntimePair

ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C'}


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


def plan_for(spec, project_id):
    project = Path('/home') / spec['developer'] / 'projects' / spec['slug']
    php, http = (runtime_dir(project_id, k) for k in ('php', 'http'))
    return ProjectHttp(RuntimePlan(project, php / 'config/fpm.conf', php / 'sockets',
                                   mapped_data=True, registration=project_id),
                       http / 'config/nginx.conf', http / 'sockets')


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
    plan = plan_for(spec, project_id)
    active = plan.runtime if kind == 'php' else plan
    sockets = work / 'sockets'
    sockets.mkdir(mode=0o700)
    mount_command('/usr/bin/setfacl', '-m',
                  'u:' + identity['account'] + ':rwx,u:www-data:--x', str(sockets))
    mount_command('/usr/bin/setfacl', '-d', '-m',
                  'u:www-data:rwX,g::---,o::---,m::rwx', str(sockets))
    active.config.parent.mkdir(mode=0o755)
    active.config.parent.chmod(0o755)
    active.config.write_text(fpm_config() if kind == 'php' else plan.nginx_config(),
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
        exec_pinned(args, sources)


def control(action, target):
    if action == 'stop-id':
        pair = RegisteredPair(target)
        return {'project_id': target, 'status': pair.stop()}
    spec, project_id, _ = registered(target)
    base = journal(project_id)
    if action == 'start':
        require_backend()
        if not os.path.lexists(base):
            base.mkdir(mode=0o700)
        protected_control(base, directory=True)
        if not os.path.lexists(base / 'identity-p.json'):
            provision_pilot(base, project_id)
    elif not os.path.lexists(base):
        return {'project': target, 'project_id': project_id, 'status': 'not-enrolled'}
    pair = RegisteredPair(project_id, plan_for(spec, project_id))
    state = {'start': pair.start, 'stop': pair.stop, 'status': pair.status}[action]()
    return {'project': target, 'project_id': project_id, 'status': state,
            'preview_socket': str(pair.http.endpoint),
            'public_domain_unchanged': True, 'local_mail_setting': spec['settings']['local_mail'],
            'smtp_in_preview': False, 'agent_access': False}


def main():
    if len(sys.argv) == 4 and sys.argv[1] == 'launch':
        launch(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 3 and sys.argv[1] in ('start', 'stop', 'status', 'stop-id'):
        print(json.dumps(control(sys.argv[1], sys.argv[2]), sort_keys=True))
    else:
        raise ValueError('Usage: project_pilot.py start|stop|status SLUG; stop-id ID')


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        # Fixed messages are sufficient; never log .env or application data.
        print('Registered pilot refused: ' + type(exc).__name__ + ': ' + str(exc)[:400],
              file=sys.stderr)
        raise SystemExit(2)
