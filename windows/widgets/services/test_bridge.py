import io,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
import bridge

TOKEN='a'*64

def request(route='GET /v1/services HTTP/1.1',port=43872,token=TOKEN,body=None,extra=''):
    payload=json.dumps(body).encode() if body is not None else b''
    h=f'{route}\r\nHost: 127.0.0.1:{port}\r\nAuthorization: Bearer {token}\r\n'+extra
    if payload:h+=f'Content-Length: {len(payload)}\r\nContent-Type: application/json\r\n'
    return io.BytesIO(h.encode()+b'\r\n'+payload)

class Tests(unittest.TestCase):
    def test_read_authorized(self):self.assertEqual(bridge.parse_request(request(),TOKEN,'read')[0],200)
    def test_no_browser_origin(self):self.assertEqual(bridge.parse_request(request(extra='Origin: https://devstack.test\r\n'),TOKEN,'read')[0],403)
    def test_bad_token(self):self.assertEqual(bridge.parse_request(request(token='b'*64),TOKEN,'read')[0],401)
    def test_rebinding(self):self.assertEqual(bridge.parse_request(io.BytesIO(request().read().replace(b'127.0.0.1',b'evil.test')),TOKEN,'read')[0],403)
    def test_no_actions_on_read(self):self.assertEqual(bridge.parse_request(request('POST /v1/restart HTTP/1.1',body={}),TOKEN,'read')[0],404)
    def test_no_get_restart(self):self.assertEqual(bridge.parse_request(request('GET /v1/restart HTTP/1.1',43873),TOKEN,'control')[0],404)
    def test_post_allowlist(self):
        for service in bridge.UNITS:
            self.assertEqual(bridge.parse_request(request('POST /v1/restart HTTP/1.1',43873,body={'service':service,'requestId':'a'*32}),TOKEN,'control')[0],200)
        for service in ['sshd','nginx.service','nginx;reboot','../nginx',None,[]]:
            self.assertNotEqual(bridge.parse_request(request('POST /v1/restart HTTP/1.1',43873,body={'service':service,'requestId':'a'*32}),TOKEN,'control')[0],200)
    def test_extra_parameters_rejected(self):
        self.assertEqual(bridge.parse_request(request('POST /v1/restart HTTP/1.1',43873,body={'service':'nginx','requestId':'a'*32,'command':'anything'}),TOKEN,'control')[0],400)
    def test_duplicate_headers_and_chunked(self):
        for h in ['Authorization: Bearer '+TOKEN+'\r\n','Host: 127.0.0.1:43872\r\n','Transfer-Encoding: chunked\r\n']:
            self.assertEqual(bridge.parse_request(request(extra=h),TOKEN,'read')[0],400)
    def test_no_body_read(self):self.assertEqual(bridge.parse_request(request(body={}),TOKEN,'read')[0],400)
    def test_missing_count_not_zero(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(bridge.project_count(d))
            (Path(d)/'projects').write_text('{"one":{},"two":{}}')
            self.assertEqual(bridge.project_count(d),2)
    def test_memory_unknown_not_zero(self):
        self.assertIsNone(bridge.counter('[not set]'));self.assertIsNone(bridge.counter('18446744073709551615'))
        self.assertEqual(bridge.counter('1048576'),1048576)
    def test_restart_fixed_command_and_dedup(self):
        with tempfile.TemporaryDirectory() as d,patch.object(bridge.subprocess,'run',return_value=Mock(returncode=0)) as command:
            self.assertEqual(bridge.restart('nginx','a'*32,d)[0],200)
            self.assertEqual(command.call_args.args[0],['/usr/bin/systemctl','--no-ask-password','restart','nginx.service'])
            self.assertEqual(bridge.restart('nginx','a'*32,d)[0],409)
            self.assertEqual(command.call_count,1)
            self.assertEqual(bridge.restart('sshd','b'*32,d)[0],403)
            self.assertEqual(command.call_count,1)
    def test_failed_restart_not_retried(self):
        with tempfile.TemporaryDirectory() as d,patch.object(bridge.subprocess,'run',return_value=Mock(returncode=1)) as cmd:
            self.assertEqual(bridge.restart('php','c'*32,d)[0],503)
            self.assertEqual(bridge.restart('php','c'*32,d)[0],409)
            self.assertEqual(cmd.call_count,1)
    def test_snapshot_count_no_names(self):
        units={unit:{'Id':unit,'LoadState':'loaded','ActiveState':'active','MemoryCurrent':'1000','CPUUsageNSec':'500'} for unit in bridge.UNITS.values()}
        with tempfile.TemporaryDirectory() as d,patch.object(bridge,'system_info',return_value=units),patch.object(bridge.time,'sleep'):
            (Path(d)/'projects').write_text('{"secret-name":{"password":"secret"}}')
            value=bridge.snapshot(d)
            self.assertEqual(value['projectCount'],1);self.assertNotIn('secret',json.dumps(value));self.assertEqual(len(value['services']),4)
    def test_control_health_never_restart(self):
        result=bridge.parse_request(request('GET /v1/health HTTP/1.1',43873),TOKEN,'control')
        self.assertEqual(result,(200,{'health':True}))

if __name__=='__main__':unittest.main()
