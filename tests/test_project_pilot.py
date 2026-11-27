from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'daemon'))
import project_pilot as pilot
from project_lifecycle import Component, LifecycleError, parse_snapshot
from project_runtime_identity import account_name


class RegisteredPilotTests(unittest.TestCase):
    def setUp(self):
        self.key = '1' * 32
        self.cfg = {'dev_user': 'ubuntu', 'projects_dir': '/home/ubuntu/projects'}
        self.project = {'project_id': self.key, 'path': '/home/ubuntu/projects/isolation-test',
                        'domain': 'isolation-test.test', 'webroot': 'public',
                        'https': True, 'database': False, 'local_mail': True}
        self.account = SimpleNamespace(pw_name='ubuntu', pw_uid=1000, pw_gid=1000,
                                       pw_dir='/home/ubuntu')

    def spec(self, **changes):
        with patch.object(pilot, 'read_control_json',
                          side_effect=[self.cfg, {'isolation-test': {**self.project, **changes}}]), \
             patch.object(pilot.pwd, 'getpwnam', return_value=self.account):
            return pilot.registered('isolation-test')

    def test_existing_mail_setting_not_rewritten(self):
        spec, key, account = self.spec()
        self.assertEqual(key, self.key)
        self.assertTrue(spec['settings']['local_mail'])
        self.assertEqual(account, self.account)
        for changes in ({'database': True}, {'https': False}, {'webroot': 'web'},
                        {'overrides': 'client_max_body_size 32m;'},
                        {'project_id': 'bad'}, {'path': '/tmp/other'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.spec(**changes)

    def test_registered_units_use_exact_registration(self):
        user = account_name('p', self.key)
        component = Component('php', user, Path('/unused/php.sock'), self.key)
        self.assertEqual(component.runtime_directory, 'devstack-pilot-' + self.key + '-php')
        values = {'Id': component.unit, 'LoadState': 'loaded', 'ActiveState': 'active',
                  'Transient': 'yes', 'User': 'root', 'Description': component.description}
        def result():
            return subprocess.CompletedProcess([], 0, stdout=''.join(
                f'{k}={v}\n' for k,v in values.items()), stderr='')
        self.assertEqual(parse_snapshot(result(), component), 'active')
        values['User'] = user
        with self.assertRaises(LifecycleError):
            parse_snapshot(result(), component)
        with self.assertRaises(ValueError):
            Component('php', user, Path('/unused/php.sock'), '../other').unit

    def test_private_writable_selection_has_no_fallback(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            (root / 'storage').mkdir()
            source = pilot.PrivateSources(root)
            try:
                with self.assertRaises(ValueError):
                    source.bind('storage', writable=True, optional=True)
                source.writable['storage'] = os.open(root / 'storage', pilot.DIR_FLAGS)
                args = source.bind('storage', writable=True)
                self.assertEqual(args[0], '--bind-fd')
                self.assertEqual(args[2], str(root / 'storage'))
                self.assertFalse(os.get_inheritable(int(args[1])))
                with self.assertRaises(ValueError):
                    source.bind('', writable=True)
            finally:
                source.close()

    def test_host_mount_namespace_is_refused_before_mount(self):
        spec, key, account = self.spec()
        identity = {'uid': 900, 'gid': 901}
        with tempfile.TemporaryDirectory() as work:
            path = Path(work)
            path.chmod(0o711)
            with patch.object(pilot, 'require_backend'), \
                 patch.object(pilot, 'registered', return_value=(spec,key,account)), \
                 patch.object(pilot, 'journal', return_value=path), \
                 patch.object(pilot, 'verify_binding', return_value=identity), \
                 patch.object(pilot, 'runtime_dir', return_value=path), \
                 patch.object(pilot, 'protected_parents'), \
                 patch.object(pilot, 'protected_control'), \
                 patch.object(pilot.os.path, 'samestat', return_value=True), \
                 patch.object(pilot, 'mount_command') as mount:
                with self.assertRaisesRegex(ValueError, 'PrivateMounts'):
                    pilot.launch(key, 'php')
                mount.assert_not_called()


if __name__ == '__main__':
    unittest.main()
