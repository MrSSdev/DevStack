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
assert Path.cwd() == Path({str(launcher.parent)!r})
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


def transport_helper(*args, check=True):
    script = Path(__file__).resolve().parents[1] / 'scripts/enable-windows-local-notifications.sh'
    return subprocess.run(['/usr/bin/bash', str(script), *args], check=check,
                          capture_output=True, text=True, timeout=10)

def compare_workdir_dependencies():
    """Contrast systemd's implicit mount requirements with post-UID chdir.

    Do not edit fstab, mask mounts, or fake a successful fixture run. Some
    Azure runner images have an optional /mnt resource disk configured even
    when no such device is present. The current runner may have a real disk.
    """
    print('Runner filesystem and optional /mnt unit:', flush=True)
    subprocess.run(['/usr/bin/findmnt', '-T', str(launcher.parent),
                    '-o', 'TARGET,SOURCE,FSTYPE'], check=True, timeout=5)
    subprocess.run(['/usr/bin/systemctl', 'show', 'mnt.mount',
                    '-p', 'LoadState', '-p', 'ActiveState', '-p', 'SubState',
                    '-p', 'Result', '-p', 'What', '-p', 'FragmentPath',
                    '-p', 'Requires', '-p', 'After'], check=False, timeout=5)

    def probe(name, working_directory, command):
        unit = 'devstack-ci-notification-workdir-' + name + '.service'
        try:
            result = subprocess.run(
                ['/usr/bin/systemd-run', '--system', '--quiet',
                 '--service-type=oneshot', '--unit=' + unit,
                 '--description=DevStack CI mount dependency probe',
                 '--uid=' + str(account.pw_uid), '--gid=' + str(account.pw_gid),
                 '--working-directory=' + working_directory,
                 '--expand-environment=no', '--property=RemainAfterExit=yes',
                 '--property=TimeoutStartSec=3s',
                 '--property=NoNewPrivileges=yes', '--property=PrivateTmp=yes',
                 '--property=PrivateDevices=yes', '--property=ProtectKernelTunables=yes',
                 '--property=ProtectKernelModules=yes', '--property=ProtectControlGroups=yes',
                 *command],
                capture_output=True, text=True, timeout=10,
            )
            query = subprocess.run(
                ['/usr/bin/systemctl', 'show', '--no-pager',
                 '-p', 'ActiveState', '-p', 'SubState', '-p', 'ExecMainCode',
                 '-p', 'ExecMainStatus', '-p', 'Result', '-p', 'RequiresMountsFor',
                 '-p', 'Requires', '-p', 'After', unit],
                capture_output=True, text=True, timeout=5,
            )
            state = query.stdout
            print(f'Workdir probe {name}: exit={result.returncode}\n{state}', flush=True)
            if result.stderr:
                print(result.stderr, flush=True)
            return (result.returncode == 0 and query.returncode == 0
                    and 'ActiveState=active\n' in state and 'SubState=exited\n' in state
                    and 'ExecMainCode=1\n' in state and 'ExecMainStatus=0\n' in state)
        finally:
            for action in ('stop', 'reset-failed'):
                subprocess.run(['/usr/bin/systemctl', action, unit], capture_output=True, timeout=5)

    original = probe('original', str(launcher.parent), ['/usr/bin/test', '-x', str(launcher)])
    revised = probe('revised', '/', ['/usr/bin/env', '-i', '--chdir=' + str(launcher.parent),
                                     'PATH=/usr/bin:/bin', '/usr/bin/test', '-x', str(launcher)])
    print(f'Workdir comparison: original={original} revised={revised}', flush=True)
    if not original:
        subprocess.run(['/usr/bin/journalctl', '-b', '-u', 'mnt.mount',
                        '-u', 'devstack-ci-notification-workdir-original.service',
                        '--no-pager', '-n', '40'], check=False, timeout=10)
    assert revised, 'Revised workdir could not access the actual launcher path.'


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
    assert transport_helper('--status').stdout.strip() == 'disabled'
    assert not marker.exists() and not marker.is_symlink(), 'Status unexpectedly opted in.'
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
    # The actual privileged helper must distinguish absent, enabled and
    # unsafe markers, and must never reset the original opt-in cutoff.
    os.utime(marker, (1_700_000_000, 1_700_000_000))
    before = marker.stat().st_mtime_ns
    assert transport_helper('--status').stdout.strip() == 'enabled'
    assert marker.read_text() == str(launcher) + '\n'
    assert marker.stat().st_mtime_ns == before
    # Existing consent also survives a missing Windows binary.
    launcher.rename(launcher.with_suffix('.fixture'))
    try:
        assert transport_helper('--status').stdout.strip() == 'enabled'
    finally:
        launcher.with_suffix('.fixture').rename(launcher)
    marker.chmod(0o666)
    assert transport_helper('--status', check=False).returncode != 0
    assert transport_helper(check=False).returncode != 0
    marker.chmod(0o644)
    marker.unlink()
    marker.symlink_to(launcher)
    assert transport_helper('--status', check=False).returncode != 0
    assert transport_helper(check=False).returncode != 0
    marker.unlink()
    marker.write_text('/mnt/c/Users/Fixture/AppData/Local/DevStack/LocalNotifications/DevStack.Notifications.exe\n')
    marker.chmod(0o644)
    os.utime(marker, (1_700_000_000, 1_700_000_000))
    assert transport_helper('--status').stdout.strip() == 'enabled'
    transport_helper()
    assert marker.read_text() == str(launcher) + '\n'
    assert marker.stat().st_mtime_ns == before
    transport_helper()
    assert marker.stat().st_mtime_ns == before
    print('PASS: read-only opt-in detection, missing launcher, unsafe markers, migration and idempotent enable.')
    compare_workdir_dependencies()
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
