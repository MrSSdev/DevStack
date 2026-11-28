"""No root or system services are used by these unit tests."""
import base64
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
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

    def test_isolated_nginx_has_only_the_registration_upstream(self):
        key = 'a' * 32
        root = Path('/home/developer/projects/alpha/public')
        text = d.nginx_conf('alpha', 'alpha.test', root, True, isolation_id=key)
        self.assertIn(f'proxy_pass http://unix:/run/devstack-pilot-{key}-http/sockets/http.sock;', text)
        self.assertIn('proxy_ignore_headers X-Accel-Redirect;', text)
        self.assertIn('proxy_set_header X-Forwarded-For $remote_addr;', text)
        self.assertNotIn('fastcgi_pass', text)
        self.assertNotIn(str(root), text)
        for ssl, rules, identity in ((False, '', key), (True, 'client_max_body_size 30m;', key),
                                     (True, '', '../arbitrary')):
            with self.subTest(ssl=ssl, rules=rules, identity=identity), self.assertRaises(d.InputError):
                d.nginx_conf('alpha', 'alpha.test', root, ssl, rules, isolation_id=identity)

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
            self.assertEqual(args[0][0], '/usr/bin/systemd-run')
            self.assertEqual(args[0][-3:-1], ['/usr/bin/python3','-I'])
            self.assertTrue(args[0][-1].endswith('/daemon/project_frameworks.py'))
            self.assertIn('--uid=developer', args[0])
            self.assertIn('--gid=1001', args[0])
            self.assertIn('--working-directory=/', args[0])
            self.assertIn('--property=NoNewPrivileges=yes', args[0])
            self.assertIn('--property=ProtectHome=read-only', args[0])
            self.assertIn('--property=ProtectSystem=strict', args[0])
            self.assertIn('--property=RuntimeMaxSec=5s', args[0])
            self.assertIn('--property=InaccessiblePaths=-/etc/devstack -/var/lib/devstack -/var/lib/devstack-web -/run/devstack -/mnt/c', args[0])
            self.assertNotIn('user', kwargs)
            self.assertNotIn('group', kwargs)
            self.assertEqual(json.loads(kwargs['input']), ['demo','other','unknown'])
            self.assertEqual(kwargs['timeout'], 10)
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

    def test_lightweight_service_status_uses_only_registered_units(self):
        rows = [{'id':'redis','unit':'redis-server.service','state':'inactive','autostart':False},
                {'id':'mailpit','unit':'devstack-mailpit.service','state':'active','autostart':True}]
        with patch.object(d.managed_services,'public_rows',return_value=rows) as read, \
             patch.object(d.managed_services,'action_pending',return_value=True), \
             patch.object(d,'public_projects',side_effect=AssertionError('Project scan in fast service read')), \
             patch.object(d,'get_ui_preferences',side_effect=AssertionError('Preference read in fast service read')), \
             patch.object(d,'public_engine_job',side_effect=AssertionError('Job read in fast service read')):
            reply=json.loads(d.handle(b'{"op":"service_status","data":{}}'))
            self.assertEqual(reply,{'ok':True,'result':{
                'services':{'redis-server':'inactive','devstack-mailpit':'active'},
                'managed_services':rows,'service_action_pending':True,'mailpit':'active'}})
            read.assert_called_once_with()
            invalid=json.loads(d.handle(b'{"op":"service_status","data":{"service":"sshd"}}'))
            self.assertFalse(invalid['ok'])
            read.assert_called_once_with()

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
        for backup_kind in ('missing', 'file', 'symlink', 'directory'):
            with self.subTest(backup_kind=backup_kind):
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
                    backup=folder/'vhost-demo-previous.conf'
                    unrelated=folder/'vhost-other-previous.conf'
                    unrelated.write_text('other project backup')
                    if backup_kind == 'file':
                        backup.write_text('previous nginx config')
                    elif backup_kind == 'symlink':
                        backup.symlink_to(unrelated)
                    elif backup_kind == 'directory':
                        backup.mkdir()
                        (backup/'unexpected').write_text('keep')
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
                    self.assertEqual(unrelated.read_text(),'other project backup')
                    if backup_kind == 'directory':
                        self.assertEqual((backup/'unexpected').read_text(),'keep')
                        self.assertIn('Nie udalo sie usunac kopii',result['message'])
                    else:
                        self.assertFalse(backup.exists())
                        self.assertFalse(backup.is_symlink())
                        self.assertNotIn('Nie udalo sie usunac kopii',result['message'])
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
            backup = folder / 'vhost-demo-previous.conf'
            backup.write_text('previous nginx config')
            meta = {'name':'Demo','domain':'demo.test','path':str(root),
                    'webroot':'public','https':False,'database':False,
                    'db_name':'','db_user':'','overrides':'', 'project_id':'c'*32}
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
            self.assertEqual(backup.read_text(),'previous nginx config')
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
                 patch.object(d,'require_mariadb_available'), \
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

    def test_empty_project_registry_is_created_without_overwriting_existing_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'projects.json'
            with patch.object(d, 'STATE_FILE', path):
                d.ensure_project_registry()
                self.assertEqual(json.loads(path.read_text()), {})
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                d.write_json_atomic(path, {'demo': {'domain': 'demo.test'}})
                before = path.read_bytes()
                d.ensure_project_registry()
                self.assertEqual(path.read_bytes(), before)
                path.unlink()
                other = Path(tmp) / 'other.json'
                other.write_text('{}')
                path.symlink_to(other)
                with self.assertRaises(RuntimeError):
                    d.ensure_project_registry()
                self.assertEqual(other.read_text(), '{}')

    def test_mariadb_guard_never_starts_the_service(self):
        from types import SimpleNamespace
        import subprocess
        with patch.object(d.os, 'access', return_value=False), patch.object(d.subprocess, 'run') as run:
            with self.assertRaisesRegex(d.InputError, 'MariaDB'):
                d.require_mariadb_available()
            run.assert_not_called()
        with patch.object(d.os, 'access', return_value=True), patch.object(d.subprocess, 'run') as run:
            for outcome in (SimpleNamespace(returncode=3), SimpleNamespace(returncode=4)):
                run.return_value = outcome
                with self.assertRaisesRegex(d.InputError, 'MariaDB'):
                    d.require_mariadb_available()
                self.assertEqual(run.call_args.args[0],
                                 ['/usr/bin/systemctl', 'is-active', '--quiet', 'mariadb.service'])
            run.return_value = SimpleNamespace(returncode=0)
            d.require_mariadb_available()
            run.side_effect = subprocess.TimeoutExpired('systemctl', 5)
            with self.assertRaises(d.InputError):
                d.require_mariadb_available()

    def test_stopped_mariadb_rejects_create_before_touching_project_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            with patch.object(d, 'config', return_value={'projects_dir': str(folder), 'dev_user': 'ubuntu'}), \
                 patch.object(d, 'STATE_FILE', folder/'projects.json'), \
                 patch.object(d, 'SECRET_FILE', folder/'credentials.json'), \
                 patch.object(d, 'site_path', return_value=folder/'site.conf'), \
                 patch.object(d, 'site_link_path', return_value=folder/'enabled.conf'), \
                 patch.object(d, 'pool_path', return_value=folder/'pool.conf'), \
                 patch.object(d, 'require_mariadb_available', side_effect=d.InputError('MariaDB unavailable')), \
                 patch.object(d, 'ensure_project_paths') as paths, \
                 patch.object(d, 'create_db') as database:
                with self.assertRaisesRegex(d.InputError, 'MariaDB'):
                    d.create_environment({'slug':'demo','name':'Demo','domain':'demo.test',
                                          'webroot':'public','database':True,'https':False})
                paths.assert_not_called()
                database.assert_not_called()
                self.assertEqual(list(folder.iterdir()), [])

    def test_secret_not_in_mariadb_error(self):
        import subprocess
        with patch.object(d, 'run', side_effect=RuntimeError("SQL with password secret-should-not-leak")):
            with self.assertRaisesRegex(RuntimeError, 'Sprawdz log') as cm:
                d.mariadb_execute("SELECT 'secret-should-not-leak'")
            self.assertNotIn('secret-should-not-leak', str(cm.exception))

    def test_new_registration_identity_is_unique_and_not_a_slug(self):
        existing = 'a' * 32
        new = 'b' * 32
        state = {'legacy': {'name': 'Legacy'},
                 'active': {'project_id': existing},
                 'bad': {'project_id': []}}
        snapshot = json.dumps(state)
        with patch.object(d.secrets, 'token_hex', side_effect=[existing, new]) as token:
            self.assertEqual(d.new_project_id(state), new)
            self.assertEqual(token.call_count, 2)
            token.assert_called_with(16)
        self.assertEqual(json.dumps(state), snapshot)
        with patch.object(d.secrets, 'token_hex', return_value=existing):
            with self.assertRaises(RuntimeError):
                d.new_project_id(state)

    def test_create_commit_assigns_internal_identity_without_rewriting_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            state = folder / 'projects.json'
            secret = folder / 'credentials.json'
            legacy = {'old': {'domain': 'old.test', 'name': 'Legacy'}}
            d.write_json_atomic(state, legacy)
            root = folder / 'demo'
            public = root / 'public'
            site, link, pool = [folder / x for x in ('site.conf', 'enabled.conf', 'pool.conf')]
            payload = {'slug':'demo', 'name':'Demo', 'domain':'demo.test',
                       'webroot':'public', 'database':False, 'https':False,
                       'project_id':'f'*32}
            with patch.object(d, 'config', return_value={'projects_dir': str(folder), 'dev_user': 'ubuntu'}), \
                 patch.object(d, 'STATE_FILE', state), patch.object(d, 'SECRET_FILE', secret), \
                 patch.object(d, 'site_path', return_value=site), \
                 patch.object(d, 'site_link_path', return_value=link), \
                 patch.object(d, 'pool_path', return_value=pool), \
                 patch.object(d, 'ensure_project_paths', return_value=public), \
                 patch.object(d, 'grant_public_access'), patch.object(d, 'run', return_value=''), \
                 patch.object(d, 'defer_php_fpm_reload'), \
                 patch.object(d, 'request_windows_hosts_sync', return_value=''), \
                 patch.object(d.secrets, 'token_hex', return_value='1'*32):
                d.create_environment(payload)
                current = d.read_json(state)
                self.assertEqual(current['old'], legacy['old'])
                self.assertEqual(current['demo']['project_id'], '1'*32)
                self.assertNotEqual(current['demo']['project_id'], payload['project_id'])
                self.assertEqual(current['demo']['path'], str(root))
                with patch.object(d, 'project_frameworks', return_value={}):
                    self.assertTrue(all('project_id' not in p for p in d.public_projects()))
                self.assertEqual(d.read_json(state), current)

    def test_atomic_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            d.write_json_atomic(path, {'key': 'value'})
            self.assertEqual(d.read_json(path)['key'], 'value')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_windows_notification_uses_only_the_fixed_program_and_typed_input(self):
        event = {'id': 'mail-' + 'a'*22, 'category': 'mail', 'kind': 'info'}
        with tempfile.TemporaryDirectory() as tmp:
            mode = Path(tmp) / 'enabled'
            legacy = Path('/mnt/c/Users/Test User/AppData/Local/DevStack/LocalNotifications/DevStack.Notifications.exe')
            exe = Path('/mnt/c/DevStack/LocalNotifications/DevStack.Notifications.exe')
            is_file = Path.is_file
            account = SimpleNamespace(pw_name='fixture', pw_uid=1001, pw_gid=1001)
            good_state = ('LoadState=loaded\nActiveState=active\nSubState=exited\n'
                          'Result=success\nExecMainCode=1\nExecMainStatus=0\n')
            start_status = 0
            query_status = 0
            state_output = good_state
            start_error = None

            def fake_run(args, **kwargs):
                if args[0] == '/usr/bin/systemd-run':
                    if start_error is not None:
                        raise start_error
                    return SimpleNamespace(returncode=start_status)
                if args[2] == 'show':
                    return SimpleNamespace(returncode=query_status, stdout=state_output)
                self.assertIn(args[2], ('stop', 'reset-failed'))
                return SimpleNamespace(returncode=0)

            with (
                patch.object(d, 'WINDOWS_NOTIFICATION_MODE', mode),
                patch.object(Path, 'is_file', lambda file: file in (exe, legacy) or is_file(file)),
                patch.object(d, 'config', return_value={'dev_user': 'fixture'}),
                patch.object(d.pwd, 'getpwnam', return_value=account),
                patch.object(d.subprocess, 'run', side_effect=fake_run) as run,
            ):
                self.assertFalse(d.show_windows_notification(event)['accepted'])
                mode.write_text('/mnt/c/Users/Test User/powershell.exe\n')
                self.assertFalse(d.show_windows_notification(event)['accepted'])
                mode.write_text(str(exe) + '\n')
                for bad in ({**event, 'path': 'unexpected'}, {**event, 'id': 'https://other.test/'},
                            {**event, 'category': ['mail']}, {**event, 'kind': 'loading'}):
                    with self.assertRaises(d.InputError):
                        d.show_windows_notification(bad)
                account.pw_uid = 0
                self.assertFalse(d.show_windows_notification(event)['accepted'])
                account.pw_uid = 1001
                account.pw_gid = 0
                self.assertFalse(d.show_windows_notification(event)['accepted'])
                run.assert_not_called()
                account.pw_gid = 1001
                self.assertTrue(d.show_windows_notification(event)['accepted'])
                self.assertEqual(run.call_count, 4)
                launch, query, stop, reset = run.call_args_list
                arguments = launch.args[0]
                self.assertEqual(arguments[0], '/usr/bin/systemd-run')
                self.assertIn('--uid=1001', arguments)
                self.assertIn('--gid=1001', arguments)
                self.assertIn('--service-type=oneshot', arguments)
                self.assertIn('--expand-environment=no', arguments)
                for flag in ('--wait', '--pipe', '--collect', '--no-block'):
                    self.assertNotIn(flag, arguments)
                for option in ('NoNewPrivileges=yes', 'PrivateTmp=yes', 'PrivateDevices=yes',
                               'ProtectKernelTunables=yes', 'ProtectKernelModules=yes',
                               'ProtectControlGroups=yes', 'TimeoutStartSec=10s',
                               'TimeoutStopSec=2s', 'PartOf=devstackd.service',
                               'RemainAfterExit=yes', 'CollectMode=inactive',
                               'StandardOutput=null', 'StandardError=null', 'StandardInput=null'):
                    self.assertIn('--property=' + option, arguments)
                self.assertIn('--working-directory=/', arguments)
                self.assertNotIn('--working-directory=' + str(exe.parent), arguments)
                self.assertIn('--description=DevStack local Windows notification', arguments)
                self.assertEqual(arguments[-7:-1], ['/usr/bin/env', '-i', '--chdir=' + str(exe.parent),
                                                      'PATH=/usr/bin:/bin', 'LANG=C.UTF-8', str(exe)])
                self.assertEqual(json.loads(base64.b64decode(arguments[-1])), event)
                unit = next(arg.split('=', 1)[1] for arg in arguments if arg.startswith('--unit='))
                self.assertRegex(unit, r'^devstack-windows-notification-[a-f0-9]{16}\.service$')
                self.assertEqual(query.args[0][:3], ['/usr/bin/systemctl', '--system', 'show'])
                self.assertEqual(query.args[0][-2:], ['--', unit])
                for key in ('LoadState', 'ActiveState', 'SubState', 'Result', 'ExecMainCode', 'ExecMainStatus'):
                    self.assertIn('--property=' + key, query.args[0])
                self.assertEqual(stop.args[0], ['/usr/bin/systemctl', '--system', 'stop', '--', unit])
                self.assertEqual(reset.args[0], ['/usr/bin/systemctl', '--system', 'reset-failed', '--', unit])
                for key in ('user', 'group', 'extra_groups', 'shell', 'preexec_fn'):
                    self.assertNotIn(key, launch.kwargs)
                self.assertEqual(launch.kwargs['timeout'], 15)
                self.assertEqual(query.kwargs['timeout'], 3)
                run.reset_mock()
                mode.write_text(str(legacy) + '\n')
                self.assertTrue(d.show_windows_notification(event)['accepted'])
                self.assertEqual(run.call_args_list[0].args[0][-2], str(legacy))
                self.assertIn('--chdir=' + str(legacy.parent), run.call_args_list[0].args[0])
                run.reset_mock()
                mode.write_text('/mnt/d/DevStack/LocalNotifications/DevStack.Notifications.exe\n')
                self.assertFalse(d.show_windows_notification(event)['accepted'])
                run.assert_not_called()
                mode.write_text(str(exe) + '\n')
                with patch.object(d.sys, 'stderr') as stderr:
                    for bad_state in ('', good_state.replace('ExecMainStatus=0', 'ExecMainStatus=23'),
                                      good_state.replace('Result=success', 'Result=timeout'),
                                      good_state.replace('ActiveState=active', 'ActiveState=inactive'),
                                      good_state.replace('ExecMainCode=1', 'ExecMainCode=2')):
                        state_output = bad_state
                        run.reset_mock()
                        self.assertFalse(d.show_windows_notification(event)['accepted'])
                        self.assertEqual([call.args[0][2] for call in run.call_args_list[-2:]], ['stop', 'reset-failed'])
                    state_output = good_state
                    start_status = 1
                    self.assertFalse(d.show_windows_notification(event)['accepted'])
                    start_status = 0
                    query_status = 1
                    self.assertFalse(d.show_windows_notification(event)['accepted'])
                    query_status = 0
                    for error in (PermissionError(1, 'private fixture path'),
                                  d.subprocess.TimeoutExpired('/private/fixture', 15)):
                        start_error = error
                        run.reset_mock()
                        self.assertFalse(d.show_windows_notification(event)['accepted'])
                        self.assertEqual([call.args[0][2] for call in run.call_args_list[-2:]], ['stop', 'reset-failed'])
                    log = ''.join(call.args[0] for call in stderr.write.call_args_list)
                    self.assertIn('PermissionError', log)
                    self.assertIn('TimeoutExpired', log)
                    self.assertNotIn('private fixture path', log)
                    self.assertNotIn('/private/fixture', log)

    def test_nginx_config_rollback_on_validation_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'devstack-alpha.conf'
            target.write_text('old config', encoding='utf-8')
            state = Path(tmp) / 'state.json'
            state.write_text(json.dumps({'alpha': {'domain':'alpha.test','path':'/home/ubuntu/projects/alpha', 'webroot':'public','https':True, 'overrides':''}}), encoding='utf-8')
            backup = Path(tmp) / 'vhost-alpha-previous.conf'
            commands=[]
            def fake_run(argv, *a, **kw):
                commands.append(argv)
                if argv[-1]=='-t' and len(commands)==1: raise RuntimeError('test failed')
                return ''
            # Both state and the editor backup live in this disposable directory.
            with patch.object(d, 'STATE_FILE', state), patch.object(d, 'site_path', return_value=target), patch.object(d, 'run', side_effect=fake_run):
                with self.assertRaises(RuntimeError):
                    d.update_config({'slug':'alpha','rules':'client_max_body_size 64m;'})
            self.assertEqual(target.read_text(), 'old config')
            self.assertEqual(backup.read_text(), 'old config')
            self.assertEqual(json.loads(state.read_text())['alpha']['overrides'], '')

if __name__ == '__main__':
    unittest.main()
