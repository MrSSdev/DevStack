"""Agent Skills filesystem adapter. Run only under the configured developer UID."""

from contextlib import ExitStack, contextmanager
import ctypes
import json
import os
import re
import stat
import sys

SLUG = re.compile(r'[a-z][a-z0-9-]{1,39}\Z')
NAME = re.compile(r'[a-z][a-z0-9-]{0,39}\Z')
TOKEN = re.compile(r'[0-9a-f]{32}\Z')
TARGET = re.compile(r'/opt/[a-z][a-z0-9-]{0,39}/skills/[a-z][a-z0-9-]{0,39}\Z')
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def check(pattern, value):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError('Invalid instruction operation')
    return value


def mount(fd):
    with open('/proc/self/fdinfo/' + str(fd), encoding='ascii') as info:
        return next(line for line in info if line.startswith('mnt_id:')).split()[1]


@contextmanager
def child(parent, name, root_mount=None, create=False):
    if create:
        try:
            os.mkdir(name, 0o755, dir_fd=parent)
        except FileExistsError:
            pass
    fd = os.open(name, DIRECTORY, dir_fd=parent)
    try:
        info = os.fstat(fd)
        if root_mount is not None and (mount(fd) != root_mount or info.st_uid != os.geteuid()):
            raise PermissionError('Unsafe project directory')
        yield fd
    finally:
        os.close(fd)


def entry(fd, name):
    if fd is None:
        return None, None
    try:
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if not stat.S_ISLNK(info.st_mode) or info.st_uid != os.geteuid():
            return info, None
        target = os.readlink(name, dir_fd=fd)
        return info, target
    except FileNotFoundError:
        return None, None


def identity(info):
    return [info.st_dev, info.st_ino]


def link_identity(info):
    # Moving a symlink changes ctime but preserves its inode and mtime.
    return [*identity(info), info.st_mtime_ns]


def remove_link(fd, name, reference, target):
    current, current_target = entry(fd, name)
    if current is not None and current_target == target and link_identity(current) == reference:
        os.unlink(name, dir_fd=fd)
        os.fsync(fd)
        return True
    return False


def publish_link(agents, source, skills, destination):
    # A no-replace rename leaves exactly one link and preserves its inode.
    # Persistent hardlinks would make isolation reject the project tree.
    rename = ctypes.CDLL(None, use_errno=True).renameat2
    if rename(agents, os.fsencode(source), skills, os.fsencode(destination), 1) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    os.fsync(agents)
    os.fsync(skills)


def reconcile_link(agents, skills, name, details, attach):
    name = check(NAME, name)
    target = check(TARGET, details['target'])
    scratch = '.devstack-instruction-' + check(TOKEN, details['token'])
    existing, existing_target = entry(skills, name)
    staged, staged_target = entry(agents, scratch)
    owned = details.get('owned')
    active = owned is not None and existing_target == target and link_identity(existing) == owned
    if staged is not None and (staged_target != target or owned is not None and link_identity(staged) != owned):
        raise PermissionError('Unrecognized staged instruction')
    if not attach:
        if active and remove_link(skills, name, owned, target):
            existing = None
        if staged is not None:
            remove_link(agents, scratch, link_identity(staged), target)
        if existing is not None:
            return 'manual' if existing_target == target else 'conflict'
        return 'detached'
    if active:
        return 'linked'
    if existing is not None:
        return 'manual' if existing_target == target else 'conflict'
    if staged is None:
        os.symlink(target, scratch, dir_fd=agents)
        os.fsync(agents)
        staged, staged_target = entry(agents, scratch)
        if staged_target != target:
            raise PermissionError('Staged instruction changed')
        return link_identity(staged)
    if owned is None:
        return link_identity(staged)
    publish_link(agents, scratch, skills, name)
    current, current_target = entry(skills, name)
    if current_target != target or link_identity(current) != owned:
        raise PermissionError('Published instruction changed')
    return 'linked'


