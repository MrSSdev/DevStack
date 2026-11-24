#!/usr/bin/env python3
"""One authenticated, read-only response per systemd socket activation.

No timers, background sampling, shell execution, disk writes or service actions.
CPU/RAM are kernel-wide WSL metrics; disk and PID-1 uptime belong to this distro.
"""
from __future__ import annotations
import hmac
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import BinaryIO

DISTRO = 'Ubuntu-26.04'
PORT = 43871
MAX_HEADERS = 8192


def cpu_counters(text: str) -> tuple[int, int]:
    line = next(line for line in text.splitlines() if line.startswith('cpu '))
    values = [int(n) for n in line.split()[1:9]]  # guest times already included
    if len(values) < 4 or min(values) < 0:
        raise ValueError('Invalid CPU counters')
    return sum(values), values[3] + (values[4] if len(values) > 4 else 0)


def cpu_percent(first: tuple[int, int], second: tuple[int, int]) -> float | None:
    total = second[0] - first[0]
    idle = second[1] - first[1]
    if total <= 0 or idle < 0 or idle > total:
        return None
    return round(100 * (total - idle) / total, 2)


def memory_values(text: str) -> tuple[int, int]:
    values = {}
    for line in text.splitlines():
        bits = line.split()
        if bits and bits[0] in ('MemTotal:', 'MemAvailable:'):
            if len(bits) != 3 or bits[2] != 'kB':
                raise ValueError('Invalid memory unit')
            values[bits[0]] = int(bits[1]) * 1024
    total, available = values['MemTotal:'], values['MemAvailable:']
    if total <= 0 or not 0 <= available <= total:
        raise ValueError('Invalid memory values')
    return total - available, total


def distro_uptime(stat: str, kernel_uptime: str, ticks: int) -> float:
    # /proc/PID/stat: the command can contain spaces and closing parentheses.
    fields = stat[stat.rfind(')') + 2:].split()
    start_ticks = int(fields[19])  # field 22, remaining sequence begins at field 3
    if ticks <= 0:
        raise ValueError('Invalid clock ticks')
    return max(0.0, float(kernel_uptime.split()[0]) - start_ticks / ticks)


def snapshot() -> dict:
    result = dict(schema=1, distro=DISTRO, source='linux-procfs',
                  cpuScope='shared-wsl-kernel', cpuPercent=None,
                  memoryUsedBytes=None, memoryTotalBytes=None,
                  diskUsedBytes=None, diskTotalBytes=None, uptimeSeconds=None)
    try:
        first = cpu_counters(Path('/proc/stat').read_text())
        time.sleep(0.15)  # request-local sample, not a resident polling loop
        result['cpuPercent'] = cpu_percent(first, cpu_counters(Path('/proc/stat').read_text()))
    except (OSError, ValueError, StopIteration):
        pass
    try:
        result['memoryUsedBytes'], result['memoryTotalBytes'] = memory_values(Path('/proc/meminfo').read_text())
    except (OSError, ValueError, KeyError):
        pass
    try:
        disk = os.statvfs('/')
        result['diskTotalBytes'] = disk.f_blocks * disk.f_frsize
        result['diskUsedBytes'] = (disk.f_blocks - disk.f_bfree) * disk.f_frsize
    except OSError:
        pass
    try:
        result['uptimeSeconds'] = round(distro_uptime(Path('/proc/1/stat').read_text(), Path('/proc/uptime').read_text(), os.sysconf('SC_CLK_TCK')), 1)
    except (OSError, ValueError, IndexError):
        pass
    return result


def request_allowed(stream: BinaryIO, token: str) -> int:
    first = stream.readline(4097)
    if len(first) > 4096:
        return 431
    if first.rstrip(b'\r\n') != b'GET /v1/metrics HTTP/1.1':
        return 404
    headers: dict[bytes, bytes] = {}
    total = len(first)
    for _ in range(40):
        line = stream.readline(4097)
        total += len(line)
        if total > MAX_HEADERS or len(line) > 4096:
            return 431
        if line in (b'\r\n', b'\n'):
            break
        if not line or b':' not in line:
            return 400
        key, value = line.split(b':', 1)
        key, value = key.strip().lower(), value.strip()
        if key in headers:
            return 400
        headers[key] = value
    else:
        return 431
    if headers.get(b'host') != f'127.0.0.1:{PORT}'.encode() or b'origin' in headers:
        return 403  # no browser origin or DNS rebinding surface
    if b'transfer-encoding' in headers or headers.get(b'content-length', b'0') != b'0':
        return 400
    actual = headers.get(b'authorization', b'')
    expected = ('Bearer ' + token).encode('ascii')
    return 200 if hmac.compare_digest(actual, expected) else 401


def respond(output: BinaryIO, status: int, payload: dict) -> None:
    encoded = json.dumps(payload, separators=(',', ':'), allow_nan=False).encode('utf-8')
    reason = {200:'OK',400:'Bad Request',401:'Unauthorized',403:'Forbidden',404:'Not Found',431:'Request Header Fields Too Large',503:'Service Unavailable'}[status]
    head = f'HTTP/1.1 {status} {reason}\r\nContent-Type: application/json; charset=utf-8\r\nContent-Length: {len(encoded)}\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\nConnection: close\r\n\r\n'
    output.write(head.encode('ascii') + encoded)
    output.flush()


def main() -> int:
    signal.alarm(4)  # also bounded by systemd RuntimeMaxSec
    try:
        token = (Path(os.environ['CREDENTIALS_DIRECTORY']) / 'auth').read_text().strip()
        if len(token) != 64 or any(c not in '0123456789abcdef' for c in token):
            raise ValueError('Invalid credential')
        status = request_allowed(sys.stdin.buffer, token)
        respond(sys.stdout.buffer, status, snapshot() if status == 200 else {'error':'request_rejected'})
    except (KeyError, OSError, ValueError):
        try:
            respond(sys.stdout.buffer, 503, {'error':'collector_unavailable'})
        except (BrokenPipeError, OSError):
            pass
        return 1
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
