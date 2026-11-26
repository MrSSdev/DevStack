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
import sys

if (os.environ.get('GITHUB_ACTIONS') != 'true' or os.geteuid() != 0
        or 'microsoft' in Path('/proc/sys/kernel/osrelease').read_text().lower()):
    raise SystemExit('Disposable non-WSL GitHub Actions runner required.')

marker = Path('/etc/devstack/windows-local-notifications')
mount = Path('/mnt/c')
launcher = mount / 'DevStack/LocalNotifications/DevStack.Notifications.exe'
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

assert os.geteuid() == {account.pw_uid}
assert os.getegid() == {account.pw_gid}
assert len(sys.argv) == 2
assert os.environ['PATH'] == '/usr/bin:/bin'
assert os.environ['LANG'] == 'C.UTF-8'
assert 'WSL_INTEROP' not in os.environ
assert any(line.split() == ['NoNewPrivs:', '1'] for line in Path('/proc/self/status').read_text().splitlines())
assert json.loads(base64.b64decode(sys.argv[1])) == {event!r}
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
    with marker.open('x') as f:
        marker_created = True
        f.write(str(launcher) + '\n')
    marker.chmod(0o644)
    assert rpc(event) == {'accepted': True}, 'Actual daemon failed to deliver to the developer-UID fixture.'
    # A valid different event must not be reported as delivered when the
    # fixture exits nonzero. No retry/fallback may disguise this error.
    assert rpc({**event, 'kind': 'error'}) == {'accepted': False}, 'Failed worker reported success.'
    print('PASS: actual restricted daemon RPC, transient unit developer identity, bounded typed payload, and nonzero exit handling.')
finally:
    if marker_created:
        marker.unlink()
    if mount_created:
        launcher.unlink(missing_ok=True)
        for directory in (launcher.parent, mount / 'DevStack', mount):
            if directory.exists():
                directory.rmdir()
    if timer_active:
        ctl('start', 'devstack-notifications.timer')
