import importlib.util
import pathlib
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "mail_daemon", pathlib.Path(__file__).resolve().parents[1] / "daemon" / "devstackd.py"
)
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


class MailpitTests(unittest.TestCase):
    def test_missing_unit_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(d, "MAILPIT_UNIT_FILE", pathlib.Path(tmp) / "missing.service"), \
             patch.object(d.subprocess, "run") as run:
            self.assertEqual(d.mailpit_status(), "not-installed")
            run.assert_not_called()

    def test_only_fixed_unit_is_queried(self):
        with tempfile.TemporaryDirectory() as tmp:
            unit = pathlib.Path(tmp) / "unit"
            unit.touch()
            with patch.object(d, "MAILPIT_UNIT_FILE", unit), \
                 patch.object(d.subprocess, "run", return_value=SimpleNamespace(stdout="active\n")) as run:
                self.assertEqual(d.mailpit_status(), "active")
                run.assert_called_once_with(
                    ["/usr/bin/systemctl", "is-active", "devstack-mailpit.service"],
                    capture_output=True, text=True, timeout=2)
                for value, expected in (("inactive\n", "inactive"), ("failed\n", "failed"), ("arbitrary output", "unknown")):
                    run.return_value = SimpleNamespace(stdout=value)
                    self.assertEqual(d.mailpit_status(), expected)
                run.side_effect = subprocess.TimeoutExpired("systemctl", 2)
                self.assertEqual(d.mailpit_status(), "unknown")

    def test_mail_is_explicit_and_requires_running_service(self):
        with patch.object(d, "mailpit_status", return_value="not-installed") as status:
            self.assertFalse(d.require_local_mail({}))
            self.assertFalse(d.require_local_mail({"local_mail": False}))
            status.assert_not_called()
            for value in (1, "yes", None, {}, []):
                with self.assertRaises(d.InputError):
                    d.require_local_mail({"local_mail": value})
            with self.assertRaises(d.InputError):
                d.require_local_mail({"local_mail": True})
            status.return_value = "active"
            self.assertTrue(d.require_local_mail({"local_mail": True}))

    def test_php_mail_configuration_is_per_project(self):
        ordinary = d.php_pool("demo", "ubuntu")
        opted_in = d.php_pool("demo", "ubuntu", True)
        self.assertNotIn("sendmail_path", ordinary)
        self.assertEqual(opted_in, ordinary + "php_admin_value[sendmail_path] = /opt/devstack-mailpit/mailpit sendmail -S 127.0.0.1:1025 -t -i\n")

    def test_core_service_contract_is_unchanged(self):
        with patch.object(d.subprocess, "run", return_value=SimpleNamespace(stdout="active\n")):
            self.assertEqual(set(d.service_status()), {"nginx", "php8.5-fpm", "mariadb", "redis-server"})
