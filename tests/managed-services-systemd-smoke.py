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
unit_path = Path('/etc/systemd/system')/unit

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
    unit_path.write_text('[Unit]\nDescription=Disposable DevStack control fixture\n[Service]\nType=simple\nExecStart=/usr/bin/sleep infinity\n[Install]\nWantedBy=multi-user.target\n')
    ctl('daemon-reload')
    with tempfile.TemporaryDirectory() as tmp:
        m.CATALOG=Path(tmp)/'catalog.json';m.SELECTION=Path(tmp)/'selection.json'
        m.RESULTS_DB=Path(tmp)/'receipts.sqlite3'
        # ExecStopPost runs in its own real process. Point only this disposable
        # root-owned fixture at its own catalogue and database.
        m.RESULT_HELPER=Path(tmp)/'completion.py'
        m.RESULT_HELPER.write_text(
            'import importlib.util, os, sys\nfrom pathlib import Path\n'
            f'spec=importlib.util.spec_from_file_location("fixture", {str(root/"daemon/managed_services.py")!r})\n'
            'm=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)\n'
            f'm.CATALOG=Path({str(m.CATALOG)!r});m.RESULTS_DB=Path({str(m.RESULTS_DB)!r})\n'
            'os.umask(0o077)\nm.record_completion(*sys.argv[2:], env=os.environ)\n')
        def await_receipt(receipt, expected):
            for _ in range(120):
                rows={r['id']:r for r in m.completion_results()}
                if receipt in rows:
                    assert rows[receipt]['outcome']==expected, rows[receipt]
                    return rows[receipt]
                time.sleep(.25)
            raise AssertionError('Missing actual service completion receipt '+receipt)
        def row(id,name,unit):
            return {'id':id,'name':name,'unit':unit,'description':'CI fixture','probe':'unit','port':0,'widget_default':True,'panel_dependency':False}
        m.CATALOG.write_text(json.dumps([row('fixture','Fixture',unit),row('missing','Missing',missing)]))
        assert [r['id'] for r in m.public_rows()]==['fixture']
        assert m.public_rows()[0]['state']=='inactive'
        assert m.public_rows()[0]['autostart'] is False
        assert m.set_autostart('fixture',True)['autostart'] is True
        assert ctl('is-enabled',unit)=='enabled'
        assert m.public_rows()[0]['state']=='inactive'
        assert m.set_autostart('fixture',False)['autostart'] is False
        assert m.save_selection([],m.public_rows())==[]
        assert not m.public_rows()[0]['widget_selected']
        accepted=m.schedule('fixture','start')
        assert accepted['accepted']
        assert m.action_pending(), 'operation must be pending during delay'
        try:
            m.schedule('fixture','restart')
            raise AssertionError('Concurrent action accepted')
        except RuntimeError:
            pass
        await_state('active')
        await_receipt(accepted['receipt'],'success')
        before=ctl('show','--property=MainPID','--value',unit)
        for enabled in (True,False):
            assert m.set_autostart('fixture',enabled)['autostart'] is enabled
            assert m.public_rows()[0]['autostart'] is enabled
            assert m.public_rows()[0]['state']=='active'
            assert ctl('show','--property=MainPID','--value',unit)==before
        accepted=m.schedule('fixture','restart')
        await_state('active')
        assert ctl('show','--property=MainPID','--value',unit)!=before
        await_receipt(accepted['receipt'],'success')
        accepted=m.schedule('fixture','stop')
        await_state('inactive')
        await_receipt(accepted['receipt'],'success')
        # A genuinely failing unit must produce an error receipt, even if
        # systemd subsequently collects the transient controller.
        unit_path.write_text('[Unit]\nDescription=Failing DevStack fixture\n[Service]\nType=oneshot\nExecStart=/usr/bin/false\n')
        ctl('daemon-reload')
        accepted=m.schedule('fixture','start')
        await_state('failed')
        await_receipt(accepted['receipt'],'error')
        unit_path.write_text('[Unit]\nDescription=Disposable DevStack control fixture\n[Service]\nType=simple\nExecStart=/usr/bin/sleep infinity\n[Install]\nWantedBy=multi-user.target\n')
        ctl('daemon-reload')
        ctl('reset-failed',unit)
        accepted=m.schedule('fixture','start')
        await_state('active')
        await_receipt(accepted['receipt'],'success')
        print('PASS: installed and absent units, persistent empty selection, delayed start/restart/stop, actual success and failed receipts, and busy guard.')
finally:
    subprocess.run(['/usr/bin/systemctl','stop',m.ACTION_UNIT+'.timer',m.ACTION_UNIT+'.service',unit],capture_output=True,timeout=20)
    subprocess.run(['/usr/bin/systemctl','disable',unit],capture_output=True,timeout=15)
    unit_path.unlink(missing_ok=True)
    subprocess.run(['/usr/bin/systemctl','daemon-reload'],capture_output=True,timeout=15)
