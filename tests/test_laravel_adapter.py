import io
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "engines"))
from laravel.adapter import (
    AdapterError, LaravelAdapter, environment_values, project_path,
    rewrite_environment, scaffold_argv, tool_argv, require_supported_frontend,
)


class LaravelAdapterTests(unittest.TestCase):
    def test_official_installer_never_forces_overwrite(self):
        argv = scaffold_argv("/home/dev/.config/composer/vendor/bin/laravel", "demo-app", "livewire")
        self.assertEqual(argv[:3], ["/home/dev/.config/composer/vendor/bin/laravel", "new", "demo-app"])
        self.assertIn("--livewire", argv)
        self.assertIn("--no-interaction", argv)
        self.assertIn("--database=sqlite", argv)
        self.assertNotIn("--force", argv)
        self.assertNotIn("--livewire", scaffold_argv("laravel", "demo", "none"))
        for slug, kit in (("../demo", "none"), ("--force", "none"), ("demo", "--force")):
            with self.assertRaises(AdapterError):
                scaffold_argv("laravel", slug, kit)

    def test_existing_directory_and_symlink_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "exists").mkdir()
            (root / "linked").symlink_to(root / "exists", target_is_directory=True)
            (root / "broken").symlink_to(root / "absent")
            for slug in ("exists", "linked", "broken"):
                with self.assertRaises(AdapterError):
                    project_path(root, slug, exists=False)
            self.assertEqual(project_path(root, "fresh", exists=False), root / "fresh")

    def test_scoped_tool_update(self):
        self.assertEqual(tool_argv("/usr/bin/composer", "update-tool")[:4],
                         ["/usr/bin/composer", "global", "update", "laravel/installer"])
        self.assertEqual(tool_argv("/usr/bin/composer", "install-tool")[:4],
                         ["/usr/bin/composer", "global", "require", "laravel/installer"])
        with self.assertRaises(AdapterError):
            tool_argv("composer", "arbitrary")

    def test_configure_preserves_unrelated_settings_and_removes_duplicates(self):
        data = {"url": "https://demo.test", "db_name": "demo_db", "db_user": "demo_user",
                "db_password": "example_generated_token"}
        values = environment_values(data)
        actual = rewrite_environment("APP_KEY=unchanged\nAPP_URL=http://localhost\n"
                                     "DB_CONNECTION=sqlite\n# DB_HOST=localhost\n"
                                     "DB_PASSWORD=old\nDB_PASSWORD=duplicate\n", values)
        self.assertIn("APP_KEY=unchanged\n", actual)
        self.assertIn("APP_URL=https://demo.test\n", actual)
        self.assertIn("DB_CONNECTION=mariadb\n", actual)
        self.assertEqual(actual.count("DB_PASSWORD="), 1)
        self.assertNotIn("duplicate", actual)
        self.assertNotIn("DB_CONNECTION", environment_values({"url": "https://demo.test"}))
        for bad in ({"url": "https://example.com"}, {**data, "db_password": "bad\nAPP_KEY=no"},
                    {**data, "command": "bad"}):
            with self.assertRaises(AdapterError):
                environment_values(bad)

    def test_configuration_is_private_and_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            root = home / "projects" / "demo"
            root.mkdir(parents=True)
            env = root / ".env"
            env.write_text("APP_KEY=unchanged\n")
            adapter = LaravelAdapter(home, io.StringIO())
            self.assertEqual(adapter.configure("demo", {"url": "https://demo.test"}), {"configured": True})
            self.assertEqual(env.stat().st_mode & 0o777, 0o600)
            self.assertIn("APP_KEY=unchanged\n", env.read_text())
            env.unlink()
            env.symlink_to(home / "other")
            with self.assertRaises(AdapterError):
                adapter.configure("demo", {"url": "https://demo.test"})

    def test_frontend_version_baseline(self):
        require_supported_frontend("v22.22.1", "11.19.1")
        require_supported_frontend("v24.22.3", "11.19.1")
        require_supported_frontend("v20.19.0", "11.0.0")
        for node, npm, code in (
            ("v22.22.1", "9.2.0", "frontend_npm"),
            ("v22.11.0", "11.19.1", "frontend_node"),
            ("v20.18.0", "11.19.1", "frontend_node"),
            ("v21.7.3", "11.19.1", "frontend_node"),
            ("unknown", "11.19.1", "frontend_node"),
            ("v22.22.1", "unknown", "frontend_npm"),
        ):
            with self.subTest(node=node, npm=npm):
                with self.assertRaises(AdapterError) as error:
                    require_supported_frontend(node, npm)
                self.assertEqual(error.exception.code, code)

    def test_unsupported_npm_refused_before_scaffold(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            (home / "projects").mkdir()
            adapter = LaravelAdapter(home, io.StringIO())
            with patch("laravel.adapter.shutil.which", side_effect=lambda tool: "/usr/bin/" + tool), \
                 patch.object(adapter, "command", side_effect=["v22.22.1", "9.2.0"]) as command, \
                 patch.object(adapter, "installer") as installer:
                with self.assertRaises(AdapterError) as error:
                    adapter.scaffold("demo", "livewire")
                self.assertEqual(error.exception.code, "frontend_npm")
                installer.assert_not_called()
                self.assertEqual(command.call_count, 2)
            self.assertFalse((home / "projects" / "demo").exists())

    def test_failed_scaffold_does_not_delete_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            (home / "projects").mkdir()
            root = home / "projects" / "demo"
            adapter = LaravelAdapter(home, io.StringIO())
            def fail(*args, **kwargs):
                root.mkdir()
                (root / "partial").write_text("keep")
                raise AdapterError("failed")
            with patch.object(adapter, "require_frontend_tools"), \
                 patch.object(adapter, "installer", return_value=pathlib.Path("/fake/laravel")), \
                 patch.object(adapter, "command", side_effect=fail):
                with self.assertRaises(AdapterError):
                    adapter.scaffold("demo", "livewire")
            self.assertTrue((root / "partial").is_file())


if __name__ == "__main__":
    unittest.main()
