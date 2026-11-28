"""Controlled payload, executed as the session identity inside Bubblewrap."""
import ctypes
import errno
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import time

ROOT = Path('/workspace')


def call(*argv):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=25)
    assert not result.returncode, (argv, result.returncode, result.stderr[-1200:])
    return result.stdout


def denied(action, errors):
    try:
        action()
    except OSError as exc:
        assert exc.errno in errors, (errors, exc)
    else:
        raise AssertionError('Operation unexpectedly succeeded')


def boundary(host_home, sibling, admin_socket, expected):
    leaked = []
    for name in os.listdir('/proc/self/fd'):
        if int(name) > 2:
            try:
                # An FD that crossed exec must be inheritable. The system
                # interpreter can open its own CLOEXEC FDs during startup.
                if os.get_inheritable(int(name)):
                    leaked.append((name, os.readlink('/proc/self/fd/' + name)))
            except OSError as exc:
                assert exc.errno == errno.EBADF
    assert not leaked, ('unexpected inherited descriptors', leaked)
    assert not (set(os.environ) & {'DEVSTACK_TEST_SECRET', 'LISTEN_FDS', 'SSH_AUTH_SOCK',
                                  'XDG_RUNTIME_DIR', 'WSL_INTEROP', 'WSLENV'})
    assert os.read(0, 1) == b''
    assert all(stat.S_ISREG(os.fstat(fd).st_mode) for fd in (1, 2))
    assert os.getuid() == os.stat(ROOT).st_uid == os.geteuid() != 0
    assert os.getgid() == os.stat(ROOT).st_gid == os.getegid() != 0
    assert not os.getgroups()
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines()
                  if ':' in line)
    assert not any(int(status[name], 16) for name in ('CapInh', 'CapPrm', 'CapEff', 'CapAmb'))
    assert status['NoNewPrivs'].strip() == '1'
    assert status['Seccomp'].strip() == '2' and int(status['Seccomp_filters']) >= 2
    assert os.statvfs(ROOT).f_flag & (os.ST_NODEV | os.ST_NOSUID | os.ST_NOEXEC) == (
        os.ST_NODEV | os.ST_NOSUID | os.ST_NOEXEC)
    assert Path.cwd() == ROOT and (ROOT / 'source.txt').read_text() == expected
    for path in (host_home, sibling, '/home/ubuntu', '/root', '/mnt/c', '/init', '/run/WSL', '/sys',
                 '/run/systemd/private', '/run/docker.sock', '/run/devstack',
                 '/etc/shadow', '/usr/local'):
        assert not Path(path).exists(), path
    assert not list(Path('/usr/lib/wsl').glob('*'))
    denied(lambda: (Path(sibling) / 'source.txt').read_bytes(), {errno.ENOENT, errno.EACCES})
    denied(lambda: (Path(sibling) / 'created').write_bytes(b'no'), {errno.ENOENT, errno.EACCES})
    with socket.socket(socket.AF_UNIX) as client:
        denied(lambda: client.connect(admin_socket), {errno.ENOENT, errno.ECONNREFUSED})
        denied(lambda: client.connect('\0' + admin_socket), {errno.ECONNREFUSED})
        denied(lambda: client.bind('/tmp/named-socket'), {errno.EACCES})
    for family in (socket.AF_INET, socket.AF_INET6, socket.AF_NETLINK,
                   socket.AF_PACKET, socket.AF_VSOCK):
        denied(lambda: socket.socket(family), {errno.EAFNOSUPPORT})
    libc = ctypes.CDLL(None, use_errno=True)
    assert libc.unshare(0x10000000) == -1  # CLONE_NEWUSER
    assert ctypes.get_errno() in (errno.EPERM, errno.ENOSPC, errno.EUSERS)
    # Same architecture-independent libc interfaces used by the workloads.
    assert libc.ptrace(0, 0, 0, 0) == -1 and ctypes.get_errno() == errno.ENOSYS
    denied(lambda: os.setuid(0), {errno.EPERM, errno.EINVAL})
    # Some distributions expose sudo via a host alternatives symlink.
    # Check that the alias or any visible packaged implementation denies
    # escalation with the sandbox's capabilities, mounts and policy.
    for binary in ('/usr/bin/sudo', '/usr/bin/sudo-rs', '/usr/bin/sudo.ws'):
        try:
            result = subprocess.run([binary, '-n', '/usr/bin/id'], capture_output=True,
                                    timeout=5)
        except OSError as exc:
            assert exc.errno in (errno.ENOENT, errno.EACCES)
        else:
            assert result.returncode
    home = Path.home()
    assert home == Path('/tmp/home') and not list(home.iterdir())
    assert not list(Path('/tmp/cache').iterdir())
    (home / 'session').write_text(expected)
    namespaces = {name: os.readlink('/proc/self/ns/' + name)
                  for name in ('mnt', 'user', 'pid', 'net', 'ipc', 'uts')}
    print(json.dumps({'uid': os.getuid(), 'gid': os.getgid(), 'namespaces': namespaces,
                      'home_contents': (home / 'session').read_text()}), flush=True)


