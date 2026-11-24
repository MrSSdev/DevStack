import io
import unittest
from collector import cpu_counters,cpu_percent,memory_values,distro_uptime,request_allowed,snapshot

class CollectorTests(unittest.TestCase):
 def request(self,head):return request_allowed(io.BytesIO(head.encode()),'a'*64)
 def test_valid_token(self):
  self.assertEqual(self.request('GET /v1/metrics HTTP/1.1\r\nHost: 127.0.0.1:43871\r\nAuthorization: Bearer '+'a'*64+'\r\n\r\n'),200)
 def test_auth_required(self):self.assertEqual(self.request('GET /v1/metrics HTTP/1.1\r\nHost: 127.0.0.1:43871\r\n\r\n'),401)
 def test_post_rejected(self):self.assertEqual(self.request('POST /v1/metrics HTTP/1.1\r\n\r\n'),404)
 def test_origin_rejected(self):self.assertEqual(self.request('GET /v1/metrics HTTP/1.1\r\nHost: 127.0.0.1:43871\r\nOrigin: http://evil.invalid\r\n\r\n'),403)
 def test_rebinding_rejected(self):self.assertEqual(self.request('GET /v1/metrics HTTP/1.1\r\nHost: evil.invalid\r\n\r\n'),403)
 def test_duplicate_auth(self):self.assertEqual(self.request('GET /v1/metrics HTTP/1.1\r\nAuthorization: a\r\nAuthorization: b\r\n\r\n'),400)
 def test_limit(self):self.assertEqual(self.request('x'*4097),431)
 def test_cpu(self):
  self.assertEqual(cpu_counters('cpu  10 0 5 80 5 0 0 0 40 10\n'),(100,85))
  self.assertEqual(cpu_percent((100,80),(200,150)),30)
  self.assertIsNone(cpu_percent((100,80),(100,80)))
 def test_memory(self):
  self.assertEqual(memory_values('MemTotal: 1000 kB\nMemAvailable: 400 kB\n'),(614400,1024000))
  with self.assertRaises(ValueError):memory_values('MemTotal: 1000 kB\nMemAvailable: 4000 kB\n')
 def test_uptime(self):
  fields=['S']+['0']*18+['5000']+['0']*6
  self.assertEqual(distro_uptime('1 (init with ) bracket) '+' '.join(fields),'150.0 12.4',100),100)
 def test_actual_snapshot_safe(self):
  s=snapshot();self.assertEqual(s['distro'],'Ubuntu-26.04');self.assertEqual(s['schema'],1)
  self.assertGreater(s['diskTotalBytes'],0)
  self.assertLessEqual(s['memoryUsedBytes'],s['memoryTotalBytes'])

if __name__=='__main__':unittest.main()
