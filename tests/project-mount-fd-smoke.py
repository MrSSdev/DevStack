#!/usr/bin/env python3
"""Real bwrap fixture proving an FD-selected source is not reopened by name."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'daemon'))
from project_mounts import PinnedProject


def main():
    if os.geteuid() == 0 or len(sys.argv) != 1:
        raise SystemExit('Run the disposable fixture as an ordinary user')
    with tempfile.TemporaryDirectory(prefix='devstack-mount-fd-') as name:
        base = Path(name)
        project = base / 'project'
        project.mkdir()
        storage = project / 'storage'
        storage.mkdir()
        (storage / 'marker').write_text('own')
        other = base / 'other'
        other.mkdir()
        (other / 'marker').write_text('other')
        with PinnedProject(project) as mounts:
            args = mounts.bind('storage', writable=True)
            storage.rename(project / 'old-storage')
            storage.symlink_to(other, target_is_directory=True)
            # Fixed destination /work: never a user-controlled mount target.
            args[-1] = '/work'
            subprocess.run([
                '/usr/bin/bwrap', '--unshare-user', '--unshare-pid',
                '--unshare-net', '--unshare-ipc', '--unshare-uts',
                '--disable-userns', '--die-with-parent', '--new-session',
                '--cap-drop', 'ALL', '--ro-bind', '/usr', '/usr',
                '--symlink', 'usr/bin', '/bin', '--symlink', 'usr/lib', '/lib',
                '--symlink', 'usr/lib64', '/lib64', '--proc', '/proc',
                '--dev', '/dev', '--tmpfs', '/tmp', *args, '--chdir', '/work',
                '--clearenv', '--setenv', 'PATH', '/usr/bin:/bin',
                '--', '/usr/bin/python3', '-I', '-c', '''
import os
import stat
from pathlib import Path
assert Path('/work/marker').read_text() == 'own'
Path('/work/result').write_text('ok')
for name in os.listdir('/proc/self/fd'):
    if int(name) < 3:
        continue
    try:
        target = os.readlink('/proc/self/fd/' + name)
        mode = os.fstat(int(name)).st_mode
    except (FileNotFoundError, OSError):
        continue
    assert not stat.S_ISDIR(mode), 'directory descriptor leaked'
    assert 'devstack-mount-fd-' not in target, 'source descriptor leaked'
'''], pass_fds=tuple(mounts.fds), check=True, timeout=20)
        assert (project / 'old-storage/result').read_text() == 'ok'
        assert (other / 'marker').read_text() == 'other'
        assert not (other / 'result').exists()
    print('PASS: FD-pinned mount survives pathname replacement; no leaked source descriptor')


if __name__ == '__main__':
    main()
