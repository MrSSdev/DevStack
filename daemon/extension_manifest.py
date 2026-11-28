"""Version 1 of the declarative extension catalogue. Reads protected releases only."""

from contextlib import contextmanager, ExitStack
import fcntl
import json
import os
import pathlib
import re
import stat
import sys
from urllib.parse import urlsplit

CONTRACT = 1
ID = re.compile(r'[a-z][a-z0-9-]{0,39}\Z')
VERSION = re.compile(r'[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}\Z')
RELEASE = re.compile(r'releases/release-[0-9A-Za-z]{8}\Z')
# Only administrator-managed installations are registered here. Paths never
# come from a project, an HTTP request, or a release's manifest.
REGISTRY = {'devstack-tools': (pathlib.Path('/opt/devstack-tools'), 'devstack-tools-v1')}
BLOCKS = {'overview', 'instructions', 'projects', 'automations'}
ICONS = {'wrench', 'package', 'book'}
OPERATIONS = {'sync', 'set_automatic', 'migrate'}
EVENTS = {'startup', 'project_created', 'framework_ready', 'project_changed',
          'project_removed', 'extension_installed', 'extension_updated',
          'extension_rolled_back', 'extension_removed', 'manual'}
REQUIREMENTS = {'web', 'local_mail', 'mailpit_installed'}
SERVICES = {'mailpit'}
PATH_PART = re.compile(r'[a-zA-Z0-9@._-]{1,100}\Z')
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class InvalidManifest(ValueError):
    pass


class Incompatible(InvalidManifest):
    pass


def require(condition):
    if not condition:
        raise InvalidManifest('Nieprawidłowy manifest rozszerzenia.')


def obj(value, keys):
    require(isinstance(value, dict) and set(value) == set(keys.split()))
    return value


def plain(value, limit=200):
    require(isinstance(value, str) and 0 < len(value) <= limit and value.isprintable())
    return value


def match(pattern, value):
    require(isinstance(value, str) and pattern.fullmatch(value))
    return value


def items(value, limit=16):
    require(isinstance(value, list) and len(value) <= limit)
    return value


def choices(value, permitted):
    available = items(value)
    require(all(isinstance(item, str) and item in permitted for item in available))
    require(len(set(available)) == len(available))
    return available


def keyed(value, record_keys, limit=16):
    records = [obj(record, record_keys) for record in items(value, limit)]
    identifiers = [match(ID, record['id']) for record in records]
    require(len(set(identifiers)) == len(identifiers))
    return records


def url(value):
    parts = urlsplit(plain(value, 500))
    require(parts.scheme == 'https' and parts.hostname and not parts.username and not parts.password)
    require(not any(c in value for c in '\\ \t\r\n'))
    return value


def validate(raw, extension_id):
    require(isinstance(raw, dict))
    if type(raw.get('contract')) is not int or type(raw.get('core_contract')) is not int:
        raise InvalidManifest('Brak wersji kontraktu.')
    if raw['contract'] != CONTRACT or raw['core_contract'] != CONTRACT:
        raise Incompatible('Wydanie wymaga innej wersji kontraktu DevStack Core.')
    obj(raw, 'id name description version contract core_contract menu view terminal tools instructions legacy automations operations')
    require(match(ID, raw['id']) == extension_id)
    plain(raw['name'])
    plain(raw['description'], 500)
    match(VERSION, raw['version'])
    obj(raw['view'], 'id title sections')
    match(ID, raw['view']['id'])
    plain(raw['view']['title'])
    if raw['menu'] is not None:
        menu = obj(raw['menu'], 'label icon order view')
        plain(menu['label'], 60)
        require(isinstance(menu['icon'], str) and menu['icon'] in ICONS)
        require(type(menu['order']) is int and 0 <= menu['order'] <= 10000)
        require(menu['view'] == raw['view']['id'])
    for section in keyed(raw['view']['sections'], 'id label kind', 8):
        plain(section['label'], 60)
        require(isinstance(section['kind'], str) and section['kind'] in BLOCKS)
    require(bool(raw['view']['sections']))
    for command in keyed(raw['terminal'], 'id label command', 8):
        plain(command['label'], 60)
        plain(command['command'], 250)
    tool_ids = set()
    for tool in keyed(raw['tools'], 'id name description version files service dependencies documentation example'):
        tool_ids.add(tool['id'])
        plain(tool['name'])
        plain(tool['description'], 500)
        match(VERSION, tool['version'])
        files = items(tool['files'], 8)
        require(bool(files))
        for name in files:
            path_parts(name)
        require(tool['service'] is None or isinstance(tool['service'], str) and tool['service'] in SERVICES)
        for dependency in items(tool['dependencies'], 8):
            plain(dependency)
        url(tool['documentation'])
        plain(tool['example'], 300)
    instructions = keyed(raw['instructions'], 'id name description format tools requires reason audience')
    for instruction in instructions:
        plain(instruction['name'])
        plain(instruction['description'], 500)
        require(instruction['format'] == 'agent-skill' and instruction['audience'] == 'trusted-developer')
        require(bool(choices(instruction['tools'], tool_ids)))
        choices(instruction['requires'], REQUIREMENTS)
        plain(instruction['reason'], 300)
    legacy = choices(raw['legacy'], [match(ID, value) for value in items(raw['legacy'], 8)])
    require(not set(legacy).intersection(item['id'] for item in instructions))
    for rule in keyed(raw['automations'], 'id label description events action'):
        plain(rule['label'])
        plain(rule['description'], 500)
        require(bool(choices(rule['events'], EVENTS)) and rule['action'] == 'reconcile_instructions')
    choices(raw['operations'], OPERATIONS)
    return raw


