"""No root or system services are used by these unit tests."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('devstackd', Path(__file__).resolve().parents[1] / 'daemon/devstackd.py')
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


class ValidationTests(unittest.TestCase):
    def test_project_slug(self):
        for value in ('optiti', 'my-laravel-13', 'devstack2'):
            self.assertEqual(d.check_slug(value), value)
        for value in ('../../etc', 'UPPER', '-bad', 'x;ls', 'x/x', '', 'a', 'x'*41, 'x_foo', 'x.test'):
            with self.assertRaises(d.InputError, msg=value):
                d.check_slug(value)

    def test_domain(self):
        self.assertEqual(d.check_domain('optiti.test'), 'optiti.test')
        for value in ('devstack.test', 'optiti.com', 'project.local', 'evil.test; rm -rf /', 'a..test', 'a.b.test', '123.test', 'x' * 41 + '.test'):
            with self.assertRaises(d.InputError, msg=value):
                d.check_domain(value)

    def test_webroot(self):
        for value in ('public', 'dist', 'frontend/public'):
            self.assertEqual(d.check_webroot(value), value)
        for value in ('/etc', '../../etc', 'public/../private', 'x//y', 'x;cat', 'index.php', '.env', 'a\\b', ''):
            if value == 'index.php':
                # Allowed at syntactic validation level, but rejected if it exists as file at deploy time.
                continue
            with self.assertRaises(d.InputError, msg=value):
                d.check_webroot(value)

    def test_safe_nginx_overrides(self):
        raw = '# Project settings\nclient_max_body_size 64m;\nclient_body_timeout 120s;\n'
        self.assertEqual(d.check_overrides(raw), 'client_max_body_size 64m;\nclient_body_timeout 120s;')
        for value in (
            'include /etc/shadow;', 'access_log /tmp/hack;', 'server { listen 0.0.0.0:80; }',
            'client_max_body_size 0m;', 'fastcgi_read_timeout 9999s;', 'client_max_body_size 50m;\nclient_max_body_size 99m;',
            'client_max_body_size 10m; access_log /tmp/x;', 'client_max_body_size 1m\nexec id;',
            'proxy_pass http://evil.test;', 'client_body_timeout 5m;', 'client_max_body_size 12m;#comment',
        ):
            with self.assertRaises(d.InputError, msg=value):
                d.check_overrides(value)

    def test_theme_defaults_and_persists_server_side(self):
        with tempfile.TemporaryDirectory() as tmp:
            pref = Path(tmp) / 'preferences.json'
            with patch.object(d, 'PREFERENCES_FILE', pref):
                self.assertEqual(d.get_ui_preferences(), {'theme': 'light'})
                self.assertEqual(d.set_ui_preferences({'theme': 'dark'}), {'theme': 'dark'})
                self.assertEqual(d.get_ui_preferences(), {'theme': 'dark'})
                self.assertEqual(json.loads(pref.read_text()), {'theme': 'dark'})
                self.assertEqual(pref.stat().st_mode & 0o777, 0o600)
                self.assertEqual(d.set_ui_preferences({'theme': 'light'}), {'theme': 'light'})
                self.assertEqual(d.get_ui_preferences(), {'theme': 'light'})
                for bad in ('auto', 'system', 'DARK', '', None, 1, [], {'theme':'dark'}):
                    with self.assertRaises(d.InputError, msg=str(bad)):
                        d.set_ui_preferences({'theme': bad})
                for malformed in ({}, {'theme':'dark', 'path':'/etc/shadow'}):
                    with self.assertRaises(d.InputError):
                        d.set_ui_preferences(malformed)
                pref.write_text('{"theme":"invalid"}')
                with self.assertRaises(RuntimeError):
                    d.get_ui_preferences()

    def test_status_includes_server_theme(self):
        with patch.object(d,'service_status',return_value={'nginx':'active'}), \
             patch.object(d,'public_projects',return_value=[]), \
             patch.object(d,'get_ui_preferences',return_value={'theme':'dark'}):
            self.assertEqual(d.status({})['preferences'], {'theme': 'dark'})

    def test_fpm_reload_is_delayed_and_not_exposed_to_user_parameters(self):
        with patch.object(d, 'run') as mock_run:
            d.defer_php_fpm_reload()
        argv = mock_run.call_args.args[0]
        self.assertEqual(argv[0], '/usr/bin/systemd-run')
        self.assertIn('--on-active=8s', argv)
        self.assertEqual(argv[-3:], ['/usr/bin/systemctl', 'reload', 'php8.5-fpm'])
        self.assertTrue(any(arg.startswith('--unit=devstack-fpm-reload-') for arg in argv))

    def test_creation_failure_does_not_reload_php_fpm_or_hide_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            project_dir = temp / 'projects'
            project_dir.mkdir()
            state_file = temp / 'projects.json'
            secret_file = temp / 'credentials.json'
            site = temp / 'devstack-demo.conf'
            link = temp / 'enabled-demo.conf'
            pool = temp / 'pool-demo.conf'
            public = project_dir / 'demo' / 'public'
            public.mkdir(parents=True)
            commands = []
            def fake_run(argv, *args, **kwargs):
                commands.append(argv)
                if argv[:2] == ['/usr/bin/setfacl', '-m']:
                    raise RuntimeError('Cannot grant public access')
                return ''
            with patch.object(d, 'config', return_value={'dev_user':'ubuntu','projects_dir':str(project_dir)}), \
                 patch.object(d, 'STATE_FILE', state_file), \
                 patch.object(d, 'SECRET_FILE', secret_file), \
                 patch.object(d, 'site_path', return_value=site), \
                 patch.object(d, 'site_link_path', return_value=link), \
                 patch.object(d, 'pool_path', return_value=pool), \
                 patch.object(d, 'ensure_project_paths', return_value=public), \
                 patch.object(d, 'run', side_effect=fake_run):
                with self.assertRaisesRegex(RuntimeError, 'Cannot grant public access'):
                    d.create({'slug':'demo', 'name':'Demo', 'domain':'demo.test',
                              'webroot':'public', 'database':False, 'https':False})
            self.assertFalse(any(argv[:3] == ['/usr/bin/systemctl','reload','php8.5-fpm'] for argv in commands))
            self.assertFalse(any(argv[0] == '/usr/bin/systemd-run' for argv in commands))
            self.assertFalse(site.exists())
            self.assertFalse(pool.exists())

    def test_no_arbitrary_operations(self):
        for op in ('shell', 'delete', 'change_password', 'exec', 'reload', 'writefile', 'sudo'):
            result = json.loads(d.handle(json.dumps({'op': op, 'data': {'cmd': 'id'}}).encode()))
            self.assertFalse(result['ok'])

    def test_wrong_envelope(self):
        for raw in (b'', b'[]', b'{"op":"status"}', b'{"op":null,"data":{}}', b'{' + b'x' * 65537 + b'}'):
            self.assertFalse(json.loads(d.handle(raw))['ok'])

    def test_generated_nginx_contains_loopback_and_strict_php(self):
        conf = d.nginx_conf('optiti', 'optiti.test', Path('/home/ubuntu/projects/optiti/public'), True, 'client_max_body_size 64m;\nclient_body_timeout 120s;')
        self.assertIn('listen 127.0.0.1:443 ssl;', conf)
        self.assertIn('listen 127.0.0.1:80;', conf)
        self.assertIn('server_name optiti.test;', conf)
        self.assertIn('client_body_timeout 120s;', conf)
        self.assertIn('location ~ \\.php$ { return 404; }', conf)
        self.assertIn('location ~ ^/index\\.php$ {', conf)
        self.assertNotIn('listen 0.0.0.0', conf)
        self.assertNotIn('client_max_body_size 64m;\nclient_max_body_size', conf)

    def test_full_editor_accepts_only_marked_location_rules(self):
        p = {'path': '/home/ubuntu/projects/optiti', 'webroot': 'public', 'domain': 'optiti.test', 'https': True}
        original = d.nginx_conf('optiti', p['domain'], Path(p['path']) / p['webroot'], True)
        edited = original.replace(d.LOCATION_START, d.LOCATION_START + '        client_max_body_size 64m;\n')
        self.assertEqual(d.check_full_config(edited, 'optiti', p), 'client_max_body_size 64m;')
        for unsafe in (
            edited.replace('listen 127.0.0.1:443 ssl;', 'listen 0.0.0.0:443 ssl;'),
            edited.replace('root /home/ubuntu/projects/optiti/public;', 'root /etc;'),
            edited.replace('fastcgi_pass unix:/run/php/devstack-optiti.sock;', 'fastcgi_pass 127.0.0.1:9000;'),
            edited.replace('try_files $uri $uri/ /index.php?$query_string;', 'proxy_pass http://other.test;'),
            edited.replace(d.LOCATION_END, '        include /etc/shadow;\n' + d.LOCATION_END),
        ):
            with self.assertRaises(d.InputError):
                d.check_full_config(unsafe, 'optiti', p)

    def test_plain_nginx_has_no_cert(self):
        conf = d.nginx_conf('simple', 'simple.test', Path('/home/ubuntu/projects/simple/public'), False)
        self.assertNotIn('ssl_certificate', conf)
        self.assertNotIn('127.0.0.1:443', conf)

    def test_db_credentials_names_are_stable_and_safe(self):
        name, user = d.db_identifiers('test-project')
        self.assertEqual((name, user), d.db_identifiers('test-project'))
        self.assertRegex(name, r'^[a-z0-9]{1,64}$')
        self.assertRegex(user, r'^[a-z0-9]{1,64}$')
        self.assertNotIn('_', name)
        self.assertNotEqual(name, d.db_identifiers('test-project2')[0])

    def test_secret_not_in_mariadb_error(self):
        import subprocess
        with patch.object(d, 'run', side_effect=RuntimeError("SQL with password secret-should-not-leak")):
            with self.assertRaisesRegex(RuntimeError, 'Sprawdz log') as cm:
                d.mariadb_execute("SELECT 'secret-should-not-leak'")
            self.assertNotIn('secret-should-not-leak', str(cm.exception))

    def test_atomic_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            d.write_json_atomic(path, {'key': 'value'})
            self.assertEqual(d.read_json(path)['key'], 'value')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_nginx_config_rollback_on_validation_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'devstack-alpha.conf'
            target.write_text('old config', encoding='utf-8')
            state = Path(tmp) / 'state.json'
            state.write_text(json.dumps({'alpha': {'domain':'alpha.test','path':'/home/ubuntu/projects/alpha', 'webroot':'public','https':True, 'overrides':''}}), encoding='utf-8')
            backup = Path(tmp) / 'backup'
            backup.mkdir()
            commands=[]
            def fake_run(argv, *a, **kw):
                commands.append(argv)
                if argv[-1]=='-t' and len(commands)==1: raise RuntimeError('test failed')
                return ''
            # update_config has an explicit backup file location: patch Path at the exact site_path only,
            # no real /etc files are touched.
            with patch.object(d, 'STATE_FILE', state), patch.object(d, 'site_path', return_value=target), patch.object(d, 'run', side_effect=fake_run), patch.object(d, 'pathlib') as dummy:
                # Preserve pathlib.Path semantics for other uses and redirect only backup location.
                dummy.Path.side_effect = lambda value: backup if value=='/var/lib/devstack' else Path(value)
                with self.assertRaises(RuntimeError):
                    d.update_config({'slug':'alpha','overrides':'client_max_body_size 64m;'})
            self.assertEqual(target.read_text(), 'old config')
            self.assertEqual(json.loads(state.read_text())['alpha']['overrides'], '')

if __name__ == '__main__':
    unittest.main()
