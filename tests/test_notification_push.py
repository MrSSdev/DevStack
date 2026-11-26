import base64
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch, MagicMock

spec = importlib.util.spec_from_file_location(
    "notification_push", Path(__file__).resolve().parents[1] / "scripts/notification-push.py"
)
push = importlib.util.module_from_spec(spec)
spec.loader.exec_module(push)


def b64(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class NotificationPushTests(unittest.TestCase):
    def subscription(self, endpoint="https://fcm.googleapis.com/fcm/send/example"):
        return {"endpoint": endpoint, "keys": {"p256dh": b64(b"\x04" + b"x"*64), "auth": b64(b"y"*16)}}

    def test_only_known_push_providers(self):
        for endpoint in (
            "https://fcm.googleapis.com/fcm/send/example",
            "https://fcm.googleapis.com/wp/example",
            "https://updates.push.services.mozilla.com/wpush/v2/example",
            "https://dm3p.notify.windows.com/?token=example",
        ):
            self.assertEqual(push.valid_subscription(self.subscription(endpoint))["endpoint"], endpoint)
        for endpoint in (
            "https://127.0.0.1:443/x",
            "https://devstack.test/api/state",
            "https://evil.invalid",
            "https://fcm.googleapis.com.evil.invalid/fcm/send/x",
            "https://fcm.googleapis.com@evil.invalid/fcm/send/x",
            "https://user@fcm.googleapis.com/fcm/send/x",
            "https://fcm.googleapis.com:444/fcm/send/x",
            "https://fcm.googleapis.com/elsewhere",
            "https://notify.windows.com.evil.invalid/?token=x",
            "http://fcm.googleapis.com/fcm/send/x",
            "https://fcm.googleapis.com/fcm/send/x#fragment",
            "https://fcm.googleapis.com/fcm/send/x\n",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                push.valid_subscription(self.subscription(endpoint))

    def test_invalid_encryption_key_is_rejected(self):
        value = self.subscription()
        value["keys"]["auth"] = b64(b"short")
        with self.assertRaises(ValueError):
            push.valid_subscription(value)

    def test_sender_checks_endpoint_and_disables_redirects(self):
        # No external network request is made in this test. Stub the library
        # only at the encryption/transport boundary and inspect its arguments.
        import sys
        from types import SimpleNamespace

        class Session:
            trust_env = True
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def request(self, method, url, **kwargs):
                return kwargs

        class PushError(Exception):
            pass

        status = 201

        def fake_webpush(subscription, **kwargs):
            session = kwargs["requests_session"]
            self.assertFalse(session.trust_env)
            self.assertFalse(session.request("POST", subscription["endpoint"], allow_redirects=True)["allow_redirects"])
            with self.assertRaises(ValueError):
                session.request("POST", "https://127.0.0.1/")
            self.assertEqual(kwargs["ttl"], 30)
            self.assertEqual(kwargs["timeout"], 8)
            self.assertNotIn("subject", kwargs["data"])
            if status == 410:
                error = PushError('Do not expose provider data')
                error.response = SimpleNamespace(status_code=410, text='Private response body')
                raise error
            return SimpleNamespace(status_code=status)

        mods = {"pywebpush": SimpleNamespace(webpush=fake_webpush, WebPushException=PushError),
                "requests": SimpleNamespace(Session=Session)}
        with patch.dict(sys.modules, mods), patch.object(push, "KEY", MagicMock(is_file=lambda: True)):
            request = {"subscription": self.subscription(), "payload": {
                "id": "mail-" + "M"*22, "category": "mail", "kind": "info"}}
            result = push.send(request)
            self.assertEqual(result, {"ok": True, "http_status": 201})
            status = 410
            self.assertEqual(push.send(request), {"ok": False, "expired": True, "http_status": 410})


if __name__ == "__main__":
    unittest.main()
