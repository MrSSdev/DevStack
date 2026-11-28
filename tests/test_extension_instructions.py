import copy
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'daemon'))
import extension_instruction_worker as worker
import extension_instructions as instructions
import extension_manifest as contract

TOOLS = ROOT / 'extensions/devstack-tools'
MANIFEST = json.loads((TOOLS / 'manifest.json').read_text())
TARGET = '/opt/devstack-tools/skills/devstack-playwright'
LEGACY = '/opt/devstack-tools/skills/devstack-tools'


class ContractTests(unittest.TestCase):
    def test_compatible_manifest_and_rejected_values(self):
        self.assertEqual(contract.validate(MANIFEST, 'devstack-tools')['name'], 'DevStack Tools')
        for mutator, exception in [
            (lambda m: m.update({'id': 'impostor'}), contract.InvalidManifest),
            (lambda m: m.update({'core_contract': 2}), contract.Incompatible),
            (lambda m: m.update({'contract': True}), contract.InvalidManifest),
            (lambda m: m['tools'][0].update({'files': ['../file']}), contract.InvalidManifest),
            (lambda m: m['tools'][0].update({'documentation': 'javascript:alert(1)'}), contract.InvalidManifest),
            (lambda m: m['instructions'][0].update({'audience': 'agent'}), contract.InvalidManifest),
            (lambda m: m['operations'].append('execute'), contract.InvalidManifest),
            (lambda m: m['view']['sections'][0].update({'kind': 'html'}), contract.InvalidManifest),
            (lambda m: m['automations'][0].update({'action': '/bin/sh'}), contract.InvalidManifest),
        ]:
            with self.subTest(mutator=mutator):
                invalid = copy.deepcopy(MANIFEST)
                mutator(invalid)
                with self.assertRaises(exception):
                    contract.validate(invalid, 'devstack-tools')

    @unittest.skipUnless(os.geteuid() == 0, 'Protected release fixture needs a disposable root')
    def test_protected_active_snapshot_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / 'devstack-tools'
            release = base / 'releases/release-Abc123xy'
            release.mkdir(parents=True)
            shutil.copytree(TOOLS / 'skills', release / 'skills')
            shutil.copyfile(TOOLS / 'manifest.json', release / 'manifest.json')
            (release / 'BUILD').write_text('c' * 64 + '\n')
            (base / 'current').symlink_to('releases/' + release.name)
            (base / 'skills').symlink_to('current/skills')
            (base / '.managed').write_text('devstack-tools-v1\n')
            (base / '.lock').touch()
            self.assertEqual(contract.discover_one('devstack-tools', base, 'devstack-tools-v1')['state'], 'active')
            (release / 'manifest.json').unlink()
            (release / 'manifest.json').symlink_to(TOOLS / 'manifest.json')
            with self.assertRaises(OSError):
                contract.discover_one('devstack-tools', base, 'devstack-tools-v1')

    def test_selection_uses_config_and_installation(self):
        active = {**MANIFEST, 'tools': [{**tool, 'installed': True} for tool in MANIFEST['tools']]}
        web = {'domain': 'demo.test', 'webroot': 'public'}
        selected = instructions.desired_instructions
        self.assertEqual(set(selected(active, instructions.selection_facts(web, True))), {'devstack-playwright'})
        web['local_mail'] = True
        self.assertEqual(set(selected(active, instructions.selection_facts(web, False))), {'devstack-playwright'})
        self.assertEqual(set(selected(active, instructions.selection_facts(web, True))),
                         {'devstack-playwright', 'devstack-mail'})


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / 'webapp'
        self.project.mkdir()
        self.skills = self.project / '.agents/skills'
        self.link = self.skills / 'devstack-playwright'
        self.plan = {'slug': 'webapp', 'identity': None, 'enabled': True, 'desired': ['devstack-playwright'],
                     'links': {'devstack-playwright': {'token': 'a' * 32, 'target': TARGET}}, 'legacy': {}}

    def reconcile(self, plan=None):
        with worker.child(None, str(self.root)) as projects:
            return worker.run_project(projects, worker.mount(projects), plan or self.plan)

    def prepare(self):
        report = self.reconcile()
        self.assertEqual(report['state'], 'complete', report)
        self.assertFalse(self.link.is_symlink())
        self.plan['identity'] = report['identity']
        self.plan['links']['devstack-playwright']['owned'] = report['prepared']['devstack-playwright']

    def test_replay_prepared_link_and_last_known_identity(self):
        self.prepare()
        original = self.reconcile()
        self.assertEqual(original['links'], {'devstack-playwright': 'linked'})
        self.assertEqual(self.link.readlink(), Path(TARGET))
        self.assertEqual(self.link.lstat().st_nlink, 1)
        self.assertEqual(self.reconcile(), original)
        self.plan['enabled'] = False
        self.assertEqual(self.reconcile()['links'], {'devstack-playwright': 'detached'})
        self.assertFalse(self.link.is_symlink())
        self.assertTrue(self.skills.is_dir())

    def test_matching_user_replacement_remains_manual(self):
        self.prepare()
        self.reconcile()
        spare = self.link.with_name('replacement')
        spare.symlink_to(TARGET)
        spare.replace(self.link)
        self.plan['enabled'] = False
        self.assertEqual(self.reconcile()['links'], {'devstack-playwright': 'manual'})
        self.assertTrue(self.link.is_symlink())

    def test_legacy_migration_only_after_replacements_are_linked(self):
        self.skills.mkdir(parents=True)
        old = self.skills / 'devstack-tools'
        old.symlink_to(LEGACY)
        self.plan['legacy']['devstack-tools'] = {'target': LEGACY, 'migrate': None}
        first = self.reconcile()
        self.assertEqual(first['legacy'], {'devstack-tools': 'manual'})
        self.assertFalse(self.link.is_symlink())
        self.plan['legacy']['devstack-tools']['migrate'] = first['legacy_receipts']['devstack-tools']
        self.prepare()
        self.assertTrue(old.is_symlink())
        self.assertEqual(self.reconcile()['legacy'], {})
        self.assertFalse(old.is_symlink())
        self.assertTrue(self.link.is_symlink())

    def test_manual_links_are_visible_with_automation_disabled(self):
        self.skills.mkdir(parents=True)
        self.link.symlink_to(TARGET)
        self.plan.update(enabled=False, links={}, inspect={'devstack-playwright': TARGET})
        self.assertEqual(self.reconcile()['observed'], {'devstack-playwright': 'manual'})
        self.assertTrue(self.link.is_symlink())

    def test_conflict_or_directory_escape_preserves_user_content(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (self.project / '.agents').symlink_to(outside)
        self.assertEqual(self.reconcile()['state'], 'unsafe_directory')
        self.assertEqual(list(outside.iterdir()), [])
        (self.project / '.agents').unlink()
        self.skills.mkdir(parents=True)
        self.link.write_text('own instruction')
        self.assertEqual(self.reconcile()['links'], {'devstack-playwright': 'conflict'})
        self.assertEqual(self.link.read_text(), 'own instruction')
        self.plan['identity'] = [0, 0]
        self.assertEqual(self.reconcile()['state'], 'directory_changed')
        self.plan['slug'] = '../outside'
        with self.assertRaises(ValueError):
            self.reconcile()


class ManagerTests(unittest.TestCase):
    def test_events_crash_recovery_settings_and_retired_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_root = root / 'webapp'
            project_root.mkdir()
            registry = root / 'projects.json'
            prior = '1' * 32
            current = '2' * 32

            def registered(project_id):
                return {'webapp': {'project_id': project_id, 'path': str(project_root), 'domain': 'webapp.test',
                                   'webroot': 'public', 'local_mail': True}}

            def read(path):
                return json.loads(path.read_text()) if path.exists() else {}

            def write(path, value):
                path.write_text(json.dumps(value))

            core = SimpleNamespace(STATE_FILE=registry, SLUG=re.compile(r'webapp'), read_json=read,
                                   write_json_atomic=write, config=lambda: {'projects_dir': tmp})
            manager = instructions.Manager(core)
            ext = {**MANIFEST, 'build': 'a' * 64, 'state': 'active',
                   'tools': [{**tool, 'installed': True} for tool in MANIFEST['tools']]}
            active = {'devstack-tools': ext}

            interrupted = True

            def adapter(_, plans):
                nonlocal interrupted
                with worker.child(None, tmp) as fd:
                    if interrupted and any(plan['identity'] for plan in plans.values()):
                        interrupted = False
                        raise OSError('interrupted after preparing')
                    return {key: worker.run_project(fd, worker.mount(fd), plan) for key, plan in plans.items()}

            with patch.object(instructions, 'discover', return_value=active), \
                 patch.object(instructions, 'installed_service', return_value=True), \
                 patch.object(instructions, 'run_worker', side_effect=adapter):
                write(registry, registered(prior))
                manager.reconcile(0)
                self.assertEqual(manager.load()['projects'][prior]['extensions']['devstack-tools']['status'], 'error')
                manager.reconcile(3)
                skill = project_root / '.agents/skills/devstack-mail'
                self.assertTrue(skill.is_symlink())
                manager.operate({'extension': 'devstack-tools', 'project': prior, 'action': 'set_automatic', 'enabled': False})
                manager.reconcile(6)
                self.assertFalse(skill.is_symlink())
                self.assertFalse(manager.load()['projects'][prior]['extensions']['devstack-tools']['automatic'])
                write(registry, registered(current))
                manager.reconcile(9)
                self.assertTrue(skill.is_symlink())
                self.assertNotIn(prior, manager.load()['projects'])
                self.assertTrue(manager.load()['projects'][current]['extensions']['devstack-tools']['automatic'])
                active['devstack-tools'] = {'state': 'absent'}
                manager.reconcile(12)
                self.assertFalse(skill.is_symlink())
                self.assertTrue(project_root.is_dir())
