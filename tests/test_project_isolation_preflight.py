import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'daemon'))
import project_isolation_preflight as p


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.config = {'dev_user': 'developer', 'projects_dir': '/home/developer/projects'}
        self.project = {'path': '/home/developer/projects/sample-app',
                        'domain': 'sample-app.test', 'webroot': 'public',
                        'https': True, 'database': False, 'local_mail': False}
        self.registry = {'sample-app': self.project}
        self.slug = 'sample-app'

    def test_registered_canonical_path_only(self):
        self.assertEqual(p.project_spec(self.config, self.registry, self.slug)['webroot'], 'public')
        for slug in ('../etc', '/tmp', 'devstack-source', 'unknown-app', 'x'):
            with self.subTest(slug=slug), self.assertRaises(ValueError):
                p.project_spec(self.config, self.registry, slug)
        for key, value in (('path', '/tmp/sample-app'), ('domain', 'devstack.test'),
                           ('webroot', 'public/../private'), ('webroot', 'public/./build'),
                           ('database', 'false'), ('https', 1)):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                p.project_spec(self.config, {self.slug: {**self.project, key: value}}, self.slug)

    def test_metadata_only_and_reject_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / 'projects'
            root = base / self.slug
            public = root / 'public'
            public.mkdir(parents=True)
            index = public / 'index.php'
            index.write_text('fixture')
            (root / 'storage').mkdir()
            (root / 'database').mkdir()
            (root / 'bootstrap').mkdir()
            outside = Path(tmp) / 'outside'
            outside.mkdir()
            (root / 'bootstrap/cache').symlink_to(outside, target_is_directory=True)
            state = p.inspect_layout(base, self.slug, 'public', os.getuid())
            self.assertEqual(state['project'], 'present')
            self.assertEqual(state['webroot'], 'present')
            self.assertEqual(state['front_controller'], 'present')
            self.assertEqual(state['storage'], 'present')
            self.assertEqual(state['database_dir'], 'present')
            self.assertEqual(state['bootstrap_cache'], 'unsafe')
            self.assertEqual(state['published_storage'], 'missing')
            self.assertEqual(index.read_text(), 'fixture')
            self.assertFalse(any(outside.iterdir()))
            # Reject a hard-linked front controller and FIFO without opening content.
            os.link(index, outside / 'linked.php')
            self.assertEqual(p.inspect_layout(base, self.slug, 'public', os.getuid())['front_controller'], 'unsafe')
            index.unlink()
            os.mkfifo(index)
            self.assertEqual(p.inspect_layout(base, self.slug, 'public', os.getuid())['front_controller'], 'unsafe')
            index.unlink()
            public.rmdir()
            public.symlink_to(outside, target_is_directory=True)
            self.assertEqual(p.inspect_layout(base, self.slug, 'public', os.getuid())['webroot'], 'unsafe')
            with patch.object(p, 'mount_id', side_effect=OSError('unavailable')):
                self.assertEqual(p.inspect_layout(base, self.slug, 'public', os.getuid())['project'], 'unsafe')

    def test_root_symlink_and_mount_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / 'projects'
            base.mkdir()
            outside = Path(tmp) / 'outside'
            outside.mkdir()
            (base / self.slug).symlink_to(outside, target_is_directory=True)
            self.assertEqual(p.inspect_layout(base, self.slug, 'public', os.getuid())['project'], 'unsafe')
            (base / self.slug).unlink()
            (base / self.slug).mkdir()
            with patch.object(p, 'mount_id', side_effect=[100, 101]):
                self.assertEqual(p.inspect_layout(base, self.slug, 'public', os.getuid())['project'], 'unsafe')

    def test_registration_identity_is_metadata_only(self):
        self.assertEqual(p.project_spec(self.config, self.registry, self.slug)['identity'], 'legacy')
        value = 'a' * 32
        self.project['project_id'] = value
        spec = p.project_spec(self.config, self.registry, self.slug)
        self.assertEqual(spec['identity'], 'registered')
        self.assertNotIn('project_id', spec)
        result = p.build_report(spec, dict.fromkeys(p.LAYOUT_KEYS, 'present'), {})
        self.assertEqual(result['registration_identity'], 'registered')
        self.assertEqual(result['status'], 'blocked')
        self.assertNotIn('project_identity_enrollment_required', result['blockers'])
        self.registry['other'] = {'project_id': value}
        self.assertEqual(p.project_spec(self.config, self.registry, self.slug)['identity'], 'invalid')
        self.registry.pop('other')
        for bad in (None, '', 'A'*32, [], 'x/../../etc'):
            self.project['project_id'] = bad
            self.assertEqual(p.project_spec(self.config, self.registry, self.slug)['identity'], 'invalid')

    def test_report_never_grants_access(self):
        spec = p.project_spec(self.config, self.registry, self.slug)
        layout = dict.fromkeys(p.LAYOUT_KEYS, 'present')
        result = p.build_report(spec, layout, {'package': 'installed', 'version': '99.0',
                                'binary': 'ordinary-root-owned'})
        self.assertEqual(result['status'], 'blocked')
        self.assertFalse(result['sandbox']['approved'])
        self.assertIn('sandbox_backend_security_review', result['blockers'])
        self.assertIn('production_runtime_not_connected', result['blockers'])
        self.assertIn('project_identity_enrollment_required', result['blockers'])
        self.assertEqual(result['registration_identity'], 'legacy')
        self.assertNotIn('project_layout_review', result['blockers'])
        spec['settings']['database'] = True
        spec['settings']['extra_locations'] = True
        layout['storage'] = 'unsafe'
        result = p.build_report(spec, layout, {})
        self.assertIn('project_layout_review', result['blockers'])
        self.assertIn('project_service_access_review', result['blockers'])
        self.assertIn('custom_nginx_rules_review', result['blockers'])
        with self.assertRaises(ValueError):
            p.build_report(spec, {**layout, 'secret': 'present'}, {})


    def test_worker_failure_exposes_only_fixed_stage(self):
        import contextlib
        import io
        from types import SimpleNamespace
        spec = p.project_spec(self.config, self.registry, self.slug)
        account = SimpleNamespace(pw_name='developer', pw_uid=1000,
                                  pw_gid=1000, pw_dir='/home/developer')
        result = SimpleNamespace(returncode=2, stdout='',
                                 stderr='Isolation preflight failed at worker-identity.\nPRIVATE_TEST_SENTINEL\n')
        diagnostics = io.StringIO()
        with patch.object(p.pwd, 'getpwnam', return_value=account), \
             patch.object(p.subprocess, 'run', return_value=result), \
             contextlib.redirect_stderr(diagnostics):
            with self.assertRaises(ValueError):
                p.inspect_as_developer(spec)
        self.assertIn('exit=2; stage=worker-identity', diagnostics.getvalue())
        self.assertNotIn('PRIVATE_TEST_SENTINEL', diagnostics.getvalue())
