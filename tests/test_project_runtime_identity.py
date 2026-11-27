from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'daemon'))
import project_runtime_identity as identity


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.key = '1' * 32
        self.name = identity.account_name('a', self.key)
        self.ready = {'schema': 1, 'slot': 'a', 'project_id': self.key,
                      'account': self.name, 'state': 'ready', 'uid': 900, 'gid': 901}

    def test_registration_not_slug_or_model(self):
        self.assertEqual(len(self.name), 31)
        self.assertNotEqual(self.name, identity.account_name('a', '2'*32))
        for bad in (None, '', 'a'*31, 'A'*32, '../other'):
            with self.subTest(bad=bad), self.assertRaises(identity.IdentityError):
                identity.account_name('a', bad)
        with self.assertRaises(identity.IdentityError):
            identity.account_name('x', self.key)

    def test_record_rejects_mismatches_and_extra_fields(self):
        self.assertEqual(identity.validate_record(self.ready, 'a'), self.ready)
        for changes in ({'schema': True}, {'uid': True}, {'gid': 0}, {'uid': -1},
                        {'slot': 'b'}, {'account': 'root'}, {'project_id': '2'*32},
                        {'unexpected': 'agent'}, {'state': 'pending'}):
            with self.subTest(changes=changes), self.assertRaises(identity.IdentityError):
                identity.validate_record({**self.ready, **changes}, 'a')
        pending = {k:v for k,v in self.ready.items() if k not in ('uid', 'gid')}
        pending['state'] = 'pending'
        self.assertEqual(identity.validate_record(pending, 'a'), pending)
        with patch.object(identity.pwd, 'getpwnam') as lookup:
            with self.assertRaises(identity.IdentityError):
                identity.verify_account(pending)
            lookup.assert_not_called()

    def test_account_reuse_and_group_drift_refused(self):
        account = SimpleNamespace(pw_name=self.name, pw_uid=900, pw_gid=901,
                                  pw_dir=identity.HOME, pw_shell=identity.SHELL,
                                  pw_gecos=identity.comment(self.key))
        group = SimpleNamespace(gr_name=self.name, gr_gid=901)
        with patch.object(identity.pwd, 'getpwnam', return_value=account), \
             patch.object(identity.pwd, 'getpwuid', return_value=account), \
             patch.object(identity.grp, 'getgrnam', return_value=group), \
             patch.object(identity.grp, 'getgrgid', return_value=group), \
             patch.object(identity.os, 'getgrouplist', return_value=[901]) as groups:
            self.assertEqual(identity.verify_account(self.ready), account)
            groups.return_value = [901, 27]
            with self.assertRaises(identity.IdentityError):
                identity.verify_account(self.ready)
            groups.return_value = [901]
            account.pw_uid = 902
            with self.assertRaises(identity.IdentityError):
                identity.verify_account(self.ready)

    def test_existing_name_never_adopted(self):
        with patch.object(identity.os, 'geteuid', return_value=0), \
             patch.dict(identity.os.environ, {'GITHUB_ACTIONS': 'true', 'DEVSTACK_RUNTIME_CI': '1'}), \
             patch.object(identity, 'record_path', return_value=Path('/unused/identity-a.json')), \
             patch.object(identity.pwd, 'getpwnam', return_value=object()), \
             patch.object(identity.subprocess, 'run') as command, \
             patch.object(identity.os, 'open') as opened:
            with self.assertRaises(identity.IdentityError):
                identity.provision_fixture(Path('/unused'), 'a', self.key)
            command.assert_not_called()
            opened.assert_not_called()

    def test_wrong_session_credentials_rejected(self):
        account = SimpleNamespace(pw_uid=900, pw_gid=901)
        with patch.object(identity, 'read_record', return_value=self.ready), \
             patch.object(identity, 'verify_account', return_value=account), \
             patch.object(identity.os, 'geteuid', return_value=902), \
             patch.object(identity.os, 'getegid', return_value=901), \
             patch.object(identity.os, 'getgroups', return_value=[901]):
            with self.assertRaises(identity.IdentityError):
                identity.verify_binding(Path('/unused'), 'a', current=True)


if __name__ == '__main__':
    unittest.main()
