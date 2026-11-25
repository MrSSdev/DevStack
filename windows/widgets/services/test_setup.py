"""Reader account provisioning, idempotence and immutable sandbox regression."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('service_setup', ROOT/'setup.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)
NAME = setup.READER_USER

class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.etc = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        for name in ('passwd', 'group', 'shadow'):
            (self.etc/name).write_text('')

    def identity(self, uid=987, gid=987, shell='/usr/sbin/nologin', home='/nonexistent', password='!*'):
        (self.etc/'passwd').write_text(f'{NAME}:x:{uid}:{gid}:{setup.READER_DESCRIPTION}:{home}:{shell}\n')
        (self.etc/'group').write_text(f'{NAME}:x:{gid}:\n')
        (self.etc/'shadow').write_text(f'{NAME}:{password}:20000::::::\n')

    def test_missing_identity(self):
        self.assertIsNone(setup.reader_identity(self.etc))

    def test_local_nonlogin_identity(self):
        self.identity()
        self.assertEqual(setup.reader_identity(self.etc), (987,987))

    def test_root_or_dynamic_id_refused(self):
        for uid in (0,61184,64000,65534):
            with self.subTest(uid=uid):
                self.identity(uid=uid)
                with self.assertRaises(ValueError):setup.reader_identity(self.etc)

    def test_group_root_or_dynamic_refused(self):
        for gid in (0,61184,65534):
            with self.subTest(gid=gid):
                self.identity(gid=gid)
                with self.assertRaises(ValueError):setup.reader_identity(self.etc)

    def test_login_identity_not_adopted(self):
        for params in ({'shell':'/bin/bash'}, {'home':'/home/ubuntu'}, {'password':'$6$example'}):
            with self.subTest(params=params):
                self.identity(**params)
                with self.assertRaises(ValueError):setup.reader_identity(self.etc)

    def test_unrelated_description_refused(self):
        self.identity()
        p=self.etc/'passwd';p.write_text(p.read_text().replace(setup.READER_DESCRIPTION,'Existing account'))
        with self.assertRaises(ValueError):setup.reader_identity(self.etc)

    def test_supplementary_group_refused(self):
        self.identity()
        p=self.etc/'group';p.write_text(p.read_text()+f'sudo:x:27:{NAME}\n')
        with self.assertRaises(ValueError):setup.reader_identity(self.etc)

    def test_incomplete_identity_refused(self):
        (self.etc/'group').write_text(f'{NAME}:x:987:\n')
        with self.assertRaises(ValueError):setup.reader_identity(self.etc)

    def test_duplicate_identity_refused(self):
        self.identity();p=self.etc/'passwd';p.write_text(p.read_text()*2)
        with self.assertRaises(ValueError):setup.reader_identity(self.etc)

    def test_existing_identity_is_not_modified(self):
        with patch.object(setup,'reader_identity',return_value=(987,987)), patch.object(setup.subprocess,'run') as command:
            setup.ensure_reader_identity();command.assert_not_called()

    def test_new_identity_only_one_sysusers_line(self):
        with patch.object(setup,'reader_identity',side_effect=[None,(987,987)]), patch.object(setup.subprocess,'run') as command:
            setup.ensure_reader_identity()
            command.assert_called_once()
            args=command.call_args.args[0]
            self.assertEqual(args[:2],['/usr/bin/systemd-sysusers','--inline'])
            self.assertEqual(len(args),3)
            self.assertIn(' /nonexistent /usr/sbin/nologin',args[2])
            self.assertTrue(command.call_args.kwargs['check'])

    def test_sandbox_kept(self):
        text=(ROOT/'devstack-widget-services@.service').read_text()
        for line in ('User='+NAME,'Group='+NAME,'DynamicUser=yes','NoNewPrivileges=yes',
                     'ProtectSystem=strict','ProtectHome=yes','PrivateDevices=yes',
                     'ProtectControlGroups=yes','CapabilityBoundingSet=',
                     'LoadCredential=auth:/etc/devstack-widgets/services-read.token'):
            self.assertIn(line+'\n',text)
        self.assertNotIn('User=root',text)

if __name__=='__main__':unittest.main()
