#!/usr/bin/env python3
"""Disposable CI only: installed stack without optional project services."""
import base64
import json
import os
from pathlib import Path
import re
import secrets
import smtplib
import sqlite3
import subprocess
import sys
import time
import urllib.request
from email.message import EmailMessage

if (os.geteuid() != 0 or os.environ.get('GITHUB_ACTIONS') != 'true'
        or 'microsoft' in Path('/proc/sys/kernel/osrelease').read_text().lower()):
    raise SystemExit('Disposable non-WSL GitHub Actions runner required.')
if len(sys.argv) != 2 or sys.argv[1] not in ('missing', 'upgrade'):
    raise SystemExit('Expected missing or upgrade.')

ROOT = Path(__file__).resolve().parents[1]
STATE = Path('/var/lib/devstack')
CFG = json.loads(Path('/etc/devstack/server.json').read_text())
USER = CFG['dev_user']
PROJECTS = Path(CFG['projects_dir'])
OPTIONAL = ('mariadb.service', 'redis-server.service')
UNIT = Path('/etc/systemd/system/devstackd.service')
CORE_UNIT = (ROOT/'scripts/devstackd.service').read_text()
LEGACY_UNIT = CORE_UNIT.replace('After=local-fs.target nginx.service php8.5-fpm.service\n',
                               'After=local-fs.target nginx.service php8.5-fpm.service mariadb.service\n').replace(
                               'Wants=nginx.service php8.5-fpm.service\n',
                               'Wants=nginx.service php8.5-fpm.service mariadb.service\n')
SLUG = 'core-sqlite-fixture'
DB_SLUG = 'core-mariadb-fixture'
LARAVEL = 'core-laravel-fixture'
NOTICE = Path('/run/devstack-ci-notice-id')


def run(*args, check=True, input=None, cwd=None, env=None, timeout=180):
    return subprocess.run(args, check=check, input=input, cwd=cwd, env=env,
                          text=True, capture_output=True, timeout=timeout)


def ctl(*args, check=True):
    return run('/usr/bin/systemctl', *args, check=check)


def web_php(code, *args):
    return run('/usr/sbin/runuser', '-u', 'devstackweb', '--', '/usr/bin/php',
               '-r', code, *args).stdout


def rpc(op, data=None, expect_ok=True):
    result = json.loads(web_php(
        'require "/opt/devstack/security.php";'
        'try {echo json_encode(["ok"=>true,"result"=>devstack_rpc($argv[1],json_decode($argv[2],true))]);}'
        'catch(Throwable $e){echo json_encode(["ok"=>false,"error"=>$e->getMessage()]);}',
        op, json.dumps(data or {})))
    assert result['ok'] is expect_ok, (op, result.get('error', 'unexpected success'))
    return result['result'] if expect_ok else result['error']


def wait_rpc():
    for _ in range(30):
        try:
            rpc('get_preferences')
            return
        except (AssertionError, subprocess.SubprocessError, ValueError):
            time.sleep(0.5)
    raise AssertionError('DevStack RPC not ready')


def unit_state(unit):
    result = ctl('show', '-p', 'LoadState', '-p', 'ActiveState',
                 '-p', 'ActiveEnterTimestampMonotonic', unit, check=False)
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    assert {'LoadState','ActiveState'} <= values.keys(), unit
    return values


def optional_snapshot(mode):
    result = {name: unit_state(name) for name in OPTIONAL}
    for value in result.values():
        assert value['LoadState'] == ('not-found' if mode == 'missing' else 'loaded'), result
        assert value['ActiveState'] == 'inactive', result
    return result


def dashboard():
    for service in ('nginx.service','php8.5-fpm.service','devstackd.service',
                    'devstack-notifications.timer'):
        ctl('is-active','--quiet',service)
    html = run('/usr/bin/curl','-fsS','--noproxy','*','--cacert',
               str(STATE/'ca/rootCA.pem'),'--resolve','devstack.test:443:127.0.0.1',
               'https://devstack.test/').stdout
    assert '<div id="root"></div>' in html
    return rpc('status')


def project_payload(slug, database=False):
    return {'slug':slug,'name':slug,'domain':slug+'.test','webroot':'public',
            'database':database,'https':True,'local_mail':False,'engine':'none'}