def git_proof(changed):
    status = call('/usr/bin/git', 'status', '--porcelain', '--untracked-files=no')
    diff = call('/usr/bin/git', 'diff', '--no-ext-diff', '--no-textconv', '--', 'source.txt')
    assert bool(status.strip()) == bool(diff.strip()) == changed
    denied(lambda: Path('/run/git/config').write_text('bad'), {errno.EROFS, errno.EACCES})


def write_project():
    (ROOT / 'source.txt').write_text('sandbox edit\n')
    (ROOT / 'created').write_text('sandbox data\n')
    # Neither a shebang nor an ELF in the mapped checkout can be execve'd.
    for executable in ('./script.sh', './native'):
        denied(lambda: subprocess.run([executable], check=True), {errno.EACCES})
    assert call('/usr/bin/sh', './script.sh').strip() == 'interpreted'
    (ROOT / 'outside').symlink_to('/mnt/c/Windows')
    try:
        denied(lambda: (ROOT / 'outside').read_text(), {errno.ENOENT})
    finally:
        (ROOT / 'outside').unlink()
    denied(lambda: os.link('/usr/bin/sh', ROOT / 'outside'), {errno.EXDEV})
    assert 'sqlite:1' in call('/usr/bin/php8.5', 'probe.php')
    git_proof(changed=True)


def write_tools():
    assert 'node:local' in call('/usr/bin/npm', 'run', 'proof')
    assert 'Composer version' in call('/usr/bin/composer', '--version')
    assert 'composer:local' in call('/usr/bin/php8.5', 'composer-proof.php')


def main():
    mode, host_home, sibling, admin_socket, expected = sys.argv[1:]
    boundary(host_home, sibling, admin_socket, expected)
    if mode == 'read':
        assert os.statvfs(ROOT).f_flag & os.ST_RDONLY
        denied(lambda: (ROOT / 'source.txt').write_text('bad'), {errno.EROFS})
        denied(lambda: (ROOT / 'created').touch(), {errno.EROFS})
        git_proof(changed=False)
    elif mode == 'write':
        assert not os.statvfs(ROOT).f_flag & os.ST_RDONLY
        git_proof(changed=False)
        write_project()
        write_tools()
    elif mode == 'write-second':
        assert not os.statvfs(ROOT).f_flag & os.ST_RDONLY
        git_proof(changed=False)
        write_project()
        if (ROOT / 'artisan').exists():
            assert 'Laravel Framework' in call('/usr/bin/php8.5', 'artisan', '--version')
            print('PASS: actual Laravel Artisan and preinstalled vendor tree', flush=True)
    elif mode in ('hold', 'crash'):
        # A real orphan candidate with its own session and no stdio pipes.
        subprocess.Popen(['/usr/bin/sh', '-c',
                          'touch /tmp/child-ready; exec sleep 120'],
                         start_new_session=True)
        while not Path('/tmp/child-ready').exists():
            time.sleep(0.02)
        print('child-ready', flush=True)
        marker = ROOT / 'finish'
        while not marker.exists():
            time.sleep(0.05)
        assert (ROOT / 'source.txt').read_text() == expected
        if mode == 'crash':
            os._exit(17)
    else:
        raise AssertionError('Unexpected proof mode')
    print('PASS: ' + mode, flush=True)


if __name__ == '__main__':
    main()
