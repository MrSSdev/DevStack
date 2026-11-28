"""Core reconciliation and public state for the declarative extension contract."""

from bisect import bisect_right
from datetime import datetime, timezone
import hashlib
import json
import os
import pathlib
import pwd
import re
import secrets
import stat
import subprocess
import sys
import time

from extension_manifest import ID, REGISTRY, discover, pin_publications, skills_target

PROJECT_ID = re.compile(r'[0-9a-f]{32}\Z')
MAILPIT_VERSION = pathlib.Path('/opt/devstack-mailpit/VERSION')
MAILPIT_UNIT = pathlib.Path('/etc/systemd/system/devstack-mailpit.service')
STATUSES = {'linked', 'detached', 'manual', 'conflict', 'error'}
WORKER_STATES = {'complete', 'error', 'unsafe_directory', 'directory_changed', 'missing'}
WRITE_OPERATIONS = {'sync', 'set_automatic', 'migrate'}
OPERATION_LIMIT = 64
OPERATION_TIMEOUT = 3600
RESULT_LIMIT = 64
RESULT_TTL = 30 * 86400


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def installed_service(service):
    if service != 'mailpit':
        return False
    try:
        for path in (MAILPIT_UNIT, MAILPIT_VERSION.parent, MAILPIT_VERSION):
            info = path.lstat()
            if info.st_uid != 0 or info.st_mode & 0o022:
                return False
        if not stat.S_ISDIR(MAILPIT_VERSION.parent.lstat().st_mode) or not MAILPIT_UNIT.is_file():
            return False
        fd = os.open(MAILPIT_VERSION, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as version:
            return stat.S_ISREG(os.fstat(version.fileno()).st_mode) and version.read(64) == b'v1.34.0\n'
    except OSError:
        return False


def desired_instructions(extension, facts):
    available = {tool['id'] for tool in extension['tools'] if tool['installed']}
    return {
        item['id']: item['reason'] for item in extension['instructions']
        if set(item['tools']) <= available and set(item['requires']) <= set(facts)
    }


def selection_facts(project, mailpit):
    facts = []
    if project.get('domain') and project.get('webroot'):
        facts.append('web')
    if project.get('local_mail') is True:
        facts.append('local_mail')
    if mailpit:
        facts.append('mailpit_installed')
    return facts


def project_cards(extension, facts):
    return [card for card in extension.get('project_cards', []) if set(card['requires']) <= set(facts)]


def project_instruction_state(project_id, project, row, extension, facts, global_automatic):
    wanted = desired_instructions(extension, facts) if project.get('engine_status', 'ready') == 'ready' else {}
    names = {item['id'] for item in extension['instructions']} | set(extension['legacy'])
    results = {name: value for name, value in row.get('results', {}).items() if name in names}
    manual = [name for name in row.get('manual', []) if name in names]
    automatic = row.get('automatic', True)
    pending = bool(row.get('pending'))
    return {
        'id': project_id, 'automatic': automatic, 'pending': pending,
        'status': 'pending' if pending else row.get('status', 'pending'),
        'last_sync': row.get('last_sync'), 'event': row.get('event'),
        'wanted': wanted, 'results': results, 'manual': manual, 'conflicts': row.get('conflicts', []),
        'migratable': bool(automatic and global_automatic and wanted and
                           set(manual).intersection(extension['legacy'], row.get('legacy_receipts', {}))),
    }


def run_worker(core, batch):
    user, home = core.engine_identity()
    account = pwd.getpwnam(user)
    if account.pw_gid == 0:
        raise RuntimeError('Nieprawidłowa grupa dewelopera.')
    args = [
        '/usr/bin/systemd-run', '--quiet', '--wait', '--pipe', '--collect',
        '--service-type=exec', '--unit=devstack-instructions-' + secrets.token_hex(8),
        '--uid=' + user, '--gid=' + str(account.pw_gid), '--working-directory=/',
        '--setenv=HOME=' + home, '--setenv=PATH=/usr/bin:/bin',
        '--property=NoNewPrivileges=yes', '--property=ProtectSystem=strict',
        '--property=ProtectHome=read-only', '--property=ReadWritePaths=' + home + '/projects',
        '--property=PrivateNetwork=yes', '--property=PrivateTmp=yes',
        '--property=PrivateDevices=yes', '--property=ProtectKernelTunables=yes',
        '--property=ProtectKernelModules=yes', '--property=ProtectControlGroups=yes',
        '--property=RestrictSUIDSGID=yes',
        '--property=InaccessiblePaths=-/etc/devstack -/var/lib/devstack -/var/lib/devstack-web -/run/devstack -/mnt/c',
        '--property=PartOf=devstackd.service', '--property=RuntimeMaxSec=8s', '--property=UMask=0022',
        '/usr/bin/python3', '-IB', str(pathlib.Path(__file__).with_name('extension_instruction_worker.py')),
    ]
    completed = subprocess.run(args, input=json.dumps(batch), text=True, capture_output=True,
                               timeout=12, check=True, close_fds=True)
    if len(completed.stdout) > 1024 * 1024:
        raise ValueError('Instruction adapter response exceeds limit.')
    result = json.loads(completed.stdout)
    if not isinstance(result, dict) or set(result) != set(batch):
        raise ValueError('Invalid instruction adapter response.')
    for key, report in result.items():
        if not isinstance(report, dict) or report.get('state') not in WORKER_STATES:
            raise ValueError('Invalid project instruction status.')
        identity = report.get('identity')
        if identity is not None and (not isinstance(identity, list) or len(identity) != 2 or
                                     any(type(part) is not int or part < 0 for part in identity)):
            raise ValueError('Invalid project directory identity.')
        for field in ('links', 'legacy', 'observed'):
            values = report.get(field, {})
            if not isinstance(values, dict) or not set(values) <= set(batch[key]['inspect' if field == 'observed' else field]) or any(
                value not in STATUSES for value in values.values()
            ):
                raise ValueError('Invalid instruction link status.')
        if report['state'] == 'complete':
            links, prepared = set(report.get('links', {})), set(report.get('prepared', {}))
            if links & prepared or links | prepared != set(batch[key]['links']):
                raise ValueError('Incomplete successful instruction response.')
        migrated = report.get('migrated', [])
        allowed_migrations = {name for name, details in batch[key]['legacy'].items() if details['migrate']}
        if not isinstance(migrated, list) or any(not isinstance(name, str) or name not in allowed_migrations for name in migrated):
            raise ValueError('Invalid instruction migration result.')
        for field, allowed in (('prepared', 'links'), ('legacy_receipts', 'legacy')):
            receipts = report.get(field, {})
            if not isinstance(receipts, dict) or not set(receipts) <= set(batch[key][allowed]):
                raise ValueError('Invalid instruction identity response.')
            for receipt in receipts.values():
                if not isinstance(receipt, list) or len(receipt) != 3 or any(type(part) is not int or part < 0 for part in receipt):
                    raise ValueError('Invalid instruction link identity.')
    return result


def finish_target(state, row, outcome):
    operation = state['operations'].get(row.pop('operation', None))
    if operation is not None:
        operation['remaining'] -= 1
        counts = operation['outcomes']
        counts[outcome] = counts.get(outcome, 0) + 1


def cancel_unworkable_targets(state, projects, extensions, stamp):
    if not state['operations']:
        return
    for key, project_row in state['projects'].items():
        for extension_id, row in project_row['extensions'].items():
            operation = state['operations'].get(row.get('operation'))
            if operation is None:
                continue
            release = extensions.get(extension_id, {})
            outcome = None
            if key not in projects:
                outcome = 'removed'
            elif release.get('state') != 'active' or release.get('publication', release.get('build')) != operation['publication']:
                outcome = 'unavailable'
            elif projects[key][1].get('engine_status') == 'failed':
                outcome = 'error'
            elif stamp - operation['queued'] >= OPERATION_TIMEOUT:
                outcome = 'expired'
            if outcome is not None:
                finish_target(state, row, outcome)
                row.pop('pending', None)
                row.pop('migrate', None)


def instruction_outcome(operation, status, plan, report, manual):
    if status not in {'synced', 'disabled', 'manual', 'conflict'}:
        return 'error'
    if status == 'conflict':
        return 'conflict'
    if operation['action'] == 'migrate':
        if any(name in manual for name in plan['migration']):
            return 'conflict'
        if set(plan['migration']) - set(report.get('migrated', [])):
            # A disappeared legacy link is not evidence that we removed it.
            return 'unchanged'
    if manual:
        return 'manual'
    if operation['action'] == 'set_automatic':
        if operation['enabled'] and not plan['enabled']:
            return 'inactive'
        if not operation['changed']:
            return 'unchanged'
    return 'done'


def finalize_operations(state, stamp):
    for key, operation in list(state['operations'].items()):
        if operation['remaining']:
            continue
        fields = ('extension', 'slug', 'name', 'action', 'enabled', 'outcomes')
        state['results'][key] = {**{name: operation[name] for name in fields}, 'stamp': stamp}
        del state['operations'][key]
    state['results'] = dict(sorted(
        ((key, value) for key, value in state['results'].items() if stamp - value['stamp'] <= RESULT_TTL),
        key=lambda item: (item[1]['stamp'], item[0]),
    )[-RESULT_LIMIT:])


def new_state():
    return {'schema': 1, 'settings': {}, 'projects': {}, 'extensions': {}, 'automations': {},
            'operations': {}, 'results': {}}


def registered(core, *, identities=False):
    state = core.read_json(core.STATE_FILE)
    projects = {}
    root = pathlib.Path(core.config()['projects_dir'])
    changed = False
    for slug, data in state.items():
        if not core.SLUG.fullmatch(slug) or not isinstance(data, dict) or data.get('path') != str(root / slug):
            continue
        identifier = data.get('project_id')
        if identifier is None and identities:
            identifier = data['project_id'] = core.new_project_id(state)
            changed = True
        if isinstance(identifier, str) and PROJECT_ID.fullmatch(identifier):
            if identifier in projects:
                raise RuntimeError('Powtórzony identyfikator rejestracji projektu.')
            projects[identifier] = (slug, data)
    if changed:
        core.write_json_atomic(core.STATE_FILE, state)
    return projects


def release_event(previous, info):
    state = info['state']
    if state in {'absent', 'legacy'}:
        return 'extension_removed' if previous and previous.get('build') else None
    if state != 'active':
        return None
    publication = info.get('publication', info['build'])
    if not previous or not previous.get('build'):
        return 'extension_installed'
    if publication == previous.get('publication', previous['build']):
        return None
    return 'extension_rolled_back' if publication == previous.get('previous') else 'extension_updated'


def project_event(previous, fingerprint):
    if not previous:
        return 'framework_ready' if fingerprint['engine'] else 'project_created'
    return 'project_changed' if previous != fingerprint else None


def project_fingerprint(project, facts):
    return {'facts': facts, 'engine': project.get('engine') if project.get('engine') != 'none' else None,
            'domain': project.get('domain'), 'https': project.get('https', False),
            'webroot': project.get('webroot'), 'database': project.get('database', False),
            'isolation': project.get('isolation_id') is not None,
            'configuration': hashlib.sha256(json.dumps(
                [project.get('overrides'), project.get('extra_locations')], ensure_ascii=False
            ).encode()).hexdigest()}


def allows(extensions, event):
    return {key for key, info in extensions.items() if info['state'] == 'active'
            and any(event in item['events'] for item in info['automations'])}


class Manager:
    def __init__(self, core):
        self.core = core
        self.startup = True
        self.dirty = True
        self.next_poll = 0
        self.next_audit = 0
        self.audit_pending = set()
        self.cursor = None
        self.error = False

    @property
    def path(self):
        return self.core.STATE_FILE.with_name('extension-instructions.json')

    def load(self):
        data = self.core.read_json(self.path)
        if not data:
            return new_state()
        if data.get('schema') != 1:
            raise RuntimeError('Nieobsługiwana wersja rejestru instrukcji.')
        data.setdefault('operations', {})
        data.setdefault('results', {})
        return data

    def save(self, data):
        self.core.write_json_atomic(self.path, data)

    def completion_results(self):
        cutoff = int(time.time()) - RESULT_TTL
        return [
            {'id': key, **result} for key, result in self.load()['results'].items()
            if result['stamp'] >= cutoff
        ]

    def tick(self):
        clock = time.monotonic()
        if not self.dirty and clock < self.next_poll:
            return
        self.dirty = False
        self.next_poll = clock + 3
        try:
            from project_pilot import registry_lock

            self.error = False
            with registry_lock(blocking=False), pin_publications() as pinned:
                if pinned:
                    self.reconcile(clock)
        except BlockingIOError:
            # Project creation or the terminal isolation tool holds the lock.
            return
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            self.error = True
            # This worker never logs project content, command output or paths.
            print('Instruction reconciliation unavailable: ' + type(error).__name__, file=sys.stderr, flush=True)

    def reconcile(self, clock):
        extensions = discover()
        if any(info['state'] == 'busy' for info in extensions.values()):
            return
        state = self.load()
        active = any(info['state'] == 'active' for info in extensions.values())
        if not active and not state['projects'] and not state['extensions'] and not state['operations']:
            return
        projects = registered(self.core, identities=active)
        stamp = int(time.time())
        cancel_unworkable_targets(state, projects, extensions, stamp)
        mailpit = installed_service('mailpit')
        if clock >= self.next_audit:
            self.next_audit = clock + 30
            self.audit_pending = set(projects)

        extension_events = set()
        for key, info in extensions.items():
            previous = state['extensions'].get(key, {})
            event = release_event(previous, info)
            if event:
                if info['state'] == 'active':
                    state['extensions'][key] = {
                        'build': info['build'], 'publication': info.get('publication', info['build']),
                        'previous': previous.get('publication', previous.get('build')),
                        'event': event, 'changed_at': now(),
                    }
                else:
                    state['extensions'].pop(key, None)
                if key in allows(extensions, event) or event == 'extension_removed':
                    extension_events.add(key)
                    for row in state['projects'].values():
                        current = row['extensions'].get(key)
                        if current:
                            current['release_event'] = event
        rule_owners = {}
        for key, info in extensions.items():
            if info['state'] == 'active':
                for name in [item['id'] for item in info['instructions']] + info['legacy']:
                    rule_owners.setdefault(name, set()).add(key)

        batch = {}
        keys = sorted((key in projects, key) for key in set(projects) | set(state['projects']))
        position = bisect_right(keys, self.cursor) if self.cursor else 0
        self.cursor = None
        for registered_now, key in keys[position:] + keys[:position]:
            audit = key in self.audit_pending
            self.audit_pending.discard(key)
            slug, project = projects.get(key, ('', None))
            row = state['projects'].get(key)
            if project is not None and project.get('engine_status', 'ready') != 'ready':
                continue
            if project is None and not row:
                continue
            if project is not None and not row and not active:
                continue
            if row is None:
                row = state['projects'][key] = {
                    'slug': slug, 'identity': None, 'fingerprint': None, 'extensions': {},
                }
            facts = selection_facts(project, mailpit) if project is not None else []
            fingerprint = project_fingerprint(project, facts) if project is not None else None
            event = project_event(row['fingerprint'], fingerprint) if project is not None else 'project_removed'
            triggers = allows(extensions, event) | extension_events
            if self.startup:
                triggers |= allows(extensions, 'startup')
            if audit:
                triggers |= allows(extensions, 'startup')
            links, wanted, legacy, ready, inspect = {}, set(), {}, {}, {}
            for extension_id, info in extensions.items():
                current = row['extensions'].get(extension_id)
                if not current and info['state'] != 'active':
                    continue
                if current is None:
                    current = row['extensions'][extension_id] = {
                        'links': {}, 'automatic': True, 'wanted': {}, 'status': 'pending',
                    }
                if project is not None and info['state'] in {'invalid', 'incompatible'}:
                    continue
                if project is not None and info['state'] == 'active' and not (
                    extension_id in triggers or current.get('pending') or current['status'] in {'pending', 'error'}
                    or extension_id in row.get('planned', {}) or current.get('release_event')
                ):
                    continue
                enabled = (project is not None and info['state'] == 'active' and current['automatic']
                           and state['settings'].get(extension_id, True))
                selected = desired_instructions(info, facts) if info['state'] == 'active' and project is not None else {}
                collision = any(rule_owners.get(name, set()) - {extension_id} for name in selected)
                current['wanted'] = selected
                authorized = set(selected) if enabled and not collision else set()
                names = []
                if info['state'] == 'active' and project is not None:
                    for item in info['instructions']:
                        name = item['id']
                        if rule_owners[name] == {extension_id}:
                            inspect[name] = skills_target(extension_id, name)
                            names.append(name)
                    for name in info['legacy']:
                        if rule_owners[name] == {extension_id}:
                            legacy[name] = {'target': skills_target(extension_id, name),
                                            'migrate': current.get('migrate', {}).get(name) if authorized else None}
                # Persist ownership before the adapter writes a symlink.
                for name in authorized:
                    current['links'].setdefault(name, {
                        'token': secrets.token_hex(16), 'target': skills_target(extension_id, name),
                    })
                for name, details in current['links'].items():
                    if name in links:
                        collision = True
                        continue
                    links[name] = details
                wanted |= authorized
                if event and (project is None or extension_id in allows(extensions, event)):
                    reason = event
                elif current.get('pending'):
                    reason = 'manual'
                elif current.get('release_event') or extension_id in extension_events:
                    reason = current.get('release_event') or state['extensions'].get(extension_id, {}).get('event', 'startup')
                else:
                    reason = 'startup'
                ready[extension_id] = {
                    'enabled': enabled, 'collision': collision,
                    'installed': info['state'] == 'active',
                    'legacy': info.get('legacy', []), 'names': list(current['links']), 'inspect': names,
                    'event': reason, 'migration': list(current.get('migrate', {})),
                }
            if not ready:
                continue
            row['fingerprint'] = fingerprint
            row['planned'] = ready
            batch[key] = {'slug': row['slug'], 'identity': row['identity'], 'enabled': bool(wanted), 'desired': sorted(wanted),
                          'links': links, 'legacy': legacy, 'inspect': inspect}
            if len(batch) == 64:
                self.cursor = (registered_now, key)
                self.dirty = True
                break
        if not batch:
            finalize_operations(state, stamp)
            if state != self.load():
                self.save(state)
            return
        self.save(state)
        self.startup = False
        try:
            reports = run_worker(self.core, batch)
            ready = {}
            for key, report in reports.items():
                if not report.get('prepared'):
                    continue
                row = state['projects'][key]
                if row['identity'] is None:
                    row['identity'] = report['identity']
                batch[key]['identity'] = row['identity']
                # Batch links and the reloaded journal share each ownership record.
                for name, receipt in report['prepared'].items():
                    batch[key]['links'][name]['owned'] = receipt
                ready[key] = batch[key]
            if ready:
                self.save(state)
                reports.update(run_worker(self.core, ready))
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            reports = {key: {'state': 'error'} for key in batch}
            self.error = True
        outcomes = {}
        for key, report in reports.items():
            row = state['projects'][key]
            if row['identity'] is None:
                row['identity'] = report.get('identity')
            for extension_id, plan in row.pop('planned').items():
                current = row['extensions'][extension_id]
                success = report['state'] == 'complete' and not report.get('prepared')
                retired = key not in projects
                outcome = outcomes.setdefault((extension_id, plan['event']), {'projects': 0, 'errors': 0})
                outcome['projects'] += 1
                outcome['errors'] += not success and not (retired and report['state'] in {'missing', 'directory_changed'})
                if retired and report['state'] in {'missing', 'directory_changed'}:
                    del row['extensions'][extension_id]
                    continue
                results = {name: result for name, result in report.get('links', {}).items() if name in plan['names']}
                manual = {name: value for name, value in report.get('legacy', {}).items() if name in plan['legacy']}
                if success:
                    for name, result in results.items():
                        if result == 'detached' or (result in {'manual', 'conflict'} and name not in batch[key]['desired']):
                            current['links'].pop(name, None)
                    current.pop('migrate', None)
                    current.pop('release_event', None)
                    if retired or not plan['installed'] and not current['links']:
                        if retired:
                            del row['extensions'][extension_id]
                            continue
                current.pop('pending', None)
                results.update({name: value for name, value in report.get('observed', {}).items() if name in plan['inspect']})
                conflicts = sorted(name for name, result in results.items() | manual.items() if result == 'conflict')
                if plan['collision']:
                    conflicts.append('Identyfikator instrukcji używany przez inne rozszerzenie')
                status = ('error' if report['state'] == 'complete' else report['state']) if not success else (
                    'conflict' if conflicts else 'manual' if 'manual' in manual.values() else
                    'synced' if plan['enabled'] else 'disabled')
                manual_names = sorted(name for name, result in results.items() | manual.items() if result == 'manual')
                operation = state['operations'].get(current.get('operation'))
                if operation is not None:
                    finish_target(state, current, instruction_outcome(operation, status, plan, report, manual_names))
                current.update(status=status, last_sync=now(), event=plan['event'], results=results,
                               manual=manual_names, conflicts=conflicts,
                               legacy_receipts={name: value for name, value in report.get('legacy_receipts', {}).items()
                                                if name in plan['legacy']})
            if key not in projects and not row['extensions']:
                del state['projects'][key]
        for (extension_id, event), outcome in outcomes.items():
            latest = state['automations'].setdefault(extension_id, {})
            latest[event] = {**outcome, 'at': now()}
        # Commit receipts with the reconciled state. If this write fails,
        # neither the result nor a finished operation is visible to the collector.
        finalize_operations(state, int(time.time()))
        self.save(state)

    def navigation(self, data):
        if data:
            raise ValueError('Operacja nie przyjmuje parametrów.')
        return [
            {'id': key, 'menu': value['menu'], 'view': value['view']['id'], 'title': value['view']['title']}
            for key, value in discover().items() if value['state'] == 'active' and value['menu']
        ]

    def detail(self, data):
        extension_id = data.get('extension')
        if set(data) != {'extension'} or not isinstance(extension_id, str) or extension_id not in REGISTRY:
            return {'state': 'absent'}
        extension = discover()[extension_id]
        if extension['state'] != 'active':
            return extension
        state = self.load()
        mailpit = installed_service('mailpit')
        mail_status = self.core.mailpit_status() if mailpit else 'not-installed'
        tools = [{**tool, 'ready': tool['runtime_ready'] and (not tool['service'] or mail_status == 'active'),
                  'service_state': mail_status if tool['service'] else None} for tool in extension['tools']]
        users = {item['id']: [] for item in extension['instructions']}
        for key, (slug, project) in registered(self.core).items():
            results = state['projects'].get(key, {}).get('extensions', {}).get(extension_id, {}).get('results', {})
            for name, result in results.items():
                if name in users and result in {'linked', 'manual'}:
                    users[name].append(slug)
        instructions = [
            {**{k: v for k, v in item.items() if k != 'text'}, 'project_count': len(users[item['id']]),
             'project_slugs': sorted(users[item['id']])[:32]} for item in extension['instructions']
        ]
        automation_state = state['automations'].get(extension_id, {})
        rules = [{**rule, 'last_run': max(
            (dict(automation_state[event], event=event) for event in rule['events'] if event in automation_state),
            key=lambda run: run['at'], default=None,
        )} for rule in extension['automations']]
        public = {key: value for key, value in extension.items() if key not in {'legacy_text', 'project_cards'}}
        return {**public, 'tools': tools, 'instructions': instructions, 'automations': rules,
                'automatic': state['settings'].get(extension_id, True),
                'lifecycle': state['extensions'].get(extension_id, {}), 'worker_error': self.error}

    def card_for(self, extension, card_id, project):
        if isinstance(card_id, str) and ID.fullmatch(card_id):
            facts = selection_facts(project, installed_service('mailpit'))
            for card in project_cards(extension, facts):
                if card['id'] == card_id:
                    return card
        raise ValueError('Karta rozszerzenia jest niedostępna dla tego projektu.')

    def preview(self, data):
        extension_id, name = data.get('extension'), data.get('instruction')
        allowed = ({'extension', 'instruction'}, {'extension', 'instruction', 'project', 'card'})
        if set(data) not in allowed or not isinstance(extension_id, str) or extension_id not in REGISTRY or not isinstance(name, str):
            raise ValueError('Nieznana instrukcja.')
        extension = discover()[extension_id]
        if 'project' in data:
            project_id = data['project']
            lookup = registered(self.core)
            if not isinstance(project_id, str) or project_id not in lookup:
                raise ValueError('Projekt nie jest już zarejestrowany.')
            _, project = lookup[project_id]
            card = self.card_for(extension, data['card'], project)
            if not any(section['kind'] == 'instructions' for section in card['sections']):
                raise ValueError('Karta nie udostępnia instrukcji.')
            row = self.load()['projects'].get(project_id, {}).get('extensions', {}).get(extension_id, {})
            visible = desired_instructions(extension, selection_facts(project, installed_service('mailpit')))
            if name not in visible and name not in row.get('manual', []) and row.get('results', {}).get(name) not in {'linked', 'conflict', 'error'}:
                raise ValueError('Instrukcja jest niedostępna dla tego projektu.')
        for item in extension.get('instructions', []):
            if item['id'] == name:
                return {'text': item['text'], 'build': extension['build']}
        if 'project' in data and name in extension.get('legacy_text', {}):
            return {'text': extension['legacy_text'][name], 'build': extension['build']}
        raise ValueError('Instrukcja niedostępna w aktywnym wydaniu.')

    def project_cards(self, data):
        if set(data) != {'slug'} or not isinstance(data['slug'], str) or not self.core.SLUG.fullmatch(data['slug']):
            raise ValueError('Nieprawidłowy projekt.')
        for project_id, (slug, project) in registered(self.core).items():
            if slug == data['slug']:
                break
        else:
            return {'extensions': []}
        state = self.load()
        rows = state['projects'].get(project_id, {}).get('extensions', {})
        facts = selection_facts(project, installed_service('mailpit'))
        found = []
        for extension_id, extension in discover().items():
            if extension['state'] != 'active':
                continue
            cards = project_cards(extension, facts)
            if not cards:
                continue
            global_automatic = state['settings'].get(extension_id, True)
            project_state = project_instruction_state(project_id, project, rows.get(extension_id, {}), extension, facts, global_automatic)
            found.append({
                'id': extension_id, 'build': extension['build'], 'cards': cards, 'automatic': global_automatic,
                'project': {**project_state, 'slug': slug, 'name': project.get('name', slug)},
                'instructions': [{'id': item['id'], 'name': item['name']} for item in extension['instructions']]
                                + [{'id': name, 'name': extension['name'] + ' (starsza instrukcja)'} for name in extension['legacy']],
            })
        return {'extensions': found}

    def projects(self, data):
        extension_id, offset = data.get('extension'), data.get('offset')
        if set(data) != {'extension', 'offset'} or not isinstance(extension_id, str) or extension_id not in REGISTRY or type(offset) is not int or not 0 <= offset <= 100000:
            raise ValueError('Nieprawidłowe zapytanie o projekty.')
        extension = discover()[extension_id]
        if extension['state'] != 'active':
            return {'projects': [], 'next': None}
        lookup = registered(self.core)
        state = self.load()
        ordered = sorted(lookup.items(), key=lambda item: item[1][0])
        page = ordered[offset:offset + 16]
        frameworks = self.core.project_frameworks([slug for _, (slug, _) in page])
        projects = []
        mailpit = installed_service('mailpit')
        global_automatic = state['settings'].get(extension_id, True)
        for key, (slug, project) in page:
            row = state['projects'].get(key, {}).get('extensions', {}).get(extension_id, {})
            detected = frameworks.get(slug, {})
            service_names = [label for label, present in [
                ('HTTP', True), ('HTTPS', project.get('https')), ('MariaDB', project.get('database')),
                ('SQLite', project.get('engine') == 'laravel' and not project.get('database')),
                ('Poczta lokalna', project.get('local_mail')),
            ] if present]
            projects.append({
                **project_instruction_state(key, project, row, extension, selection_facts(project, mailpit), global_automatic),
                'slug': slug, 'name': project.get('name', slug),
                'technologies': ['PHP'] + ([detected['name'] + (' ' + detected['version'] if detected['version'] else '')] if detected
                                          else ['Laravel'] if project.get('engine') == 'laravel' else []),
                'services': service_names,
            })
        return {'projects': projects, 'next': offset + len(page) if offset + len(page) < len(ordered) else None}

    def summary(self, data):
        if set(data) != {'slug'} or not isinstance(data['slug'], str) or not self.core.SLUG.fullmatch(data['slug']):
            raise ValueError('Nieprawidłowy projekt.')
        project_id = next((key for key, (slug, _) in registered(self.core).items() if slug == data['slug']), None)
        if project_id is None:
            return []
        state = self.load()
        rows = state['projects'].get(project_id, {}).get('extensions', {})
        return [
            {'id': key, 'title': info['view']['title'], 'status': rows.get(key, {}).get('status', 'pending'),
             'count': sum(value == 'linked' for value in rows.get(key, {}).get('results', {}).values())}
            for key, info in discover().items() if info['state'] == 'active' and info['instructions']
        ]

    def operate(self, data):
        extension_id, action, project_id = (data.get(key) for key in ('extension', 'action', 'project'))
        if not isinstance(extension_id, str) or extension_id not in REGISTRY or not isinstance(action, str) or action not in WRITE_OPERATIONS:
            raise ValueError('Nieznana operacja rozszerzenia.')
        expected = {'extension', 'action'} | ({'project'} if 'project' in data else set())
        if 'card' in data:
            expected.add('card')
        if action == 'set_automatic':
            expected.add('enabled')
        if set(data) != expected or action == 'set_automatic' and type(data['enabled']) is not bool:
            raise ValueError('Nieprawidłowe parametry synchronizacji.')
        lookup = registered(self.core)
        if 'project' in data and (not isinstance(project_id, str) or project_id not in lookup):
            raise ValueError('Projekt nie jest już zarejestrowany.')
        if ('card' in data or action == 'migrate') and not project_id:
            raise ValueError('Wskaż aktualny projekt.')
        extension = discover()[extension_id]
        if extension['state'] != 'active' or action not in extension['operations']:
            raise ValueError('Operacja niedostępna dla aktywnego wydania.')
        if 'card' in data:
            card = self.card_for(extension, data['card'], lookup[project_id][1])
            if not any(action in section['operations'] for section in card['sections']):
                raise ValueError('Karta nie udostępnia tej operacji.')
        if action == 'sync' and extension_id not in allows({extension_id: extension}, 'manual'):
            raise ValueError('Rozszerzenie nie obsługuje ręcznej synchronizacji.')
        state = self.load()
        if len(state['operations']) >= OPERATION_LIMIT:
            raise ValueError('Za dużo oczekujących zleceń synchronizacji.')
        slug, project = lookup[project_id] if project_id else ('', {})
        name = project.get('name')
        if not isinstance(name, str) or not name:
            name = slug
        receipt = secrets.token_hex(16)
        operation = {
            'extension': extension_id, 'publication': extension.get('publication', extension['build']),
            'slug': slug, 'name': name[:70], 'action': action, 'enabled': data.get('enabled'),
            'changed': False, 'queued': int(time.time()), 'remaining': 0, 'outcomes': {},
        }
        if action == 'set_automatic' and project_id is None:
            operation['changed'] = state['settings'].get(extension_id, True) != data['enabled']
            state['settings'][extension_id] = data['enabled']
        for key, (slug, project) in lookup.items():
            if project_id and key != project_id:
                continue
            parent = state['projects'].setdefault(key, {'slug': slug, 'identity': None, 'fingerprint': None, 'extensions': {}})
            row = parent['extensions'].setdefault(extension_id, {'links': {}, 'automatic': True, 'wanted': {}, 'status': 'pending'})
            if action == 'set_automatic' and project_id:
                operation['changed'] = row['automatic'] != data['enabled']
                row['automatic'] = data['enabled']
            if action == 'migrate':
                facts = selection_facts(project, installed_service('mailpit'))
                project_state = project_instruction_state(key, project, row, extension, facts, state['settings'].get(extension_id, True))
                if not project_state['migratable']:
                    raise ValueError('Włącz zarządzanie i sprawdź ręczne dowiązanie przed migracją.')
                row['migrate'] = {name: receipt for name, receipt in row.get('legacy_receipts', {}).items()
                                  if name in extension['legacy']}
            elif row.get('operation'):
                row.pop('migrate', None)
            finish_target(state, row, 'superseded')
            row['operation'] = receipt
            row['pending'] = True
            operation['remaining'] += 1
        state['operations'][receipt] = operation
        self.save(state)
        self.dirty = True
        return {'receipt': receipt, 'project_slug': operation['slug'], 'project_name': operation['name'],
                'message': 'Zapisano zlecenie synchronizacji instrukcji.'}
