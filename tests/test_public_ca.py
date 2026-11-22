"""Public CA export: only the certificate may leave the privileged helper."""

import importlib.util
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    'devstack_ca_helper', Path(__file__).resolve().parents[1] / 'daemon/devstackd.py'
)
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


class PublicCaTests(unittest.TestCase):
    def test_read_only_public_certificate_and_sha256(self):
        with tempfile.TemporaryDirectory() as work:
            pem_file = Path(work) / 'rootCA.pem'
            public_pem = b'-----BEGIN CERTIFICATE-----\nTUlJQkZBS0U=\n-----END CERTIFICATE-----\n'
            pem_file.write_bytes(public_pem)
            with patch.object(d, 'CA_PUBLIC_FILE', pem_file):
                result = d.public_ca_certificate({})
                self.assertEqual(result['pem'].encode('ascii'), public_pem)
                self.assertEqual(result['sha256'], hashlib.sha256(public_pem).hexdigest().upper())
                self.assertEqual(set(result), {'pem', 'sha256'})
                with self.assertRaises(d.InputError):
                    d.public_ca_certificate({'path': '/var/lib/devstack/ca/rootCA-key.pem'})

    def test_private_key_and_invalid_cert_are_rejected(self):
        with tempfile.TemporaryDirectory() as work:
            pem_file = Path(work) / 'rootCA.pem'
            with patch.object(d, 'CA_PUBLIC_FILE', pem_file):
                for contents in (
                    b'-----BEGIN PRIVATE KEY-----\nTUlJQkZBS0U=\n-----END PRIVATE KEY-----\n',
                    b'-----BEGIN CERTIFICATE-----\nTUlJQkZBS0U=\n-----END CERTIFICATE-----\nSECRET',
                    b'-----BEGIN CERTIFICATE-----\n***\n-----END CERTIFICATE-----\n',
                    b'',
                ):
                    pem_file.write_bytes(contents)
                    with self.assertRaises(RuntimeError):
                        d.public_ca_certificate({})
                pem_file.unlink()
                pem_file.symlink_to(Path(work) / 'missing.pem')
                with self.assertRaises(RuntimeError):
                    d.public_ca_certificate({})


if __name__ == '__main__':
    unittest.main()