def run_project(projects, root_mount, plan):
    slug = check(SLUG, plan['slug'])
    try:
        with ExitStack() as stack:
            project = stack.enter_context(child(projects, slug, root_mount))
            actual = identity(os.fstat(project))
            if plan['identity'] is not None and plan['identity'] != actual:
                return {'state': 'directory_changed', 'identity': actual}
            # An existing .agents/skills is inspected even in disabled mode.
            # Do not create directories to check or detach instructions.
            try:
                agents = stack.enter_context(child(project, '.agents', root_mount))
            except FileNotFoundError:
                agents = None
            try:
                skills = None if agents is None else stack.enter_context(child(agents, 'skills', root_mount))
            except FileNotFoundError:
                skills = None
            legacy, legacy_receipts, migration = {}, {}, {}
            for name, details in plan['legacy'].items():
                check(NAME, name)
                target = check(TARGET, details['target'])
                info, value = entry(skills, name)
                if info is None:
                    continue
                matched = value == target
                legacy[name] = 'manual' if matched else 'conflict'
                if matched:
                    receipt = link_identity(info)
                    legacy_receipts[name] = receipt
                    if details.get('migrate') == receipt:
                        migration[name] = (receipt, target)
            manual = {name for name, value in legacy.items() if value == 'manual'}
            attach = plan['enabled'] and manual <= set(migration) and bool(plan['desired'])
            if attach and skills is None:
                if agents is None:
                    agents = stack.enter_context(child(project, '.agents', root_mount, create=True))
                skills = stack.enter_context(child(agents, 'skills', root_mount, create=True))
            results, prepared = {}, {}
            for name, details in plan['links'].items():
                try:
                    result = reconcile_link(agents, skills, name, details, attach and name in plan['desired'])
                    if isinstance(result, list):
                        prepared[name] = result
                    else:
                        results[name] = result
                except OSError:
                    results[name] = 'error'
            observed = {}
            for name, target in plan.get('inspect', {}).items():
                check(NAME, name)
                check(TARGET, target)
                if name in plan['links']:
                    continue
                info, actual_target = entry(skills, name)
                if info is not None:
                    observed[name] = 'manual' if actual_target == target else 'conflict'
            migration_failed = False
            migrated = []
            if attach and migration and all(results.get(name) in {'linked', 'manual'} for name in plan['desired']):
                for name, (receipt, target) in migration.items():
                    if remove_link(skills, name, receipt, target):
                        del legacy[name]
                        del legacy_receipts[name]
                        migrated.append(name)
                    else:
                        migration_failed = True
            state = 'error' if migration_failed or 'error' in results.values() else 'complete'
            return {'state': state, 'identity': actual, 'links': results, 'legacy': legacy,
                    'legacy_receipts': legacy_receipts, 'prepared': prepared, 'observed': observed, 'migrated': migrated}
    except FileNotFoundError:
        return {'state': 'missing'}
    except OSError:
        return {'state': 'unsafe_directory'}


def main():
    if os.geteuid() == 0:
        sys.exit('Instruction adapter requires a developer account.')
    raw = sys.stdin.buffer.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        sys.exit('Instruction request exceeds limit.')
    data = json.loads(raw)
    if not isinstance(data, dict) or len(data) > 128 or any(not TOKEN.fullmatch(key) for key in data):
        sys.exit('Invalid instruction batch.')
    import pwd

    account = pwd.getpwuid(os.geteuid())
    if account.pw_dir != '/home/' + account.pw_name:
        sys.exit('Invalid developer home.')
    with child(None, '/home') as home_root, child(home_root, account.pw_name) as home, child(home, 'projects') as projects:
        info = os.fstat(projects)
        if info.st_uid != os.geteuid() or os.fstat(home).st_uid != os.geteuid():
            sys.exit('Invalid projects directory owner.')
        root_mount = mount(projects)
        print(json.dumps({key: run_project(projects, root_mount, plan) for key, plan in data.items()}))


if __name__ == '__main__':
    main()
