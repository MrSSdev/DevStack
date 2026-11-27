#!/usr/bin/env python3
"""Exercise idmapped writable data in a disposable private mount namespace.

Called only from the existing runtime CI fixture; no installed projects,
global mount table, developer account, persistent mounts or ACL migration
are changed. This is a feasibility test, not a production mount broker.
"""
from __future__ import annotations

import os
from pathlib import Path
import pwd
import re
import sqlite3
import stat
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'daemon'))
from project_runtime_identity import verify_binding
from project_idmap import IdMapping, IdmapError, publish_idmapped
from project_mounts import DIR_FLAGS

ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C'}


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, timeout=30, env=ENV, **kwargs)


def as_user(account, *args, **kwargs):
    return run('/usr/bin/setpriv', '--reuid=' + str(account.pw_uid),
               '--regid=' + str(account.pw_gid), '--init-groups',
               '--no-new-privs', '--', *args, **kwargs)


def owner(path):
    info = path.stat()
    return info.st_uid, info.st_gid


def deny_read(account, path):
    as_user(account, '/usr/bin/python3', '-I', '-c', '''
import sys
try:
    with open(sys.argv[1]) as stream:
        stream.read()
except PermissionError:
    pass
else:
    raise AssertionError('Unexpected access outside authorized mount')
''', str(path))


# The actual runtime opens the protected mount publication under its own
# credentials, and hands the source descriptor to Bubblewrap, as in the
# existing runtime launcher. No arbitrary command is exposed to a caller.
LAUNCH = r'''
import os, subprocess, sys
view, code, program = sys.argv[1:]
flags = os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
data_fd = os.open(view, flags)
code_fd = os.open(code, flags)
try:
    subprocess.run([
        '/usr/bin/bwrap', '--unshare-user', '--unshare-pid',
        '--unshare-net', '--unshare-ipc', '--unshare-uts',
        '--disable-userns', '--die-with-parent', '--new-session',
        '--cap-drop', 'ALL', '--ro-bind', '/usr', '/usr',
        '--symlink', 'usr/bin', '/bin', '--symlink', 'usr/lib', '/lib',
        '--symlink', 'usr/lib64', '/lib64', '--proc', '/proc',
        '--dev', '/dev', '--tmpfs', '/tmp', '--dir', '/home',
        '--dir', '/mnt', '--dir', '/run', '--dir', '/work',
        '--ro-bind-fd', str(code_fd), '/work/code',
        '--bind-fd', str(data_fd), '/work/data',
        '--chdir', '/work', '--clearenv',
        '--setenv', 'PATH', '/usr/bin:/bin', '--',
        '/usr/bin/php8.5', '-n', '-d', 'extension=pdo',
        '-d', 'extension=pdo_sqlite', '-r', program,
    ], pass_fds=(data_fd, code_fd), check=True, timeout=20)
finally:
    os.close(data_fd)
    os.close(code_fd)
'''

PHP_CREATE = r'''
function need($ok, $what) {
    if (!$ok) { throw new RuntimeException($what); }
}
need(file_get_contents('/work/code/marker') === 'code', 'read-only code');
need(@file_put_contents('/work/code/marker', 'bad') === false, 'source write');
need(file_get_contents('/work/data/seed') === 'developer', 'seed access');
need(file_put_contents('/work/data/seed', '-runtime', FILE_APPEND) === 8, 'seed write');
need(!file_exists('/mnt/c') && !file_exists('/run/devstack'), 'host visibility');
need(count(scandir('/home')) === 2, 'home visibility');
$db = new PDO('sqlite:/work/data/runtime.sqlite');
$db->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
$db->exec('CREATE TABLE proof (value TEXT NOT NULL)');
$db->exec("INSERT INTO proof VALUES ('runtime')");
need(file_put_contents('/work/data/owner-only', 'runtime') === 7, 'owner file');
need(chmod('/work/data/owner-only', 0600), 'owner mode');
need((fileperms('/work/data/owner-only') & 0777) === 0600, 'owner mode retained');
echo "PASS: PHP creates SQLite and owner-only shared data in Bubblewrap\n";
'''

