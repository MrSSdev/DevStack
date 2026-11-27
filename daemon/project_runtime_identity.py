"""Runtime account allocation and verification for disposable CI fixtures.

The registration identifier is independent of model, agent, slug and UID.
The protected record is an allocation journal, not a permission grant.
No production enrollment, account adoption, automatic removal or UID
recycling is implemented here.
"""
from __future__ import annotations

import grp
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import tempfile

PROJECT_ID = re.compile(r'[0-9a-f]{32}\Z')
BASE = re.compile(r'devstack-runtime-probe-[a-zA-Z0-9_]{8}\Z')
USER = re.compile(r'dsrt-([ab])-[a-f0-9]{24}\Z')
HOME = '/nonexistent'
SHELL = '/usr/sbin/nologin'
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C'}


class IdentityError(RuntimeError):
    pass


def account_name(slot, project_id):
    if slot not in ('a', 'b') or not isinstance(project_id, str) or not PROJECT_ID.fullmatch(project_id):
        raise IdentityError('Invalid registration identity')
    # 31 bytes, within Linux's login-name limit. The record and account
    # comment retain the full ID. Never adopt a truncated-name collision.
    return 'dsrt-' + slot + '-' + project_id[:24]


def comment(project_id):
    return 'DevStack fixture ' + project_id


def validate_record(value, slot):
    if (not isinstance(value, dict) or type(value.get('schema')) is not int
            or value['schema'] != 1 or value.get('slot') != slot):
        raise IdentityError('Invalid runtime identity record')
    project_id = value.get('project_id')
    if value.get('account') != account_name(slot, project_id):
        raise IdentityError('Runtime registration mismatch')
    keys = {'schema', 'slot', 'project_id', 'account', 'state'}
    if value.get('state') == 'ready':
        keys.update(('uid', 'gid'))
        for field in ('uid', 'gid'):
            if type(value.get(field)) is not int or not 0 < value[field] < 2**32 - 1:
                raise IdentityError('Invalid numeric runtime identity')
    elif value.get('state') != 'pending':
        raise IdentityError('Invalid allocation state')
    if set(value) != keys:
        raise IdentityError('Unexpected allocation fields')
    return value


def record_path(base, slot):
    if (slot not in ('a', 'b') or base.parent != Path('/var/lib')
            or not BASE.fullmatch(base.name)):
        raise IdentityError('Expected protected disposable fixture')
    for path in (Path('/'), Path('/var'), Path('/var/lib'), base):
        s = path.lstat()
        if not stat.S_ISDIR(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022:
            raise IdentityError('Unprotected allocation directory')
    return base / ('identity-' + slot + '.json')


def read_record(base, slot):
    path = record_path(base, slot)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as stream:
        s = os.fstat(stream.fileno())
        if (not stat.S_ISREG(s.st_mode) or s.st_uid != 0 or s.st_nlink != 1
                or stat.S_IMODE(s.st_mode) != 0o644 or s.st_size > 2048):
            raise IdentityError('Unprotected identity record')
        raw = stream.read(2049)
    if len(raw) > 2048:
        raise IdentityError('Oversized identity record')
    try:
        return validate_record(json.loads(raw), slot)
    except (ValueError, RecursionError):
        raise IdentityError('Unreadable identity record') from None


def verify_account(record, *, password_lock=False):
    if record.get('state') != 'ready':
        raise IdentityError('Allocation is incomplete; administrator review required')
    name, uid, gid = (record[k] for k in ('account', 'uid', 'gid'))
    try:
        account = pwd.getpwnam(name)
        group = grp.getgrnam(name)
        if (account.pw_uid != uid or account.pw_gid != gid
                or account.pw_dir != HOME or account.pw_shell != SHELL
                or account.pw_gecos != comment(record['project_id'])
                or pwd.getpwuid(uid).pw_name != name
                or group.gr_gid != gid or grp.getgrgid(gid).gr_name != name
                or set(os.getgrouplist(name, gid)) != {gid}):
            raise IdentityError('Runtime account or group changed')
    except KeyError:
        raise IdentityError('Bound runtime account or group missing') from None
    if password_lock:
        # Never log or persist the shadow field. A locked password is an
        # additional account invariant, not the sandbox itself.
        result = subprocess.run(['/usr/bin/getent', 'shadow', name],
                                env=ENV, capture_output=True, text=True,
                                timeout=5, check=False)
        lines = result.stdout.splitlines()
        fields = lines[0].split(':') if len(lines) == 1 else []
        if (result.returncode != 0 or len(fields) != 9 or fields[0] != name
                or not fields[1].startswith(('!', '*'))):
            raise IdentityError('Runtime password is not confirmed locked')
    return account


def verify_binding(base, slot, expected_user=None, *, current=False):
    record = read_record(base, slot)
    if expected_user is not None and record['account'] != expected_user:
        raise IdentityError('Unexpected project runtime account')
    account = verify_account(record, password_lock=os.geteuid() == 0)
    if current and (os.geteuid() != account.pw_uid or os.getegid() != account.pw_gid
                    or set(os.getgroups()) - {account.pw_gid}):
        raise IdentityError('Unexpected execution credentials')
    return record


def write_record(fd, record):
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        os.fchmod(stream.fileno(), 0o644)
        json.dump(record, stream, sort_keys=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def sync_directory(base):
    fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def exists(lookup, name):
    try:
        lookup(name)
    except KeyError:
        return False
    return True


def provision_fixture(base, slot, project_id):
    """Allocate once on the disposable runner. Leave incomplete journals blocked.

    A crash or account collision must not cause adoption of an existing
    principal. Production recovery and retirement need their own transaction.
    """
    if (os.geteuid() != 0 or os.getenv('GITHUB_ACTIONS') != 'true'
            or os.getenv('DEVSTACK_RUNTIME_CI') != '1'):
        raise IdentityError('Disposable CI administrator required')
    path = record_path(base, slot)
    name = account_name(slot, project_id)
    if exists(pwd.getpwnam, name) or exists(grp.getgrnam, name):
        raise IdentityError('Runtime name already allocated')
    pending = {'schema': 1, 'slot': slot, 'project_id': project_id,
               'account': name, 'state': 'pending'}
    # Reserve the slot before useradd. O_EXCL also refuses dangling symlinks.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                 | os.O_CLOEXEC, 0o600)
    write_record(fd, pending)
    sync_directory(base)
    result = subprocess.run(['/usr/sbin/useradd', '--system', '--user-group',
                             '--no-create-home', '--home-dir', HOME,
                             '--shell', SHELL, '--comment', comment(project_id),
                             '--', name], env=ENV, capture_output=True,
                            text=True, timeout=15, check=False)
    if result.returncode:
        raise IdentityError('Runtime allocation failed; journal retained')
    account = pwd.getpwnam(name)
    ready = {**pending, 'state': 'ready', 'uid': account.pw_uid, 'gid': account.pw_gid}
    validate_record(ready, slot)
    verify_account(ready, password_lock=True)
    if read_record(base, slot) != pending:
        raise IdentityError('Allocation journal changed')
    fd, temporary = tempfile.mkstemp(prefix='.identity-', dir=base)
    try:
        write_record(fd, ready)
        os.replace(temporary, path)
        sync_directory(base)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return verify_binding(base, slot)