def path_parts(path):
    parts = plain(path, 250).split('/')
    require(len(parts) <= 8 and all(PATH_PART.fullmatch(p) and p not in {'.', '..'} for p in parts))
    return parts


def protected(info):
    require(info.st_uid == 0 and not info.st_mode & 0o022)


def directory(path, parent=None):
    fd = os.open(path, DIRECTORY, dir_fd=parent)
    try:
        protected(os.fstat(fd))
        return fd
    except BaseException:
        os.close(fd)
        raise


def open_file(parent, path):
    here = os.dup(parent)
    try:
        *parents, name = path_parts(path)
        for part in parents:
            child = directory(part, here)
            os.close(here)
            here = child
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=here)
        try:
            info = os.fstat(fd)
            protected(info)
            require(stat.S_ISREG(info.st_mode))
            return fd
        except BaseException:
            os.close(fd)
            raise
    finally:
        os.close(here)


def read(parent, path, limit):
    with os.fdopen(open_file(parent, path), 'rb') as stream:
        value = stream.read(limit + 1)
    require(len(value) <= limit)
    return value.decode('utf-8')


def unique_object(pairs):
    result = dict(pairs)
    require(len(pairs) == len(result))
    return result


def read_release(fd, extension_id):
    build = read(fd, 'BUILD', 65).strip()
    match(re.compile(r'[0-9a-f]{64}\Z'), build)
    raw = json.loads(read(fd, 'manifest.json', 32768), object_pairs_hook=unique_object)
    manifest = validate(raw, extension_id)
    instructions = []
    for item in manifest['instructions']:
        text = read(fd, 'skills/' + item['id'] + '/SKILL.md', 24576)
        require(text.startswith('---\n') and ('\nname: ' + item['id'] + '\n') in text[:1000])
        instructions.append({**item, 'text': text, 'version': manifest['version']})
    for name in manifest['legacy']:
        require(read(fd, 'skills/' + name + '/SKILL.md', 24576).startswith('---\n'))
    tools = []
    for tool in manifest['tools']:
        installed = True
        for path in tool['files']:
            try:
                os.close(open_file(fd, path))
            except FileNotFoundError:
                installed = False
        tools.append({**tool, 'installed': installed})
    return {**manifest, 'build': build, 'state': 'active', 'instructions': instructions, 'tools': tools}


def discover_one(extension_id, base, marker):
    try:
        # Configuration paths are fixed direct children of protected /opt.
        root = directory(base.parent)
        try:
            parent = directory(base.name, root)
        finally:
            os.close(root)
    except FileNotFoundError:
        return {'state': 'absent'}
    try:
        with os.fdopen(open_file(parent, '.lock'), 'rb') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return {'state': 'busy'}
            require(read(parent, '.managed', 128).strip() == marker)
            try:
                current = os.readlink('current', dir_fd=parent)
            except FileNotFoundError:
                return {'state': 'absent'}
            protected(os.stat('current', dir_fd=parent, follow_symlinks=False))
            match(RELEASE, current)
            released = directory('releases', parent)
            try:
                release = directory(current.split('/')[1], released)
                try:
                    try:
                        os.stat('manifest.json', dir_fd=release, follow_symlinks=False)
                    except FileNotFoundError:
                        return {'state': 'legacy'}
                    require(os.readlink('skills', dir_fd=parent) == 'current/skills')
                    protected(os.stat('skills', dir_fd=parent, follow_symlinks=False))
                    return {**read_release(release, extension_id), 'publication': current}
                finally:
                    os.close(release)
            finally:
                os.close(released)
    finally:
        os.close(parent)


@contextmanager
def pin_publications():
    # Callers take the Core registry lock first. The publisher takes only
    # its own lock and never calls Core while holding it.
    with ExitStack() as stack:
        for base, _ in REGISTRY.values():
            try:
                parent = directory(base)
                stack.callback(os.close, parent)
                lock = stack.enter_context(os.fdopen(open_file(parent, '.lock'), 'rb'))
                fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            except (OSError, ValueError):
                # Discovery distinguishes a missing installation from one
                # whose location, metadata or lock failed validation.
                continue
        yield True


def discover():
    result = {}
    for extension_id, (path, marker) in REGISTRY.items():
        try:
            result[extension_id] = discover_one(extension_id, path, marker)
        except Incompatible:
            result[extension_id] = {'state': 'incompatible'}
        except (OSError, ValueError, RecursionError):
            result[extension_id] = {'state': 'invalid'}
    return result


def skills_target(extension_id, instruction_id):
    return str(REGISTRY[extension_id][0] / 'skills' / instruction_id)


if __name__ == '__main__':
    try:
        if len(sys.argv) != 3 or sys.argv[1] not in REGISTRY:
            raise InvalidManifest('Oczekiwano identyfikatora i katalogu wydania.')
        candidate = directory(sys.argv[2])
        try:
            details = read_release(candidate, sys.argv[1])
            require(all(tool['installed'] for tool in details['tools']))
        finally:
            os.close(candidate)
    except (OSError, ValueError, RecursionError) as error:
        sys.exit('DevStack extension candidate: ' + str(error)[:180])
