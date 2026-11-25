#!/usr/bin/env python3
"""Explicit root installer for the separate service-widget integration.
Never changes WSL telemetry, project records, PHP/API/auth configuration or CA.
"""
import base64
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

FILES=('bridge.py','setup.py','devstack-widget-services.socket','devstack-widget-services@.service',
       'devstack-widget-control.socket','devstack-widget-control@.service')

def run(*args):
    subprocess.run(['/usr/bin/systemctl',*args],check=True,timeout=20,capture_output=True)

def directory(path, mode):
    path=Path(path)
    if path.is_symlink(): raise ValueError('Symlink at installation directory')
    path.mkdir(parents=True,exist_ok=True)
    if path.stat().st_uid!=0: raise ValueError('Installation directory must be root-owned')
    os.chmod(path,mode)
    return path

def atomic(path, data, mode=0o644):
    path=Path(path)
    if path.is_symlink(): raise ValueError('Refusing destination symlink')
    fd,name=tempfile.mkstemp(prefix='.widget-',dir=path.parent)
    try:
        os.fchmod(fd,mode)
        with os.fdopen(fd,'wb') as f: f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)

def health(port,path,token):
    req=urllib.request.Request(f'http://127.0.0.1:{port}{path}',headers={'Authorization':'Bearer '+token})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for attempt in range(3):
        try:
            with opener.open(req,timeout=8) as response:
                if response.status!=200:raise ValueError('Integration health check failed')
                return json.loads(response.read(32769))
        except (OSError,ValueError):
            if attempt==2:raise
            time.sleep(.25)

def install_frontend(raw):
    public=Path('/opt/devstack/public')
    if public.is_symlink() or not (public/'index.php').is_file() or not (public/'index.html').is_file():
        raise ValueError('Expected installed DevStack dashboard was not found')
    entries={}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        total=0
        for info in archive.infolist():
            name=info.filename
            if info.is_dir():continue
            path=PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts or '\\' in name or name in entries or stat.S_ISLNK(info.external_attr>>16):
                raise ValueError('Unsafe frontend archive member')
            if name!='index.html' and not (len(path.parts)==2 and path.parts[0]=='assets' and re.fullmatch(r'[A-Za-z0-9._-]+\.(?:js|css|svg|png|ico|woff2?)',path.name)):
                raise ValueError('Only static index and hashed assets are allowed')
            total+=info.file_size
            if total>20_000_000 or len(entries)>200:raise ValueError('Frontend archive too large')
            entries[name]=archive.read(info)
    if 'index.html' not in entries:raise ValueError('Frontend index missing')
    backup=directory('/var/backups/devstack',0o700)/('widget-frontend-'+time.strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(mode=0o700)
    atomic(backup/'index.html',(public/'index.html').read_bytes(),0o600)
    directory(public/'assets',0o755)
    # Keep prior hashed assets for existing browser sessions and rollback.
    for name,data in entries.items():
        if name!='index.html':atomic(public/name,data)
    atomic(public/'index.html',entries['index.html'])
    print('Static dashboard updated. Previous index saved in '+str(backup))

def main():
    if os.geteuid()!=0:raise ValueError('Root setup required')
    raw=sys.stdin.buffer.read(30_000_001)
    if len(raw)>30_000_000:raise ValueError('Payload too large')
    cfg=json.loads(raw)
    for key in ('readToken','controlToken'):
        if not isinstance(cfg.get(key),str) or not re.fullmatch('[a-f0-9]{64}',cfg[key]):raise ValueError('Invalid credential')
    if cfg['readToken']==cfg['controlToken']:raise ValueError('Credentials must be separate')
    files=cfg.get('files',{})
    expected=set(FILES)|({'frontend.zip'} if cfg.get('frontend') else set())
    if set(files)!=expected:raise ValueError('Unexpected bundle contents')
    decoded={name:base64.b64decode(data,validate=True) for name,data in files.items()}
    projects=Path('/var/lib/devstack/projects.json')
    if not projects.is_file() or projects.is_symlink() or projects.stat().st_size>4_000_000:raise ValueError('DevStack project registry unavailable')
    if not isinstance(json.loads(projects.read_text()),dict):raise ValueError('Unexpected project registry format')
    lib=directory('/usr/local/lib/devstack-widgets',0o755);etc=directory('/etc/devstack-widgets',0o700)
    atomic(lib/'services-bridge.py',decoded['bridge.py'])
    for mode in ('read','control'):atomic(etc/('services-'+mode+'.token'),(cfg[mode+'Token']+'\n').encode(),0o600)
    for name in FILES:
        if name.endswith(('.socket','.service')):atomic(Path('/etc/systemd/system')/name,decoded[name])
    run('daemon-reload')
    for name in ('devstack-widget-services.socket','devstack-widget-control.socket'):
        run('enable','--now',name);run('is-active','--quiet',name)
    result=health(43872,'/v1/services',cfg['readToken'])
    if result.get('schema')!=1 or result.get('source')!='systemd-cgroup':raise ValueError('Invalid service readings')
    if health(43873,'/v1/health',cfg['controlToken']).get('ready') is not True:raise ValueError('Control health check failed')
    print('Service read/control listeners: active. No service restart was executed.')
    if cfg.get('frontend'):install_frontend(decoded['frontend.zip'])
    return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except (OSError,ValueError,KeyError,subprocess.SubprocessError,zipfile.BadZipFile) as e:
        print('DevStack integration setup failed ('+type(e).__name__+'). No tokens printed.',file=sys.stderr)
        raise SystemExit(1)
