"""Engine coordinator tests; no root, systemd, network or Composer required."""
import importlib.util
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "engine_daemon", pathlib.Path(__file__).resolve().parents[1] / "daemon" / "devstackd.py"
)
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


class EngineJobTests(unittest.TestCase):
    def test_unit_runs_official_adapter_as_developer_not_root(self):
        with patch.object(d, "config", return_value={"dev_user": "dev"}), \
             patch.object(d.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=1000, pw_dir="/home/dev")):
            args = d.engine_unit_args("devstack-engine-" + "a"*32 + "-scaffold",
                                      "scaffold", ["demo", "livewire"], 950)
        self.assertIn("--uid=dev", args)
        self.assertIn("--property=NoNewPrivileges=yes", args)
        self.assertIn("--property=ProtectSystem=strict", args)
        self.assertIn("--setenv=HOME=/home/dev", args)
        self.assertEqual(args[-5:], [d.ENGINE_RUNNER, "laravel", "scaffold", "demo", "livewire"])
        self.assertNotIn("--force", args)

    def test_engine_refuses_existing_path_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            (base / "demo").mkdir()
            with patch.object(d, "ENGINE_JOB_FILE", base / "job.json"), \
                 patch.object(d, "STATE_FILE", base / "state.json"), \
                 patch.object(d, "config", return_value={"projects_dir": str(base)}), \
                 patch.object(d, "start_engine_phase") as start:
                with self.assertRaises(d.InputError):
                    d.create({"slug":"demo", "name":"Demo", "domain":"demo.test", "webroot":"public",
                              "database":True, "https":True, "engine":"laravel", "starter_kit":"livewire"})
                start.assert_not_called()
            self.assertTrue((base / "demo").is_dir())

    def test_no_engine_uses_existing_create_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(d, "ENGINE_JOB_FILE", pathlib.Path(tmp) / "job.json"), \
                 patch.object(d, "create_environment", return_value={"message":"ok"}) as create:
                self.assertEqual(d.create({"engine":"none"}), {"message":"ok"})
                create.assert_called_once_with({"engine":"none"})

    def test_job_state_hides_internal_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "job.json"
            with patch.object(d, "ENGINE_JOB_FILE", path):
                d.write_json_atomic(path, {"id":"a"*32, "action":"create", "state":"scaffolding",
                                           "message":"working", "request":{"internal":"value"}})
                self.assertTrue(d.engine_busy())
                self.assertNotIn("request", d.public_engine_job())
                with self.assertRaises(d.InputError):
                    d.engine_tool_action({"engine":"laravel", "action":"update-tool"})

    def test_invalid_tool_result_is_rejected(self):
        self.assertEqual(d.sanitize_tool_result({"composer":True,"installed":"5.33.0"}),
                         {"composer":True,"installed":"5.33.0"})
        with self.assertRaises(RuntimeError):
            d.sanitize_tool_result({"composer":True,"installed":"arbitrary output"})

    def test_restart_does_not_retry_running_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "job.json"
            unit = "devstack-engine-" + "a"*32 + "-update-tool"
            with patch.object(d, "ENGINE_JOB_FILE", path), \
                 patch.object(d, "stop_engine_unit") as stop, \
                 patch.object(d, "start_engine_phase") as start:
                d.write_json_atomic(path, {"id":"a"*32, "action":"update-tool",
                                           "state":"updating", "unit":unit})
                d.recover_engine_job()
                self.assertEqual(d.engine_job()["state"], "failed")
                stop.assert_called_once_with(unit)
                start.assert_not_called()
