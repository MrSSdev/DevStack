#!/usr/bin/env python3
"""Socket-activated DevStack widget integration: separate read and control units.

No daemon loop, shell commands or WSL startup. Never expose project records,
credentials, arbitrary units or command output. Each connection exits after one
bounded request. The control unit runs as root; the reader uses DynamicUser.
"""
from __future__ import annotations
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

UNITS = {'nginx': 'nginx.service', 'php': 'php8.5-fpm.service',
         'mariadb': 'mariadb.service', 'redis': 'redis-server.service'}
READ_PORT, CONTROL_PORT = 43872, 43873
SYSTEMCTL = '/usr/bin/systemctl'
ENV = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'SYSTEMD_PAGER': '', 'SYSTEMD_COLORS': '0'}


def parse_request(stream, token, mode):
    """Validate framing and origin BEFORE consuming body or doing any work."""
    first = stream.readline(4097)
    if len(first) > 4096: return 431, None
    route = first.rstrip(b'\r\n')
    allowed = ([b'GET /v1/services HTTP/1.1'] if mode == 'read' else
               [b'POST /v1/restart HTTP/1.1', b'GET /v1/health HTTP/1.1'])
    if route not in allowed: return 404, None
    headers, length = {}, len(first)
    for _ in range(40):
        line = stream.readline(4097); length += len(line)
        if length > 8192 or len(line) > 4096: return 431, None
        if line in (b'\n', b'\r\n'): break
        if not line or b':' not in line: return 400, None
        key, value = line.split(b':', 1); key = key.strip().lower()
        if key in headers: return 400, None
        headers[key] = value.strip()
    else: return 431, None
    port = READ_PORT if mode == 'read' else CONTROL_PORT
    if headers.get(b'host') != f'127.0.0.1:{port}'.encode() or b'origin' in headers:
        return 403, None
    if b'transfer-encoding' in headers: return 400, None
    if not hmac.compare_digest(headers.get(b'authorization', b''), ('Bearer '+token).encode()):
        return 401, None
    rawlen = headers.get(b'content-length', b'0')
    if not re.fullmatch(rb'[0-9]{1,4}', rawlen): return 400, None
    n = int(rawlen)
    if route.startswith(b'GET '):
        return (200, {'health': True}) if not n else (400, None)
    if n < 1 or n > 1024 or headers.get(b'content-type', b'').split(b';')[0].strip() != b'application/json':
        return 400, None
    body = stream.read(n)
    if len(body) != n: return 400, None
    try: data = json.loads(body)
    except (ValueError, UnicodeError): return 400, None
    if not isinstance(data, dict) or set(data) != {'service', 'requestId'}: return 400, None
    if not isinstance(data['service'], str) or data['service'] not in UNITS: return 403, None
    if not isinstance(data['requestId'], str) or not re.fullmatch(r'[a-f0-9]{32}', data['requestId']):
        return 400, None
    return 200, data


def system_info():
    reply = subprocess.run([SYSTEMCTL, 'show', '--no-pager', '--property=Id,LoadState,ActiveState,MemoryCurrent,CPUUsageNSec',
                            *UNITS.values()], capture_output=True, text=True, timeout=2, env=ENV, check=False)
    if reply.returncode != 0 or len(reply.stdout) > 32768: raise ValueError('units unavailable')
    units = {}
    for block in reply.stdout.strip().split('\n\n'):
        values = dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
        if values.get('Id') in UNITS.values(): units[values['Id']] = values
    return units


def counter(value):
    try:
        result = int(value)
        return result if 0 <= result < 2**63 else None
    except (ValueError, TypeError): return None


def project_count(credential_dir):
    """PID1 copies the protected projects file for this one-shot reader.
    Only a count is returned; no names, paths, domains, SQL credentials or files.
    """
    path = Path(credential_dir) / 'projects'
    if not path.exists() or path.stat().st_size > 4_000_000: return None
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or len(data) > 100000: return None
        if any(not isinstance(k, str) or not isinstance(v, dict) for k,v in data.items()): return None
        return len(data)
    except (OSError, ValueError): return None