PHP_VERIFY = r'''
$db = new PDO('sqlite:/work/data/runtime.sqlite');
$rows = $db->query('SELECT value FROM proof ORDER BY rowid')->fetchAll(PDO::FETCH_COLUMN);
if ($rows !== ['runtime','developer'] ||
    file_get_contents('/work/data/owner-only') !== 'runtime-developer') {
    throw new RuntimeException('Developer changes not visible to runtime');
}
echo "PASS: PHP observes developer transaction and file edit\n";
'''


def php(account, view, code, program):
    as_user(account, '/usr/bin/python3', '-I', '-c', LAUNCH,
            str(view), str(code), program)


def main():
    if (os.geteuid() != 0 or not sys.flags.isolated
            or os.getenv('GITHUB_ACTIONS') != 'true'
            or os.getenv('DEVSTACK_RUNTIME_CI') != '1' or len(sys.argv) != 4):
        raise SystemExit('Only for the disposable CI mount namespace')
    base = Path(sys.argv[1])
    developer = pwd.getpwnam(sys.argv[2])
    if (not re.fullmatch(r'dsrt-dev-[a-f0-9]{10}', developer.pw_name)
            or developer.pw_uid == 0 or developer.pw_gid == 0
            or developer.pw_shell != '/usr/sbin/nologin'
            or developer.pw_dir != '/nonexistent'
            or set(os.getgrouplist(developer.pw_name, developer.pw_gid)) != {developer.pw_gid}):
        raise AssertionError('Expected disposable developer')
    if str(os.stat('/proc/self/ns/mnt').st_ino) == sys.argv[3]:
        raise AssertionError('Mount namespace was not separated')
    # No shared/slave propagation into the host. Require a private namespace,
    # never mount over a live project even if the kernel supports ID mapping.
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        optional = line.split(' - ', 1)[0].split()[6:]
        if any(x.startswith(('shared:', 'master:', 'propagate_from:')) for x in optional):
            raise AssertionError('Mount namespace is not private')
    records = [verify_binding(base, slot) for slot in ('a', 'b')]
    accounts = [pwd.getpwnam(record['account']) for record in records]
    assert len({developer.pw_uid, *(x.pw_uid for x in accounts)}) == 3
    assert len({developer.pw_gid, *(x.pw_gid for x in accounts)}) == 3
    work = base / 'idmap-probe'
    work.mkdir(mode=0o711)
    code = work / 'code'
    code.mkdir(mode=0o755)
    marker = code / 'marker'
    marker.write_text('code')
    marker.chmod(0o644)
    sources, views = [], []
    try:
        for index, account in enumerate(accounts):
            source = work / ('data-' + str(index))
            view = work / ('view-' + str(index))
            source.mkdir(mode=0o700)
            seed = source / 'seed'
            seed.write_text('developer')
            seed.chmod(0o600)
            os.chown(seed, developer.pw_uid, developer.pw_gid)
            os.chown(source, developer.pw_uid, developer.pw_gid)
            view.mkdir(mode=0o700)
            sources.append(source)
            views.append(view)
            # Pin the source and publication target before constructing the
            # detached tree. Never pass a path to the privileged mount API.
            mapping = IdMapping(developer.pw_uid, developer.pw_gid,
                                account.pw_uid, account.pw_gid)
            source_fd = os.open(source, DIR_FLAGS)
            view_fd = os.open(view, DIR_FLAGS)
            try:
                # A nonempty root-owned target is not a publication slot.
                if index == 0:
                    (view / 'unexpected').touch()
                    try:
                        try:
                            publish_idmapped(source_fd, view_fd, mapping)
                        except IdmapError:
                            pass
                        else:
                            raise AssertionError('Nonempty publication accepted')
                    finally:
                        (view / 'unexpected').unlink()
                    wrong = IdMapping(account.pw_uid, account.pw_gid,
                                      developer.pw_uid, developer.pw_gid)
                    try:
                        publish_idmapped(source_fd, view_fd, wrong)
                    except IdmapError:
                        pass
                    else:
                        raise AssertionError('Wrong source owner accepted')
                    # Replace the source pathname after descriptor selection.
                    # The detached mount must still refer to the original
                    # inode, not to the unrelated directory behind a symlink.
                    moved = work / 'pinned-original'
                    source.rename(moved)
                    source.symlink_to(code, target_is_directory=True)
                    try:
                        publish_idmapped(source_fd, view_fd, mapping)
                        assert (view / 'seed').read_text() == 'developer'
                        assert not (view / 'marker').exists()
                    finally:
                        source.unlink()
                        moved.rename(source)
                    print('PASS: FD publication rejects wrong owner/nonempty target and survives source replacement', flush=True)
                else:
                    publish_idmapped(source_fd, view_fd, mapping)
            finally:
                os.close(view_fd)
                os.close(source_fd)
            assert owner(source) == (developer.pw_uid, developer.pw_gid)
            assert owner(view) == (account.pw_uid, account.pw_gid)
            assert os.path.samestat(source.stat(), view.stat())
            assert owner(view / 'seed') == (account.pw_uid, account.pw_gid)
            flags = os.statvfs(view).f_flag
            assert flags & os.ST_NOSUID and flags & os.ST_NODEV and flags & os.ST_NOEXEC
            deny_read(account, source / 'seed')
            print('PASS: Exact per-project mapped owner, original owner unchanged', index, flush=True)

        for index, account in enumerate(accounts):
            source, view = sources[index], views[index]
            deny_read(account, views[1-index] / 'seed')
            php(account, view, code, PHP_CREATE)
            for name in ('seed', 'runtime.sqlite', 'owner-only'):
                assert owner(source / name) == (developer.pw_uid, developer.pw_gid), name
                assert owner(view / name) == (account.pw_uid, account.pw_gid), name
            assert stat.S_IMODE((source / 'owner-only').stat().st_mode) == 0o600
            assert (source / 'seed').read_text() == 'developer-runtime'
            deny_read(accounts[1-index], view / 'owner-only')
            as_user(developer, '/usr/bin/python3', '-I', '-c', '''
from pathlib import Path
import sqlite3, sys
root = Path(sys.argv[1])
with sqlite3.connect(root / 'runtime.sqlite') as db:
    db.execute("INSERT INTO proof VALUES ('developer')")
with (root / 'owner-only').open('a') as stream:
    stream.write('-developer')
''', str(source))
            php(account, view, code, PHP_VERIFY)
            print('PASS: Ordinary developer writes runtime-created SQLite and mode 0600 shared data without ACL repair', index, flush=True)
        assert marker.read_text() == 'code'
        print('PASS: Sibling runtime denied, code unchanged, developer groups unchanged', flush=True)
    finally:
        # Only known root-created destinations in this private namespace.
        # Also handle mount(8) failing after a mount was attached.
        for view in reversed(views):
            result = subprocess.run(['/usr/bin/mountpoint', '-q', '--', str(view)],
                                    env=ENV, timeout=5, check=False)
            if result.returncode == 0:
                run('/usr/bin/umount', '-n', '--', str(view))
            elif result.returncode != 32:
                raise RuntimeError('Cannot confirm mountpoint state')

    for source, view in zip(sources, views):
        assert not any(view.iterdir())
        assert owner(source / 'runtime.sqlite') == (developer.pw_uid, developer.pw_gid)
        with sqlite3.connect((source / 'runtime.sqlite').as_uri()+'?mode=ro', uri=True) as db:
            assert db.execute('SELECT value FROM proof ORDER BY rowid').fetchall() == [
                ('runtime',), ('developer',)]
    assert set(os.getgrouplist(developer.pw_name, developer.pw_gid)) == {developer.pw_gid}
    print('=== IDMAPPED DATA PROBE PASSED; private mounts removed ===', flush=True)


if __name__ == '__main__':
    main()
