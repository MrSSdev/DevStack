#!/usr/bin/env python3
"""CI-only real systemd/D-Bus smoke test. No application service restart.

Creates the dedicated reader account only on the disposable GitHub runner,
then exercises the exact sandbox and unchanged read bridge with fixture keys.
This does not represent a real ASUS/WSL device test.
"""
import base64
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

ROOT=Path(__file__).resolve().parents[1]/'services'
if os.environ.get('GITHUB_ACTIONS')!='true' or os.geteuid()!=0 or not Path('/run/systemd/system').is_dir():
    raise SystemExit('This smoke test is only for the disposable systemd-enabled CI runner.')
spec=importlib.util.spec_from_file_location('reader_setup',ROOT/'setup.py')
setup=importlib.util.module_from_spec(spec);spec.loader.exec_module(setup)
setup.ensure_reader_identity()
identity=setup.reader_identity()
setup.ensure_reader_identity()
assert identity==setup.reader_identity(), 'Account changed on repeat setup'
source=(ROOT/'bridge.py').read_bytes()
# The bus and cgroup operations are real. Use a known active runner unit rather
# than install/restart Nginx, PHP or databases on the runner.
probe='''import base64,json,os,pwd,sys,types
assert os.geteuid()!=0
assert pwd.getpwuid(os.geteuid()).pw_name=="devstack-widget-reader"
mod=types.ModuleType("reader_smoke")
exec(compile(base64.b64decode(SOURCE),"<unchanged-reader>","exec"),mod.__dict__)
mod.UNITS={key:"systemd-journald.service" for key in mod.UNITS}
sys.argv=["reader","read"]
raise SystemExit(mod.main())
'''.replace('SOURCE',repr(base64.b64encode(source).decode()))
loader='import base64;exec(compile(base64.b64decode('+repr(base64.b64encode(probe.encode()).decode())+'),"<smoke>","exec"))'
with tempfile.TemporaryDirectory(prefix='devstack-reader-ci-',dir='/run') as temp:
    temp=Path(temp)
    (temp/'auth').write_text('a'*64+'\n');os.chmod(temp/'auth',0o600)
    (temp/'projects').write_text('{"fixture-one":{},"fixture-two":{}}');os.chmod(temp/'projects',0o600)
    props=[];section=''
    for raw in (ROOT/'devstack-widget-services@.service').read_text().splitlines():
        line=raw.strip()
        if line.startswith('['):section=line;continue
        if section!='[Service]' or not line or line.startswith('#'):continue
        key=line.split('=',1)[0]
        if key in ('ExecStart','StandardInput','StandardOutput','StandardError'):continue
        if key=='LoadCredential':
            alias=line.split('=',1)[1].split(':',1)[0]
            line='LoadCredential='+alias+':'+str(temp/alias)
        props.append('--property='+line)
    cmd=['/usr/bin/systemd-run','--quiet','--wait','--pipe','--collect',
         '--unit=devstack-reader-ci-'+uuid.uuid4().hex[:10],*props,
         '/usr/bin/python3','-I','-u','-c',loader]
    request='GET /v1/services HTTP/1.1\r\nHost: 127.0.0.1:43872\r\nAuthorization: Bearer '+'a'*64+'\r\n\r\n'
    result=subprocess.run(cmd,input=request.encode(),capture_output=True,timeout=25,check=False)
    if result.returncode!=0:
        # No real tokens in this test; do not dump payloads or environment.
        raise SystemExit('Real sandbox reader failed: exit='+str(result.returncode)+' stderr='+result.stderr.decode(errors='replace')[:1500])
    headers,body=result.stdout.split(b'\r\n\r\n',1)
    assert headers.startswith(b'HTTP/1.1 200 '), headers[:80]
    value=json.loads(body)
    assert value['schema']==1 and value['source']=='systemd-cgroup'
    assert value['projectCount']==2 and len(value['services'])==4
    assert all(row['active'] is True for row in value['services'])
    assert all(type(row['memoryBytes']) is int for row in value['services'])
    assert all(isinstance(row['cpuPercent'],(int,float)) for row in value['services'])
    print('PASS: real systemd/D-Bus sandbox read, HTTP 200, four live cgroup rows, project count, non-root static UID and idempotent identity.')
