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

    def test_discovered_kit_metadata_is_sanitized(self):
        base = {"composer": True, "installed": "5.33.0"}
        kits = [{"id": "none", "label": "Blade"}, {"id": "new-kit", "label": "New Kit"}]
        self.assertEqual(d.sanitize_tool_result({**base, "starter_kits": kits})["starter_kits"], kits)
        for invalid in ([{"id": "--force", "label": "Bad"}], [kits[0], kits[0]],
                        [{"id": "none", "label": "Bad\ntext"}], [{"id": "none", "label": "Blade", "flags": ["--force"]}], []):
            with self.assertRaises(RuntimeError):
                d.sanitize_tool_result({**base, "starter_kits": invalid})

    def test_framework_options_and_public_root_are_checked_before_reservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            fields = [{"id": "teams", "label": "Teams", "default": "no", "kits": ["livewire"],
                       "when": {}, "choices": [{"id":"no","label":"No"}, {"id":"yes","label":"Yes"}]}]
            tool = {"composer":True, "installed":"5.33.0",
                    "starter_kits":[{"id":"none","label":"Blade"},{"id":"livewire","label":"Livewire"}],
                    "installer_options":fields}
            self.assertEqual(d.sanitize_tool_result(tool), tool)
            for invalid in ([{**fields[0], "flags": ["--force"]}], [{**fields[0], "when": {"unknown": ["yes"]}}],
                            [{**fields[0], "kits": ["missing"]}], [{**fields[0], "default": "--force"}]):
                with self.assertRaises(RuntimeError):
                    d.sanitize_tool_result({**tool, "installer_options":invalid})
            with patch.object(d, "ENGINE_JOB_FILE", base / "job.json"), \
                 patch.object(d, "STATE_FILE", base / "state.json"), \
                 patch.object(d, "config", return_value={"projects_dir": str(base)}), \
                 patch.object(d, "require_engine_auth"), \
                 patch.object(d, "engine_tool_status", return_value=tool), \
                 patch.object(d, "reserve_engine_database") as reserve, \
                 patch.object(d, "start_engine_phase") as start:
                payload = {"slug":"demo","name":"Demo","domain":"demo.test",
                           "database":False,"https":True,"engine":"laravel","starter_kit":"livewire"}
                for extra in ({"webroot":"."}, {"webroot":"other"}, {"installer_options":{"teams":"--force"}},
                              {"starter_kit":"none","installer_options":{"teams":"yes"}}):
                    with self.assertRaises(d.InputError):
                        d.create({**payload, **extra})
                reserve.assert_not_called()
                start.assert_not_called()
                self.assertFalse((base / "job.json").exists())
                d.create({**payload, "installer_options":{"teams":"yes"}})
                request = d.engine_job()["request"]
                self.assertEqual(request["webroot"], "public")
                self.assertEqual(request["installer_options"], {"teams":"yes"})
                self.assertEqual(start.call_args.args[-1]["installer_options"], {"teams":"yes"})
                reserve.assert_not_called()

    def test_stale_kit_is_rejected_before_database_reservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            with patch.object(d, "ENGINE_JOB_FILE", base / "job.json"), \
                 patch.object(d, "STATE_FILE", base / "state.json"), \
                 patch.object(d, "config", return_value={"projects_dir": str(base)}), \
                 patch.object(d, "require_engine_auth"), \
                 patch.object(d, "engine_tool_status", return_value={"installed": "5.33.0", "starter_kits": [{"id":"none","label":"Blade"}]}), \
                 patch.object(d, "reserve_engine_database") as reserve:
                with self.assertRaisesRegex(d.InputError, "nie jest juz dostepny"):
                    d.create({"slug":"demo", "name":"Demo", "domain":"demo.test", "webroot":"public",
                              "database":True, "https":True, "engine":"laravel", "starter_kit":"livewire"})
                reserve.assert_not_called()
                self.assertFalse((base / "job.json").exists())

    def test_frontend_failure_code_is_allowlisted(self):
        import json
        unit = "devstack-engine-" + "a"*32 + "-scaffold"
        for code, expected in (("frontend_npm", "frontend_npm"), ("arbitrary output", None), ([], None)):
            outcome = SimpleNamespace(returncode=1, stdout=json.dumps({
                "ok": False, "code": code, "error": "private diagnostic",
                "log": "laravel-test.log",
            }))
            with patch.object(d, "engine_unit_args", return_value=["fake"]), \
                 patch.object(d.subprocess, "run", return_value=outcome):
                result = d.adapter_call(unit, "scaffold", ["demo", "livewire"])
            self.assertEqual(result, {"ok": False, "code": expected, "log": "laravel-test.log"})

    def test_passwordless_mode_requires_explicit_installer_consent(self):
        with tempfile.TemporaryDirectory() as tmp:
            mode = pathlib.Path(tmp) / "auth.mode"
            consent = pathlib.Path(tmp) / "engine-access.mode"
            mode.write_text("disabled")
            with patch.object(d, "AUTH_MODE_FILE", mode), \
                 patch.object(d, "ENGINE_ACCESS_FILE", consent):
                self.assertFalse(d.engine_access_allowed())
                with self.assertRaises(d.InputError):
                    d.engine_tool_status({"engine": "laravel"})
                with self.assertRaises(d.InputError):
                    d.engine_tool_action({"engine": "laravel", "action": "check"})
                with patch.object(d, "ENGINE_JOB_FILE", pathlib.Path(tmp) / "job.json"), \
                     patch.object(d, "start_engine_phase") as start:
                    with self.assertRaises(d.InputError):
                        d.create({"engine": "laravel"})
                    start.assert_not_called()
                for value in ("invalid", "deny-passwordless", ""):
                    consent.write_text(value)
                    self.assertFalse(d.engine_access_allowed())
                consent.write_text("allow-passwordless\n")
                self.assertTrue(d.engine_access_allowed())
                d.require_engine_auth()
                consent.unlink()
                self.assertFalse(d.engine_access_allowed())
                mode.write_text("required\n")
                self.assertTrue(d.engine_access_allowed())

    def test_mariadb_reservation_is_private_and_cleanup_is_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            job = {"id": "b"*32, "slug": "demo", "engine": "laravel", "action": "create"}
            name, user = d.db_identifiers("demo")
            with patch.object(d, "ENGINE_DB_FILE", base / "reservation.json"), \
                 patch.object(d, "STATE_FILE", base / "state.json"), \
                 patch.object(d, "SECRET_FILE", base / "credentials.json"), \
                 patch.object(d, "create_db", side_effect=lambda slug, password: (name, user, password)) as create_db, \
                 patch.object(d, "drop_db") as drop_db:
                reservation = d.reserve_engine_database(job)
                self.assertEqual(reservation[:2], (name, user))
                self.assertEqual(d.ENGINE_DB_FILE.stat().st_mode & 0o777, 0o600)
                create_db.assert_called_once()
                self.assertEqual(d.prepared_engine_database(job), reservation)
                self.assertNotIn("password", d.public_engine_job() or {})
                self.assertIn("wymaga", d.release_engine_database(job, allow_cleanup=False))
                drop_db.assert_not_called()
                d.release_engine_database(job, allow_cleanup=True)
                drop_db.assert_called_once_with(name, user)
                self.assertFalse(d.ENGINE_DB_FILE.exists())

    def test_success_keeps_registered_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            name, user = d.db_identifiers("demo")
            job = {"id": "c"*32, "slug": "demo", "engine": "laravel", "action": "create"}
            with patch.object(d, "ENGINE_DB_FILE", base / "reservation.json"), \
                 patch.object(d, "STATE_FILE", base / "state.json"), \
                 patch.object(d, "SECRET_FILE", base / "credentials.json"), \
                 patch.object(d, "drop_db") as drop_db:
                d.write_json_atomic(d.ENGINE_DB_FILE, {"id": job["id"], "slug": "demo", "name": name,
                    "user": user, "password": "fixture", "phase": "ready"})
                d.write_json_atomic(d.STATE_FILE, {"demo": {"db_name": name, "db_user": user}})
                d.write_json_atomic(d.SECRET_FILE, {"demo": {"password": "fixture"}})
                self.assertEqual(d.release_engine_database(job, allow_cleanup=True), "")
                drop_db.assert_not_called()
                self.assertFalse(d.ENGINE_DB_FILE.exists())

    def test_existing_mariadb_user_is_not_dropped(self):
        statements = []
        def sql(statement):
            statements.append(statement)
            if statement.startswith("CREATE USER"):
                raise RuntimeError("existing user")
        with patch.object(d, "mariadb_execute", side_effect=sql):
            with self.assertRaises(RuntimeError):
                d.create_db("demo", password="fixture")
        self.assertTrue(any(s.startswith("DROP DATABASE") for s in statements))
        self.assertFalse(any(s.startswith("DROP USER") for s in statements))

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
