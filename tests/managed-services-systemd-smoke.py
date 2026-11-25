#!/usr/bin/env python3
"""Exercise real systemd on a disposable GitHub runner, never app services."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.geteuid() != 0:
    raise SystemExit('Only for the disposable GitHub Actions systemd runner.')
root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('managed_fixture', root/'daemon/managed_services.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
suffix = uuid.uuid4().hex[:10]
unit = 'devstack-ci-service-'+suffix+'.service'
missing = 'devstack-ci-missing-'+suffix+'.service'
m.ACTION_UNIT = 'devstack-ci-control-'+suffix
unit_path = Path('/run/systemd/system')/unit

def ctl(*args):
    return subprocess.run(['/usr/bin/systemctl', *args], capture_output=True, text=True, check=True, timeout=15).stdout.strip()

def await_state(expected):
    for _ in range(120):
        state=m.public_rows()[0]['state']
        if state==expected and not m.action_pending():
            return
        time.sleep(.25)
    raise AssertionError('Expected state '+expected+', got '+state)

try:
    unit_path.write_text('[Unit]\nDescription=Disposable DevStack control fixture\n[Service]\nType=simple\nExecStart=/usr/bin/sleep infinity\n')
    ctl('daemon-reload')
    with tempfile.TemporaryDirectory() as tmp:
        m.CATALOG=Path(tmp)/'catalog.json';m.SELECTION=Path(tmp)/'selection.json'
        def row(id,name,unit):
            return {'id':id,'name':name,'unit':unit,'description':'CI fixture','probe':'unit','port':0,'widget_default':True,'panel_dependency':False}
        m.CATALOG.write_text(json.dumps([row('fixture','Fixture',unit),row('missing','Missing',missing)]))
        assert [r['id'] for r in m.public_rows()]==['fixture']
        assert m.public_rows()[0]['state']=='inactive'
        assert m.save_selection([],m.public_rows())==[]
        assert not m.public_rows()[0]['widget_selected']
        assert m.schedule('fixture','start')['accepted']
        assert m.action_pending(), 'operation must be pending during delay'
        try:
            m.schedule('fixture','restart')
            raise AssertionError('Concurrent action accepted')
        except RuntimeError:
            pass
        await_state('active')
        before=ctl('show','--property=MainPID','--value',unit)
        assert m.schedule('fixture','restart')['accepted']
        await_state('active')
        assert ctl('show','--property=MainPID','--value',unit)!=before
        assert m.schedule('fixture','stop')['accepted']
        await_state('inactive')
        assert m.schedule('fixture','start')['accepted']
        await_state('active')
        print('PASS: installed and absent units, persistent empty selection, delayed start/restart/stop/start and busy guard.')
finally:
    subprocess.run(['/usr/bin/systemctl','stop',m.ACTION_UNIT+'.timer',m.ACTION_UNIT+'.service',unit],capture_output=True,timeout=20)
    unit_path.unlink(missing_ok=True)
    subprocess.run(['/usr/bin/systemctl','daemon-reload'],capture_output=True,timeout=15)
