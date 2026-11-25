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

    def test_unified_nginx_editor_preserves_both_managed_regions(self):
        flux = (r'location ~* ^/flux/(flux|editor)(\.min)?\.(js|css)$ {'
                ' expires off; try_files $uri $uri/ /index.php?$query_string; }')
        tuning = 'client_max_body_size 64m;\nclient_body_timeout 120s;'
        expected = {'overrides': tuning, 'extra_locations': d.check_extra_locations(flux)}
        self.assertEqual(d.check_nginx_rules(tuning + '\n\n' + flux), expected)
        self.assertEqual(d.check_nginx_rules(flux + '\n# tuning\n' + tuning), expected)
        self.assertEqual(d.check_nginx_rules(expected['overrides'] + '\n\n' + expected['extra_locations']), expected)
        self.assertEqual(d.check_nginx_rules(' \n# nothing\n'), {'overrides': '', 'extra_locations': ''})
        self.assertEqual(d.check_nginx_rules(flux)['overrides'], '')
        self.assertEqual(d.check_nginx_rules(tuning)['extra_locations'], '')

    def test_unified_nginx_editor_rejects_unsafe_or_duplicate_rules(self):
        flux = (r'location ~* ^/flux/flux\.js$ {'
                ' try_files $uri $uri/ /index.php?$query_string; }')
        for bad in (None, 'x' * 5803, 'client_max_body_size 0m;',
                    'client_max_body_size 64m;\nclient_max_body_size 128m;',
                    'location / { proxy_pass http://evil.test; }',
                    flux + '\n' + flux, flux + '\ninclude /etc/shadow;',
                    flux.replace('try_files', 'root /etc; try_files'),
                    flux.replace('/index.php?', '/secret.php?'),
                    'client_body_timeout 120s;\n' + flux + '\x00'):
            with self.assertRaises(d.InputError, msg=repr(bad)):
                d.check_nginx_rules(bad)
        with self.assertRaises(d.InputError):
            d.update_config({'slug':'alpha','rules':'','overrides':''})

    def test_framework_reader_uses_only_developer_credentials_and_known_results(self):
        from types import SimpleNamespace
        account = SimpleNamespace(pw_uid=1001, pw_gid=1001)
        output = {
            'demo': {'name': 'Laravel', 'version': '13.42.0', 'untrusted': 'discard'},
            'other': {'name': 'Symfony', 'version': '<script>'},
            'unknown': {'name': 'Unknown', 'version': '1.0'},
            'not-requested': {'name': 'Laravel', 'version': '13.0'},
        }
        with patch.object(d, 'engine_identity', return_value=('developer', '/home/developer')), \
             patch.object(d.pwd, 'getpwnam', return_value=account), \
             patch.object(d.subprocess, 'run', return_value=SimpleNamespace(stdout=json.dumps(output))) as run:
            result = d.project_frameworks(['demo','other','unknown'])
            self.assertEqual(result, {'demo': {'name': 'Laravel', 'version': '13.42.0'}})
            args, kwargs = run.call_args
            self.assertEqual(args[0][:2], ['/usr/bin/python3','-I'])
            self.assertTrue(args[0][-1].endswith('/daemon/project_frameworks.py'))
            self.assertEqual(kwargs['user'], 1001)
            self.assertEqual(kwargs['group'], 1001)
            self.assertEqual(kwargs['extra_groups'], [])
            self.assertEqual(json.loads(kwargs['input']), ['demo','other','unknown'])
            self.assertEqual(kwargs['cwd'], '/')
        with patch.object(d, 'engine_identity', return_value=('developer','/home/developer')), \
             patch.object(d.pwd,'getpwnam',return_value=account), \
             patch.object(d.subprocess,'run',side_effect=OSError('unavailable')):
            self.assertEqual(d.project_frameworks(['demo']), {})

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
        with patch.object(d.managed_services,'public_rows',return_value=[{'id':'nginx','unit':'nginx.service','state':'active'}]), \
             patch.object(d.managed_services,'action_pending',return_value=False), \
             patch.object(d,'public_projects',return_value=[]), \
             patch.object(d,'get_ui_preferences',return_value={'theme':'dark'}):
            self.assertEqual(d.status({})['preferences'], {'theme': 'dark'})

    def test_mkdir_runs_under_developer_identity_without_setuid(self):
        from types import SimpleNamespace
        with patch.object(d.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=1000)), \
             patch.object(d, 'run') as mock_run:
            d.mkdir_as_developer(Path('/home/ubuntu/projects/demo'), 'ubuntu')
        argv = mock_run.call_args.args[0]
        self.assertEqual(argv[0], '/usr/bin/systemd-run')
        self.assertIn('--wait', argv)
        self.assertIn('--pipe', argv)
        self.assertIn('--collect', argv)
        self.assertIn('--uid=ubuntu', argv)
        self.assertIn('--property=NoNewPrivileges=yes', argv)
        self.assertTrue(any(arg.startswith('--unit=devstack-mkdir-') for arg in argv))
        self.assertEqual(argv[-3:], ['/usr/bin/mkdir','--','/home/ubuntu/projects/demo'])
        self.assertNotIn('runuser', ' '.join(argv))

    def test_fpm_reload_is_delayed_and_not_exposed_to_user_parameters(self):
        with patch.object(d, 'run') as mock_run:
            d.defer_php_fpm_reload()
        argv = mock_run.call_args.args[0]
        self.assertEqual(argv[0], '/usr/bin/systemd-run')
        self.assertIn('--on-active=8s', argv)
        self.assertEqual(argv[-3:], ['/usr/bin/systemctl', 'reload', 'php8.5-fpm'])
        self.assertTrue(any(arg.startswith('--unit=devstack-fpm-reload-') for arg in argv))

    def test_windows_hosts_trigger_is_fixed_and_optional(self):
        # Never call the real Windows scheduler from unit tests.
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / 'schtasks.exe'
            executable.write_text('safe fixture')
            with patch.object(d, 'WINDOWS_SCHTASKS', executable), \
                 patch.object(d, 'run', return_value='SUCCESS') as fake_run:
                self.assertEqual(d.request_windows_hosts_sync(), '')
                fake_run.assert_called_once_with([
                    str(executable), '/Run', '/TN', 'DevStack Hosts Event Sync'
                ])
            with patch.object(d, 'WINDOWS_SCHTASKS', Path(tmp) / 'missing.exe'), \
                 patch.object(d, 'run') as fake_run:
                self.assertEqual(d.request_windows_hosts_sync(), '')
                fake_run.assert_not_called()
            with patch.object(d, 'WINDOWS_SCHTASKS', executable), \
                 patch.object(d, 'run', side_effect=RuntimeError('denied')):
                warning = d.request_windows_hosts_sync()
                self.assertIn('Windows hosts', warning)

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
                if '/usr/bin/setfacl' in argv:
                    raise RuntimeError('Cannot grant public access')
                return ''
            with patch.object(d, 'config', return_value={'dev_user':'ubuntu','projects_dir':str(project_dir)}), \
                 patch.object(d, 'STATE_FILE', state_file), \
                 patch.object(d, 'SECRET_FILE', secret_file), \
                 patch.object(d, 'site_path', return_value=site), \
                 patch.object(d, 'site_link_path', return_value=link), \
                 patch.object(d, 'pool_path', return_value=pool), \
                 patch.object(d, 'ensure_project_paths', return_value=public), \
                 patch.object(d.pwd, 'getpwnam'), \
                 patch.object(d, 'run', side_effect=fake_run):
                with self.assertRaisesRegex(RuntimeError, 'Cannot grant public access'):
                    d.create({'slug':'demo', 'name':'Demo', 'domain':'demo.test',
                              'webroot':'public', 'database':False, 'https':False})
            self.assertFalse(any(argv[:3] == ['/usr/bin/systemctl','reload','php8.5-fpm'] for argv in commands))
            self.assertFalse(any('reload' in argv and 'php8.5-fpm' in argv for argv in commands))
            self.assertTrue(any('--uid=ubuntu' in argv and '/usr/bin/setfacl' in argv for argv in commands))
            self.assertFalse(site.exists())
            self.assertFalse(pool.exists())

    def test_delete_requires_exact_slug_and_explicit_db_confirmation(self):
        project = {'name':'Demo', 'path':'/home/ubuntu/projects/demo',
                   'domain':'demo.test', 'webroot':'public', 'https':True,
                   'database':True, 'db_name':d.db_identifiers('demo')[0],
                   'db_user':d.db_identifiers('demo')[1]}
        state = {'demo':project}
        with patch.object(d,'config',return_value={'projects_dir':'/home/ubuntu/projects'}), \
             patch.object(d,'read_json',return_value=state):
            for body in (
                {'slug':'demo','confirmation':'wrong','delete_database':True},
                {'slug':'demo','confirmation':'demo','delete_database':False},
                {'slug':'demo','confirmation':'demo','delete_database':'true'},
                {'slug':'demo','confirmation':'demo','delete_database':True,'extra':1},
            ):
                with self.assertRaises(d.InputError, msg=str(body)):
                    d.delete_project(body)
        with patch.object(d,'read_json',return_value={}), \
             patch.object(d,'config',return_value={'projects_dir':'/home/ubuntu/projects'}):
            with self.assertRaises(d.InputError):
                d.delete_project({'slug':'missing','confirmation':'missing','delete_database':False})

    def test_delete_success_preserves_source_and_reconciles_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            statefile=folder/'projects.json'
            secretfile=folder/'credentials.json'
            root=folder/'projects'/'demo'
            root.mkdir(parents=True)
            (root/'public').mkdir()
            (root/'public'/'index.php').write_text('<?php echo "KEPT";')
            site=folder/'devstack-demo.conf'
            link=folder/'enabled.conf'
            pool=folder/'fpm.conf'
            site.write_text('nginx')
            pool.write_text('fpm')
            link.symlink_to(site)
            p={'name':'Demo','domain':'demo.test','path':str(root),'webroot':'public',
               'database':False,'https':False,'db_name':'','db_user':'','overrides':''}
            d.write_json_atomic(statefile,{'demo':p})
            d.write_json_atomic(secretfile,{})
            commands=[]
            def fake_run(argv,*args,**kwargs):
                commands.append(argv)
                return ''
            with patch.object(d,'config',return_value={'projects_dir':str(root.parent)}), \
                 patch.object(d,'STATE_FILE',statefile), \
                 patch.object(d,'SECRET_FILE',secretfile), \
                 patch.object(d,'site_path',return_value=site), \
                 patch.object(d,'site_link_path',return_value=link), \
                 patch.object(d,'pool_path',return_value=pool), \
                 patch.object(d,'run',side_effect=fake_run):
                result=d.delete_project({'slug':'demo','confirmation':'demo','delete_database':False})
            self.assertIn('Pliki projektu pozostaly',result['message'])
            self.assertEqual(d.read_json(statefile),{})
            self.assertFalse(site.exists())
            self.assertFalse(link.exists())
            self.assertFalse(pool.exists())
            self.assertEqual((root/'public'/'index.php').read_text(),'<?php echo "KEPT";')
            self.assertIn(['/usr/sbin/nginx','-t'],commands)
            self.assertTrue(any(argv[0]=='/usr/bin/systemd-run' for argv in commands))

    def test_delete_rollback_preserves_source_and_managed_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            statefile = folder / 'projects.json'
            secretfile = folder / 'credentials.json'
            root = folder / 'projects' / 'demo'
            root.mkdir(parents=True)
            (root / 'index.php').write_text('<?php echo "KEEP";')
            site = folder / 'devstack-demo.conf'
            enabled = folder / 'enabled-demo.conf'
            pool = folder / 'devstack-demo-pool.conf'
            site.write_text('original nginx config')
            pool.write_text('original php pool')
            enabled.symlink_to(site)
            meta = {'name':'Demo','domain':'demo.test','path':str(root),
                    'webroot':'public','https':False,'database':False,
                    'db_name':'','db_user':'','overrides':''}
            statefile.write_text(json.dumps({'demo':meta}))
            secretfile.write_text('{}')
            cmds=[]
            def fake_run(args,*a,**kw):
                cmds.append(args)
                if args == ['/usr/sbin/nginx','-t']:
                    raise RuntimeError('nginx verification failed')
                return ''
            with patch.object(d,'config',return_value={'projects_dir':str(root.parent)}), \
                 patch.object(d,'STATE_FILE',statefile), \
                 patch.object(d,'SECRET_FILE',secretfile), \
                 patch.object(d,'site_path',return_value=site), \
                 patch.object(d,'site_link_path',return_value=enabled), \
                 patch.object(d,'pool_path',return_value=pool), \
                 patch.object(d,'run',side_effect=fake_run):
                with self.assertRaisesRegex(RuntimeError,'nginx verification failed'):
                    d.delete_project({'slug':'demo','confirmation':'demo','delete_database':False})
            self.assertEqual(json.loads(statefile.read_text()), {'demo':meta})
            self.assertTrue(enabled.is_symlink())
            self.assertEqual(enabled.resolve(),site)
            self.assertEqual(site.read_text(),'original nginx config')
            self.assertEqual(pool.read_text(),'original php pool')
            self.assertEqual((root/'index.php').read_text(),'<?php echo "KEEP";')
            self.assertFalse(any(a[0]=='/usr/bin/systemd-run' for a in cmds))

    def test_db_smoke_test_refuses_non_project_and_never_returns_password(self):
        slug='demo'
        name,user=d.db_identifiers(slug)
        with patch.object(d,'STATE_FILE',Path('/tmp/projects.json')), \
             patch.object(d,'read_json',return_value={'demo':{'database':False}}):
            with self.assertRaises(d.InputError):
                d.test_project_database({'slug':slug})
        password='a'*48
        with tempfile.TemporaryDirectory() as tmp:
            def fake_read(path):
                if path==d.STATE_FILE:
                    return {slug:{'database':True,'db_name':name,'db_user':user}}
                return {slug:{'password':password}}
            args_seen=[]
            def fake_run(argv, input_text=None, **kw):
                args_seen.append((argv,input_text))
                opts=next(x for x in argv if x.startswith('--defaults-extra-file=')).split('=',1)[1]
                file=Path(opts)
                self.assertEqual(file.stat().st_mode & 0o777,0o600)
                config_text = file.read_text()
                self.assertEqual(config_text.splitlines(),
                                 ['[client]', 'user='+user, 'password='+password])
                self.assertTrue(config_text.endswith(chr(10)))
                self.assertNotIn(password,' '.join(argv))
                self.assertIn('CREATE TEMPORARY TABLE',input_text)
                return '1'
            # A read-only test environment has no /run/devstack. Redirect temp
            # dir via a small wrapper; no system daemon or database is used.
            original_tempdir=tempfile.TemporaryDirectory
            def temp_factory(*args,**kwargs):
                kwargs['dir']=tmp
                return original_tempdir(*args,**kwargs)
            with patch.object(d,'read_json',side_effect=fake_read), \
                 patch.object(d.tempfile,'TemporaryDirectory',side_effect=temp_factory), \
                 patch.object(d,'run',side_effect=fake_run):
                result=d.test_project_database({'slug':slug})
            self.assertIn('dzialaja poprawnie',result['message'])
            self.assertNotIn(password,str(result))
            self.assertEqual(len(args_seen),1)

    def test_no_arbitrary_operations(self):
        for op in ('shell', 'delete_all', 'change_password', 'exec', 'reload', 'writefile', 'sudo'):
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
        self.assertEqual(d.check_full_config(edited, 'optiti', p), {'overrides':'client_max_body_size 64m;', 'extra_locations':''})
        for unsafe in (
            edited.replace('listen 127.0.0.1:443 ssl;', 'listen 0.0.0.0:443 ssl;'),
            edited.replace('root /home/ubuntu/projects/optiti/public;', 'root /etc;'),
            edited.replace('fastcgi_pass unix:/run/php/devstack-optiti.sock;', 'fastcgi_pass 127.0.0.1:9000;'),
            edited.replace('try_files $uri $uri/ /index.php?$query_string;', 'proxy_pass http://other.test;'),
            edited.replace(d.LOCATION_END, '        include /etc/shadow;\n' + d.LOCATION_END),
        ):
            with self.assertRaises(d.InputError):
                d.check_full_config(unsafe, 'optiti', p)

    def test_flux_asset_location_and_server_order(self):
        raw = (r'location ~* ^/flux/(flux|editor)(\.min)?\.(js|css)$ {'
               ' expires off; try_files $uri $uri/ /index.php?$query_string; }')
        normalized = d.check_extra_locations(raw)
        self.assertIn('expires off;', normalized)
        self.assertIn('try_files $uri $uri/ /index.php?$query_string;', normalized)
        self.assertEqual(d.check_extra_locations(normalized), normalized)
        conf = d.nginx_conf('demo', 'demo.test',
                            Path('/home/ubuntu/projects/demo/public'), True,
                            '', normalized)
        self.assertIn(d.EXTRA_START, conf)
        self.assertLess(conf.index('location ~* ^/flux'), conf.index('location ~ ^/index'))
        self.assertLess(conf.index('location ~* ^/flux'), conf.index('location ~ \\.php$'))
        self.assertIn('server_name demo.test;', conf)

    def test_asset_locations_reject_protected_or_arbitrary_rules(self):
        valid = (r'location ~* ^/flux/(flux|editor)(\.min)?\.(js|css)$ {'
                 ' expires off; try_files $uri $uri/ /index.php?$query_string; }')
        for bad in (
            'location / { proxy_pass http://untrusted.test; }',
            'location ~* ^/assets/.*$ { expires off; try_files $uri $uri/ /index.php?$query_string; }',
            'location ~* ^/flux/.*\\.php$ { expires off; try_files $uri $uri/ /index.php?$query_string; }',
            valid.replace('expires off;', 'expires 1y;'),
            valid.replace('try_files', 'rewrite /something last; try_files'),
            valid.replace('/index.php?$query_string;', '/secret.php?$query_string;'),
            valid.replace('}', 'location /admin { deny all; } }'),
            valid + ' include /etc/shadow;',
            valid + '\n' + valid,
            'location ~* ^/foo/bar\\.js$ { root /etc; }',
            'location ~* ^/foo/bar\\.js$ { fastcgi_pass unix:/tmp/a.sock; }',
            'location ~* ^/foo/bar\\.js$ { alias /etc/shadow; }',
        ):
            with self.assertRaises(d.InputError, msg=bad):
                d.check_extra_locations(bad)
        with self.assertRaises(d.InputError):
            d.check_extra_locations(None)

    def test_full_editor_allows_only_two_safe_regions(self):
        p = {'path':'/home/ubuntu/projects/optiti','webroot':'public',
             'domain':'optiti.test','https':True}
        original = d.nginx_conf('optiti','optiti.test',
                                Path(p['path'])/'public', True)
        flux = (r'location ~* ^/flux/(flux|editor)(\.min)?\.(js|css)$ {'
                ' expires off; try_files $uri $uri/ /index.php?$query_string; }')
        edited = original.replace(d.EXTRA_START, d.EXTRA_START + '    ' + flux + '\n')
        result = d.check_full_config(edited, 'optiti', p)
        self.assertIn('location ~* ^/flux', result['extra_locations'])
        self.assertEqual(result['overrides'], '')
        for unsafe in (
            edited.replace('server_name optiti.test;', 'server_name malicious.test;'),
            edited.replace('fastcgi_pass unix:/run/php/devstack-optiti.sock;', 'fastcgi_pass 127.0.0.1:9000;'),
            edited.replace(d.EXTRA_END, '# other\n' + d.EXTRA_END),
        ):
            with self.assertRaises(d.InputError, msg=unsafe):
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
                    d.update_config({'slug':'alpha','rules':'client_max_body_size 64m;'})
            self.assertEqual(target.read_text(), 'old config')
            self.assertEqual(json.loads(state.read_text())['alpha']['overrides'], '')

if __name__ == '__main__':
    unittest.main()