def snapshot(credential_dir):
    before = system_info(); start = time.monotonic(); time.sleep(.12)
    after = system_info(); interval = max(.001, time.monotonic()-start)
    rows = []
    for key, unit in UNITS.items():
        values, old = after.get(unit, {}), before.get(unit, {})
        loaded = values.get('LoadState') == 'loaded'
        active = values.get('ActiveState') == 'active' if loaded else None
        first, last = counter(old.get('CPUUsageNSec')), counter(values.get('CPUUsageNSec'))
        cpu = None
        if active and first is not None and last is not None and last >= first:
            cpu = round((last-first) / (interval*1e9) * 100, 2)  # 100% = one logical CPU
        rows.append({'id':key, 'unit':unit, 'active':active,
                     'memoryBytes': counter(values.get('MemoryCurrent')) if active else None,
                     'cpuPercent':cpu})
    return {'schema':1, 'distro':'Ubuntu-26.04', 'source':'systemd-cgroup',
            'projectCount':project_count(credential_dir), 'services':rows}


def restart(service, request_id, runtime='/run/devstack-widget-control'):
    if service not in UNITS or not re.fullmatch(r'[a-f0-9]{32}', request_id): return 403, {'ok':False}
    path = Path(runtime)
    with (path / 'lock').open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return 409, {'ok':False, 'error':'busy'}
        # Consume the request ID BEFORE the operation; a timeout is never retried.
        ledger = path / 'requests.json'
        records = json.loads(ledger.read_text()) if ledger.exists() else {}
        now = time.time(); records = {k:v for k,v in records.items() if now-v < 86400}
        key = hashlib.sha256(request_id.encode()).hexdigest()
        if key in records: return 409, {'ok':False, 'error':'duplicate'}
        if len(records) >= 4096: return 429, {'ok':False, 'error':'rate_limit'}
        records[key] = now
        tmp = path / 'requests.tmp'; tmp.write_text(json.dumps(records)); os.chmod(tmp,0o600); tmp.replace(ledger)
        result = subprocess.run([SYSTEMCTL,'--no-ask-password','restart',UNITS[service]],
            capture_output=True, timeout=12, env=ENV, check=False)
        return (200 if result.returncode == 0 else 503), {'ok':result.returncode == 0, 'service':service}


def respond(output, status, body):
    data = json.dumps(body, separators=(',', ':'), allow_nan=False).encode()
    reasons={200:'OK',400:'Bad Request',401:'Unauthorized',403:'Forbidden',404:'Not Found',409:'Conflict',429:'Too Many Requests',431:'Request Header Fields Too Large',503:'Service Unavailable'}
    header=f'HTTP/1.1 {status} {reasons[status]}\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\nConnection: close\r\n\r\n'
    output.write(header.encode()+data); output.flush()


def main():
    mode = sys.argv[1] if len(sys.argv) == 2 else ''
    if mode not in ('read','control'): return 2
    signal.alarm(18 if mode == 'control' else 6)
    try:
        directory = os.environ['CREDENTIALS_DIRECTORY']
        token = (Path(directory)/'auth').read_text().strip()
        if not re.fullmatch(r'[a-f0-9]{64}',token): raise ValueError('invalid credential')
        status, data = parse_request(sys.stdin.buffer,token,mode)
        if status != 200: body={'ok':False,'error':'request_rejected'}
        elif mode == 'read': body=snapshot(directory)
        elif data.get('health'): body={'schema':1,'ready':True}
        else: status,body=restart(data['service'],data['requestId'])
        respond(sys.stdout.buffer,status,body)
    except (OSError,ValueError,KeyError,subprocess.SubprocessError):
        try: respond(sys.stdout.buffer,503,{'ok':False,'error':'integration_unavailable'})
        except OSError: pass
        return 1
    return 0

if __name__ == '__main__': raise SystemExit(main())
