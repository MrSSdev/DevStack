import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "framework-installers"))
from laravel.adapter import (
    AdapterError, LaravelAdapter, environment_values, project_path,
    rewrite_environment, scaffold_argv, tool_argv, require_supported_frontend, discover_starter_kits,
)


def help_fixture(kits=("react", "svelte", "vue", "livewire"), *, blank=True):
    def option(name, description="", accepts=False):
        return {"name": "--" + name, "description": description,
                "accept_value": accepts, "is_multiple": False, "default": False}
    options = {name: option(name) for name in ("database", "pest", "npm", "no-boost")}
    options.update({name: option(name, "Install the " + name.capitalize() + " Starter Kit") for name in kits})
    options["force"] = option("force", "Install the Dangerous Starter Kit")
    options["using"] = option("using", "Install the Custom Starter Kit", accepts=True)
    if blank:
        options["no-authentication"] = option("no-authentication")
    return json.dumps({"name": "new", "definition": {"options": options}})


class LaravelAdapterTests(unittest.TestCase):
    def test_discovery_tracks_installed_help_without_static_list(self):
        choices = discover_starter_kits(help_fixture())
        self.assertEqual(set(choices), {"none", "react", "svelte", "vue", "livewire",
                                         "blank-react", "blank-svelte", "blank-vue", "blank-livewire"})
        self.assertEqual(choices["blank-vue"]["flags"], ["--vue", "--no-authentication"])
        changed = discover_starter_kits(help_fixture(("future-kit", "react"), blank=False))
        self.assertEqual(set(changed), {"none", "future-kit", "react"})
        self.assertIn("--future-kit", scaffold_argv("laravel", "demo", "future-kit", choices=changed))
        with self.assertRaises(AdapterError):
            scaffold_argv("laravel", "demo", "livewire", choices=changed)
        for invalid in ("not json", "{}", help_fixture((), blank=False)):
            with self.assertRaises(AdapterError):
                discover_starter_kits(invalid)

    def test_help_failure_keeps_installer_version_and_maintenance_visible(self):
        adapter = LaravelAdapter(pathlib.Path("/home/dev"), io.StringIO())
        with patch("laravel.adapter.shutil.which", return_value="/usr/bin/composer"), \
             patch.object(adapter, "installer", return_value=pathlib.Path("/fake/laravel")), \
             patch.object(adapter, "command", side_effect=["Laravel Installer 5.33.0", "not-json"]):
            self.assertEqual(adapter.status(), {"composer": True, "installed": "5.33.0", "starter_kits": None})

    def test_official_installer_never_forces_overwrite(self):
        choices = discover_starter_kits(help_fixture())
        argv = scaffold_argv("/home/dev/.config/composer/vendor/bin/laravel", "demo-app", "livewire", choices=choices)
        self.assertEqual(argv[:3], ["/home/dev/.config/composer/vendor/bin/laravel", "new", "demo-app"])
        self.assertIn("--livewire", argv)
        self.assertIn("--no-interaction", argv)
        self.assertIn("--database=sqlite", argv)
        self.assertIn("--database=mariadb", scaffold_argv("laravel", "demo", "livewire", "mariadb", choices=choices))
        with self.assertRaises(AdapterError):
            scaffold_argv("laravel", "demo", "livewire", "other", choices=choices)
        self.assertNotIn("--force", argv)
        self.assertNotIn("--livewire", scaffold_argv("laravel", "demo", "none", choices=choices))
        for slug, kit in (("../demo", "none"), ("--force", "none"), ("demo", "--force")):
            with self.assertRaises(AdapterError):
                scaffold_argv("laravel", slug, kit, choices=choices)

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
                    adapter.scaffold("demo", "livewire", "sqlite", {"url": "https://demo.test"})
                self.assertEqual(error.exception.code, "frontend_npm")
                installer.assert_not_called()
                self.assertEqual(command.call_count, 2)
            self.assertFalse((home / "projects" / "demo").exists())

    def test_scaffold_passes_mariadb_settings_only_in_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            (home / "projects").mkdir()
            root = home / "projects" / "demo"
            adapter = LaravelAdapter(home, io.StringIO())
            settings = {"url": "https://demo.test", "db_name": "demo_db",
                        "db_user": "demo_user", "db_password": "example_token"}
            calls = []
            def fake_command(argv, **kwargs):
                calls.append((argv, kwargs))
                if "new" in argv:
                    for directory in ("vendor", "public/build"):
                        (root / directory).mkdir(parents=True)
                    for file in ("artisan", "composer.json", "public/index.php",
                                 "vendor/autoload.php", "public/build/manifest.json"):
                        (root / file).write_text("fixture")
                    (root / ".env").write_text("APP_KEY=unchanged\nDB_CONNECTION=mariadb\nDB_USERNAME=root\n")
                    (root / ".env.example").write_text("DB_PASSWORD=\n")
                return "0001_create_users ... [1] Ran" if "migrate:status" in argv else ""
            with patch.object(adapter, "require_frontend_tools"), \
                 patch.object(adapter, "installer", return_value=pathlib.Path("/fake/laravel")), \
                 patch.object(adapter, "starter_kits", return_value=discover_starter_kits(help_fixture())), \
                 patch("laravel.adapter.shutil.which", return_value="/usr/bin/php"), \
                 patch.object(adapter, "command", side_effect=fake_command):
                self.assertEqual(adapter.scaffold("demo", "livewire", "mariadb", settings),
                                 {"scaffolded": True, "driver": "mariadb"})
            self.assertIn("--database=mariadb", calls[0][0])
            self.assertNotIn("example_token", " ".join(calls[0][0]))
            self.assertEqual(calls[0][1]["env"]["DB_PASSWORD"], "example_token")
            self.assertEqual(calls[0][1]["env"]["DB_DATABASE"], "demo_db")
            self.assertNotIn("DB_PASSWORD", calls[1][1]["env"])
            self.assertIn("DB_PASSWORD=example_token", (root / ".env").read_text())
            self.assertNotIn("example_token", (root / ".env.example").read_text())
            self.assertFalse(any("migrate" in argv for argv, _ in calls))

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
                 patch.object(adapter, "starter_kits", return_value=discover_starter_kits(help_fixture())), \
                 patch.object(adapter, "command", side_effect=fail):
                with self.assertRaises(AdapterError):
                    adapter.scaffold("demo", "livewire", "sqlite", {"url": "https://demo.test"})
            self.assertTrue((root / "partial").is_file())


if __name__ == "__main__":
    unittest.main()
