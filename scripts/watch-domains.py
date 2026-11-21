#!/usr/bin/env python3
"""Stream only a constant SYNC marker when the root-owned project state changes.

For the Windows DevStack hosts bridge. Uses Linux inotify (not polling), no
third-party dependencies, network listener, secret output or Windows interop.
The Windows side re-reads and validates the current domain list on each event.
"""
from __future__ import annotations

import ctypes
import os
import pathlib
import struct
import sys

STATE_DIR = pathlib.Path('/var/lib/devstack')
STATE_NAME = b'projects.json'
IN_CLOSE_WRITE = 0x00000008
IN_MOVED_TO = 0x00000080
IN_CREATE = 0x00000100
IN_DELETE = 0x00000200
IN_IGNORED = 0x00008000
WATCH_MASK = IN_CLOSE_WRITE | IN_MOVED_TO | IN_CREATE | IN_DELETE
HEADER = struct.Struct('iIII')


def parse_change_events(data: bytes) -> tuple[bool, bool]:
    """Return (project_state_changed, watch_removed) for complete inotify data."""
    offset = 0
    changed = removed = False
    while offset + HEADER.size <= len(data):
        _, mask, _, size = HEADER.unpack_from(data, offset)
        offset += HEADER.size
        if size > len(data) - offset:
            raise ValueError('Truncated inotify event.')
        name = data[offset:offset + size].split(b'\0', 1)[0]
        offset += size
        removed |= bool(mask & IN_IGNORED)
        changed |= name == STATE_NAME and bool(mask & WATCH_MASK)
    if offset != len(data):
        raise ValueError('Incomplete inotify data.')
    return changed, removed


def watch() -> None:
    if os.geteuid() != 0:
        raise PermissionError('The event watcher requires WSL root (read-only).')
    if not STATE_DIR.is_dir() or STATE_DIR.is_symlink():
        raise RuntimeError('DevStack state directory is missing or unsafe.')

    libc = ctypes.CDLL(None, use_errno=True)
    init = libc.inotify_init1
    init.argtypes = [ctypes.c_int]
    init.restype = ctypes.c_int
    add = libc.inotify_add_watch
    add.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
    add.restype = ctypes.c_int

    fd = init(os.O_CLOEXEC)
    if fd < 0:
        raise OSError(ctypes.get_errno(), 'inotify_init1 failed')
    try:
        wd = add(fd, os.fsencode(STATE_DIR), WATCH_MASK)
        if wd < 0:
            raise OSError(ctypes.get_errno(), 'inotify_add_watch failed')
        # Emit initial synchronization AFTER installing the watch: no startup
        # race between a saved project and a listener that has not started.
        sys.stdout.write('SYNC\n')
        sys.stdout.flush()
        while True:
            data = os.read(fd, 16384)
            if not data:
                raise RuntimeError('The WSL inotify stream closed.')
            changed, removed = parse_change_events(data)
            if changed:
                sys.stdout.write('SYNC\n')
                sys.stdout.flush()
            if removed:
                raise RuntimeError('The DevStack state directory watch was removed.')
    finally:
        os.close(fd)


if __name__ == '__main__':
    try:
        watch()
    except KeyboardInterrupt:
        pass
    except (OSError, RuntimeError, ValueError) as exc:
        print('DevStack domain watcher: ' + str(exc), file=sys.stderr)
        sys.exit(1)