def app_response(slug):
    for _ in range(30):
        result = run('/usr/bin/curl','-fsS','--noproxy','*','--max-time','5',
                     '--cacert',str(STATE/'ca/rootCA.pem'),
                     '--resolve',slug+'.test:443:127.0.0.1','https://'+slug+'.test/',
                     check=False)
        if result.returncode == 0:
            return result.stdout
        time.sleep(1)
    raise AssertionError('HTTPS fixture did not become ready: '+slug)


def read_widget(version):
    token = Path('/etc/devstack-widgets/services-read.token').read_text().strip()
    req = urllib.request.Request('http://127.0.0.1:43872/v'+str(version)+'/services',
                                 headers={'Authorization':'Bearer '+token})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=8) as reply:
        return json.load(reply)


def widgets(mode, count):
    for version in (1, 2):
        snapshot = read_widget(version)
        assert snapshot['schema'] == version and snapshot['projectCount'] == count
        rows = {row['id']:row for row in snapshot['services']}
        assert rows['nginx']['active'] is True and rows['php']['active'] is True
        for name in ('mariadb','redis'):
            if mode == 'missing':
                if version == 1:
                    assert rows[name]['active'] is None
                else:
                    assert name not in rows
            else:
                assert rows[name]['active'] is False
        if version == 2 and count:
            assert [row['id'] for row in snapshot['services'] if row['selected']] == ['nginx','php']
        assert SLUG not in json.dumps(snapshot) and DB_SLUG not in json.dumps(snapshot)


def reject_create():
    slug = 'unavailable-mariadb-fixture'
    before = (STATE/'projects.json').read_bytes()
    assert 'MariaDB' in rpc('create', project_payload(slug, True), expect_ok=False)
    assert (STATE/'projects.json').read_bytes() == before
    assert not (PROJECTS/slug).exists()
    assert not Path('/etc/nginx/sites-available/devstack-'+slug+'.conf').exists()
    assert not Path('/etc/php/8.5/fpm/pool.d/devstack-'+slug+'.conf').exists()


def journal():
    categories = json.loads(web_php('require "/opt/devstack/security.php";'
        'require "/opt/devstack/notifications.php"; echo json_encode((new DevStackNotices())->categories());'))
    assert categories == ['mail','project']
    with sqlite3.connect('file:/var/lib/devstack-web/notifications.sqlite3?mode=ro', uri=True) as db:
        assert db.execute('SELECT count(*) FROM notices WHERE id=?',
                          (NOTICE.read_text(),)).fetchone()[0] == 1
        assert db.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
    ctl('start','devstack-notifications.service')
    assert ctl('show','-p','Result','--value','devstack-notifications.service').stdout.strip() == 'success'
    assert rpc('notification_snapshot')['services']



def registration_id(slug):
    registry = json.loads((STATE/'projects.json').read_text())
    value = registry[slug]['project_id']
    assert isinstance(value, str) and re.fullmatch('[0-9a-f]{32}', value)
    assert sum(record.get('project_id') == value for record in registry.values()) == 1
    return value


