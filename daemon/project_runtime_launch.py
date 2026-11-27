#!/usr/bin/env python3
"""Fixed, non-root launcher for protected CI fixture runtimes only."""
from __future__ import annotations

import os
from pathlib import Path
import pwd
import re
import stat
import sys


def main():
    if len(sys.argv) != 3 or not sys.flags.isolated or os.geteuid() == 0:
        raise ValueError('Expected fixed non-root fixture launcher')
    kind, raw = sys.argv[1:]
    project = Path(raw)
    if (kind not in ('php', 'http') or project.parent.parent != Path('/var/lib')
            or not re.fullmatch(r'devstack-runtime-probe-[a-zA-Z0-9_]{8}', project.parent.name)
            or project.name not in ('project-a', 'project-b')):
        raise ValueError('Expected protected disposable fixture')
    slot = project.name[-1]
    account = pwd.getpwuid(os.geteuid())
    if (not re.fullmatch(r'dsrt-' + slot + r'-[a-f0-9]{10}', account.pw_name)
            or account.pw_shell != '/usr/sbin/nologin'
            or account.pw_gid != os.getegid()
            or set(os.getgroups()) - {account.pw_gid}):
        raise ValueError('Unexpected runtime identity')
    base = project.parent
    info = base.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
            or info.st_mode & 0o022):
        raise ValueError('Unprotected fixture base')
    launcher = base / 'launcher'
    if Path(__file__).absolute() != launcher / 'project_runtime_launch.py':
        raise ValueError('Expected staged fixture launcher')
    info = launcher.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('Unprotected launcher directory')
    for name in ('project_runtime_launch.py', 'project_runtime.py',
                 'project_http.py', 'project_mounts.py'):
        info = (launcher / name).lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Unprotected launcher file')
    sys.path.insert(0, str(launcher))
    from project_runtime import RuntimePlan
    from project_http import ProjectHttp
    from project_mounts import PinnedProject, exec_pinned

    config = base / ('config-' + slot)
    runtime = RuntimePlan(project, config / 'fpm.conf', base / ('sockets-' + slot))
    plan = (runtime if kind == 'php' else
            ProjectHttp(runtime, config / 'nginx.conf', base / ('http-' + slot)))
    with PinnedProject(project) as mounts:
        args = plan.argv(mounts)
        exec_pinned(args, mounts)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        # Only disposable fixture paths; avoid arbitrary subprocess diagnostics.
        print('Fixture source launch refused: ' + type(exc).__name__, file=sys.stderr)
        raise SystemExit(2)
