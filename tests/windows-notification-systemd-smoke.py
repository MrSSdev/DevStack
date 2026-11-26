#!/usr/bin/env python3
"""Real restricted daemon/RPC/systemd regression with a Linux launcher fixture.

Only run on the disposable installed Linux CI stack, never create a fake
Windows launcher on a workstation or on WSL.
"""
import json
import os
from pathlib import Path
import pwd
import subprocess
import time

if (os.environ.get('GITHUB_ACTIONS') != 'true' or os.geteuid() != 0
        or 'microsoft' in Path('/proc/sys/kernel/osrelease').read_text().lower()):
    raise SystemExit('Disposable non-WSL GitHub Actions runner required.')

marker = Path('/etc/devstack/windows-local-notifications')
mount = Path('/mnt/c')
launcher = mount / 'DevStack/LocalNotifications/DevStack.Notifications.exe'
observed = launcher.parent / '.ci-observed'
if marker.exists() or marker.is_symlink() or mount.exists() or mount.is_symlink():
    raise SystemExit('Unexpected existing Windows data; refusing fixture.')

cfg = json.loads(Path('/etc/devstack/server.json').read_text())
account = pwd.getpwnam(cfg['dev_user'])
assert account.pw_uid != 0 and account.pw_gid != 0
event = {'id': 'batch-' + 'a' * 32, 'category': 'mail', 'kind': 'info'}
fixture = f"""#!/usr/bin/python3
import base64
import json
import os
from pathlib import Path
import sys
import time

assert os.geteuid() == {account.pw_uid}
assert os.getegid() == {account.pw_gid}
assert len(sys.argv) == 2
assert os.environ['PATH'] == '/usr/bin:/bin'
assert os.environ['LANG'] == 'C.UTF-8'
assert 'WSL_INTEROP' not in os.environ
assert any(line.split() == ['NoNewPrivs:', '1'] for line in Path('/proc/self/status').read_text().splitlines())
data = json.loads(base64.b64decode(sys.argv[1]))
assert data in ({event!r}, {{**{event!r}, 'kind': 'error'}}, {{**{event!r}, 'kind': 'success'}})
with Path({str(observed)!r}).open('a') as f:
    f.write(data['kind'] + '\\n')
# Arbitrary launcher stdout must not turn a failing exit into success.
print('DEVSTACK_NOTIFICATION_ACCEPTED', flush=True)
if data['kind'] == 'error':
    sys.exit(23)
if data['kind'] == 'success':
    time.sleep(20)
    sys.exit(24)
"""

def ctl(*args):
    return subprocess.run(['/usr/bin/systemctl', *args], check=True,
                          capture_output=True, text=True, timeout=30)

def rpc(data):
    wire = json.dumps(data)
    command = [
        '/usr/sbin/runuser', '-u', 'devstackweb', '--', '/usr/bin/php', '-r',
        'require "/opt/devstack/security.php"; echo json_encode(devstack_rpc("show_windows_notification", json_decode($argv[1], true)));',
        wire,
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=25)
    return json.loads(result.stdout)

timer_active = subprocess.run(['/usr/bin/systemctl', 'is-active', '--quiet',
                               'devstack-notifications.timer']).returncode == 0
mount_created = False
marker_created = False
try:
    ctl('stop', 'devstack-notifications.timer', 'devstack-notifications.service')
    ctl('is-active', 'devstackd.service')
    mount.mkdir(mode=0o755)
    mount_created = True
    launcher.parent.mkdir(parents=True, mode=0o755)
    # mktemp-style restrictive umasks must not prevent the developer reading
    # the fixture (directories and code here are root-owned).
    for directory in (mount, mount / 'DevStack', launcher.parent):
        directory.chmod(0o755)
    launcher.write_text(fixture)
    launcher.chmod(0o755)
    observed.touch(mode=0o600, exist_ok=False)
    os.chown(observed, account.pw_uid, account.pw_gid)
    with marker.open('x') as f:
        marker_created = True
        f.write(str(launcher) + '\n')
    marker.chmod(0o644)
    expected = []
    # Repeated fast exits exercise the race that was missed by the mocked
    # unit test. Check independently that the fixture actually ran.
    for _ in range(3):
        assert rpc(event) == {'accepted': True}, 'Actual daemon failed to deliver to the developer-UID fixture.'
        expected.append('info')
        assert rpc({**event, 'kind': 'error'}) == {'accepted': False}, 'Failed worker reported success.'
        expected.append('error')
        assert observed.read_text().splitlines() == expected, 'Fixture not invoked exactly once per request.'
    assert rpc({**event, 'kind': 'success'}) == {'accepted': False}, 'Timed-out worker reported success.'
    expected.append('success')
    assert observed.read_text().splitlines() == expected, 'Timeout fixture did not run.'
    # PID 1 collects inactive transient units asynchronously.
    for _ in range(20):
        units = ctl('list-units', '--all', '--plain', '--no-legend',
                    'devstack-windows-notification-*.service').stdout.strip()
        if not units:
            break
        time.sleep(0.1)
    assert not units, 'Transient notification units were left behind: ' + units
    print('PASS: actual restricted RPC, developer identity, repeated completion, failed output, timeout and unit cleanup.')
except Exception:
    # Independent invocation evidence and PID 1's unit logs are essential:
    # a failure before execution must not be mistaken for a launcher error.
    print('Observed fixture invocations:', observed.read_text() if observed.exists() else '(not created)', flush=True)
    subprocess.run(['/usr/bin/journalctl', '-u', 'devstack-windows-notification-*.service',
                    '--no-pager', '-n', '150'], check=False, timeout=15)
    raise
finally:
    if marker_created:
        marker.unlink()
    if mount_created:
        launcher.unlink(missing_ok=True)
        observed.unlink(missing_ok=True)
        for directory in (launcher.parent, mount / 'DevStack', mount):
            if directory.exists():
                directory.rmdir()
    if timer_active:
        ctl('start', 'devstack-notifications.timer')