def isolation_preflight(slug, *, database=False):
    installed = Path('/opt/devstack/daemon/project_isolation_preflight.py')
    assert installed.stat().st_uid == 0 and not installed.stat().st_mode & 0o022
    before = (STATE/'projects.json').read_bytes()
    pool = Path('/etc/php/8.5/fpm/pool.d/devstack-'+slug+'.conf')
    original = pool.read_bytes()
    probe = run('/usr/bin/python3', '-I', str(installed),
                '--project', slug, check=False)
    if probe.returncode:
        # Disposable fixture only: report metadata of known control ancestors,
        # not registry content or any application credentials.
        for directory in (Path('/'), Path('/opt'), Path('/opt/devstack'),
                          Path('/opt/devstack/daemon'), Path('/etc'),
                          Path('/etc/devstack'), Path('/var'), Path('/var/lib'),
                          Path('/var/lib/devstack')):
            info = directory.lstat()
            print('Preflight control path:', directory, 'uid=', info.st_uid,
                  'mode=', oct(info.st_mode), flush=True)
    assert probe.returncode == 0, (probe.returncode, probe.stderr[-1024:])
    result = json.loads(probe.stdout)
    assert result['project'] == slug and result['status'] == 'blocked', result
    assert result['layout']['project'] == 'present' and result['layout']['webroot'] == 'present', result
    assert result['settings']['database'] is database
    assert 'sandbox_backend_security_review' in result['blockers']
    assert 'production_runtime_not_connected' in result['blockers']
    assert not result['sandbox']['approved']
    assert result['registration_identity'] == 'registered'
    assert 'project_identity_enrollment_required' not in result['blockers']
    if database:
        assert 'project_service_access_review' in result['blockers']
    assert (STATE/'projects.json').read_bytes() == before and pool.read_bytes() == original
    rejected = run('/usr/bin/python3', '-I', str(installed), '--project', 'devstack-source', check=False)
    assert rejected.returncode == 2
    print('PASS: Installed read-only isolation preflight blocks activation', flush=True)

    # Test the actual installer/updater's deployed entry point as well as
    # the isolated backport fixture. Status must not require Bubblewrap,
    # allocate a principal or touch the ordinary FPM/registry.
    pilot = Path('/opt/devstack/daemon/project_pilot.py')
    status = run('/usr/bin/python3', '-I', str(pilot), 'status', slug, check=False)
    if database:
        assert status.returncode == 2, (status.returncode, status.stderr[-2000:])
    else:
        assert status.returncode == 0, (status.returncode, status.stderr[-2000:])
        data = json.loads(status.stdout)
        assert data['project'] == slug and data['status'] == 'not-enrolled', data
        assert data['project_id'] == registration_id(slug)
        assert not (STATE / ('pilot-' + data['project_id'])).exists()
    assert (STATE/'projects.json').read_bytes() == before and pool.read_bytes() == original
    print('PASS: Installed pilot status preserves ordinary project and service state', flush=True)


def missing():
    assert UNIT.read_text() == CORE_UNIT
    before = optional_snapshot('missing')
    assert dashboard()['projects'] == []
    assert json.loads((STATE/'projects.json').read_text()) == {}
    assert (STATE/'projects.json').stat().st_mode & 0o777 == 0o600
    services = ROOT/'windows/widgets/services'
    names = ('bridge.py','setup.py','devstack-widget-services.socket','devstack-widget-services@.service',
             'devstack-widget-control.socket','devstack-widget-control@.service')
    payload = {'readToken':secrets.token_hex(32),'controlToken':secrets.token_hex(32),
               'frontend':False,'files':{name:base64.b64encode((services/name).read_bytes()).decode() for name in names}}
    run('/usr/bin/python3','-I',str(services/'setup.py'),input=json.dumps(payload))
    widgets('missing',0)
    reject_create()
    rpc('create',{**project_payload(SLUG), 'project_id':'f'*32})
    original_id = registration_id(SLUG)
    assert original_id != 'f'*32
    # This is a real PHP-FPM application using its own SQLite file, not the
    # DevStack notification database or an emulated project API.
    php = '<?php $d=new PDO("sqlite:".dirname(__DIR__)."/fixture.sqlite");'
    php += '$d->exec("CREATE TABLE IF NOT EXISTS proof (id INTEGER PRIMARY KEY)");'
    php += '$d->exec("INSERT OR IGNORE INTO proof VALUES (1)");echo $d->query("SELECT count(*) FROM proof")->fetchColumn();'
    run('/usr/sbin/runuser','-u',USER,'--','/usr/bin/python3','-c',
        'from pathlib import Path;import sys;Path(sys.argv[1]).write_text(sys.stdin.read())',
        str(PROJECTS/SLUG/'public/index.php'),input=php)
    assert app_response(SLUG) == '1'
    isolation_preflight(SLUG)
    assert app_response(SLUG) == '1'
    # Exercise the actual existing editor, including the managed tuning rule.
    rpc('update_config', {'slug':SLUG, 'overrides':'client_max_body_size 32m;'})
    assert registration_id(SLUG) == original_id
    assert app_response(SLUG) == '1'
    rpc('set_preferences',{'theme':'dark'})
    rpc('set_widget_services',{'services':['nginx','php']})
    widgets('missing',1)
    assert SLUG+'.test' in run('/usr/bin/python3','/opt/devstack/scripts/list-domains.py').stdout.splitlines()
    notice = json.loads(web_php('require "/opt/devstack/security.php";require "/opt/devstack/notifications.php";'
        '$s=new DevStackNotices();$s->setCategories(["mail","project"]);'
        'echo json_encode($s->operation("/api/projects",["slug"=>$argv[1]],["message"=>"CI fixture"],"",true));',SLUG))
    assert isinstance(notice,str) and re.fullmatch('op-[a-f0-9]{32}',notice)
    NOTICE.write_text(notice)
    message=EmailMessage()
    message['From']='ci@example.test'
    message['To']='recipient@example.test'
    message['Subject']='Core-only local SMTP fixture'
    message.set_content('Local fixture')
    with smtplib.SMTP('127.0.0.1',1025,timeout=5) as smtp:
        smtp.send_message(message)
    mailbox=json.loads(web_php('require "/opt/devstack/security.php";echo json_encode(devstack_mailpit_request("list",[]));'))
    assert any(row['Subject']==message['Subject'] for row in mailbox['messages'])
    journal()
    ctl('restart','devstackd.service')
    wait_rpc()
    assert rpc('get_preferences') == {'theme':'dark'}
    assert app_response(SLUG) == '1'
    assert registration_id(SLUG) == original_id
    assert optional_snapshot('missing') == before
    print('PASS: actual core-only install, HTTPS/SQLite app, RPC, preferences, mail, journal, hosts domains, v1/v2 widgets and explicit DB refusal.',flush=True)


def update():
    checkout=PROJECTS/'devstack-source'
    assert (checkout/'scripts/apply-update.sh').is_file()
    return run('/usr/bin/bash','scripts/apply-update.sh',cwd=checkout,
               env={**os.environ,'SUDO_USER':USER},check=False,timeout=240)


def upgrade():
    for unit in OPTIONAL:
        ctl('is-active','--quiet',unit)
    rpc('create',project_payload(DB_SLUG,True))
    original_sqlite_id = registration_id(SLUG)
    original_db_id = registration_id(DB_SLUG)
    assert original_db_id != original_sqlite_id
    rpc('test_project_database',{'slug':DB_SLUG})
    registry=json.loads((STATE/'projects.json').read_text())
    db_name=registry[DB_SLUG]['db_name']
    assert re.fullmatch('[a-z0-9]{1,64}',db_name)
    run('/usr/bin/mariadb','--protocol=socket','--database='+db_name,
        '-e','CREATE TABLE ci_proof (v INT); INSERT INTO ci_proof VALUES (42);')
    # Nonempty registry, credentials and preferences must survive the update
    # byte-for-byte. No secret values are printed or put in artifacts.
    paths=(STATE/'projects.json',STATE/'credentials.json',STATE/'ui-preferences.json',
           Path('/var/lib/devstack-widget-selection.json'))
    original={path:path.read_bytes() for path in paths}
    UNIT.write_text(LEGACY_UNIT)
    ctl('daemon-reload')
    ctl('restart','devstackd.service')
    wait_rpc()
    ctl('stop',*OPTIONAL)
    before=optional_snapshot('stopped')
    # Trigger the real updater's existing late-stage failure (unit mismatch)
    # after a code swap. The previous code must return, without the old
    # MariaDB dependency or any wake of either project service.
    notice_unit=Path('/etc/systemd/system/devstack-notifications.service')
    notice_before=notice_unit.read_bytes()
    marker=Path('/opt/devstack/push-helper.php')
    assert not marker.exists()
    marker.write_text('CI previous-code marker\n')
    notice_unit.write_bytes(notice_before+b'\n# disposable rollback fixture\n')
    try:
        result=update()
        assert result.returncode != 0 and 'Notification unit differs:' in result.stderr, result.stderr[-1500:]
        assert 'restoring previous installed application' in result.stderr
        assert marker.read_text() == 'CI previous-code marker\n'
        assert UNIT.read_text() == CORE_UNIT
        assert optional_snapshot('stopped') == before
    finally:
        notice_unit.write_bytes(notice_before)
        ctl('daemon-reload')
    wait_rpc()
    assert dashboard()['projects']
    # Repeat from the exact legacy unit with the DBs still stopped.
    # daemon-reload itself must not start either one.
    UNIT.write_text(LEGACY_UNIT)
    ctl('daemon-reload')
    assert optional_snapshot('stopped') == before
    result=update()
    assert result.returncode == 0, result.stderr[-2000:]
    assert not marker.exists() and UNIT.read_text() == CORE_UNIT
    assert all(path.read_bytes()==data for path,data in original.items())
    assert optional_snapshot('stopped') == before
    wait_rpc()
    ctl('restart','devstackd.service')
    wait_rpc()
    assert rpc('get_preferences') == {'theme':'dark'}
    widgets('stopped',2)
    isolation_preflight(SLUG)
    isolation_preflight(DB_SLUG, database=True)
    assert registration_id(SLUG) == original_sqlite_id
    assert registration_id(DB_SLUG) == original_db_id
    assert app_response(SLUG) == '1'
    reject_create()
    assert 'MariaDB' in rpc('test_project_database',{'slug':DB_SLUG},expect_ok=False)
    assert 'MariaDB' in rpc('delete_project',{'slug':DB_SLUG,'confirmation':DB_SLUG,
                                              'delete_database':True},expect_ok=False)
    assert all(path.read_bytes()==data for path,data in original.items())
    assert Path('/etc/nginx/sites-available/devstack-'+DB_SLUG+'.conf').is_file()
    journal()
    # Real official Laravel installer through the production asynchronous
    # daemon/worker, with no asset build required for this database test.
    tools=rpc('engine_tool_status',{'engine':'laravel'})
    assert tools['installed']
    assert any(kit['id']=='none' for kit in tools['starter_kits'])
    assert any(field['id']=='assets' and any(c['id']=='no' for c in field['choices'])
               for field in tools['installer_options'])
    payload=project_payload(LARAVEL)
    payload.update(engine='laravel',starter_kit='none',installer_options={'assets':'no'})
    job=rpc('create',payload)['job_id']
    for _ in range(450):
        snapshot=rpc('notification_snapshot')
        current=snapshot.get('engine_job') or {}
        if current.get('id')==job and current.get('state') in ('succeeded','failed'):
            assert current['state']=='succeeded', current.get('message','installer failed')
            break
        time.sleep(2)
    else:
        raise AssertionError('Laravel fixture did not finish')
    db=PROJECTS/LARAVEL/'database/database.sqlite'
    assert db.is_file()
    with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as conn:
        assert conn.execute('SELECT count(*) FROM migrations').fetchone()[0]>0
    assert app_response(LARAVEL)
    assert registration_id(LARAVEL) not in (original_sqlite_id, original_db_id)
    isolation_preflight(LARAVEL)
    widgets('stopped',3)
    assert optional_snapshot('stopped') == before
    # Only this explicit user-equivalent action may start MariaDB/Redis.
    ctl('start',*OPTIONAL)
    rpc('test_project_database',{'slug':DB_SLUG})
    assert run('/usr/bin/mariadb','--protocol=socket','--database='+db_name,
               '--batch','--skip-column-names','-e','SELECT v FROM ci_proof;').stdout.strip()=='42'
    # Reuse the already-created SQLite app rather than another test project.
    # Removal only unregisters managed hosting, never application files.
    app = PROJECTS/SLUG
    original_source = (app/'public/index.php').read_bytes()
    database_path = app/'fixture.sqlite'
    before_db = database_path.read_bytes()
    rpc('delete_project', {'slug':SLUG, 'confirmation':SLUG, 'delete_database':False})
    assert SLUG not in json.loads((STATE/'projects.json').read_text())
    assert (app/'public/index.php').read_bytes() == original_source
    assert database_path.read_bytes() == before_db
    assert SLUG+'.test' not in run('/usr/bin/python3','/opt/devstack/scripts/list-domains.py').stdout.splitlines()
    rpc('create', project_payload(SLUG))
    assert registration_id(SLUG) != original_sqlite_id
    assert registration_id(DB_SLUG) == original_db_id
    assert (app/'public/index.php').read_bytes() == original_source
    assert app_response(SLUG) == '1'
    assert SLUG+'.test' in run('/usr/bin/python3','/opt/devstack/scripts/list-domains.py').stdout.splitlines()
    isolation_preflight(SLUG)
    print('PASS: project registration identity follows create/edit/restart/upgrade/delete/recreate, including actual Laravel installer.',flush=True)
    print('PASS: legacy unit upgrade and rollback, preserved state/credentials/project data, stopped services, actual Laravel SQLite installation and explicit MariaDB recovery.',flush=True)


if sys.argv[1]=='missing':
    missing()
else:
    upgrade()
