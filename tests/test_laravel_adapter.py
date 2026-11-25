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
    rewrite_environment, scaffold_argv, tool_argv, require_supported_frontend, discover_starter_kits, discover_capabilities,
)


def help_fixture(kits=("react", "svelte", "vue", "livewire"), *, blank=True, extra=True):
    def option(name, description="", accepts=False):
        return {"name": "--" + name, "description": description,
                "accept_value": accepts, "is_multiple": False, "default": False}
    options = {name: option(name) for name in ("database", "pest", "npm", "no-boost")}
    options.update({name: option(name, "Install the " + name.capitalize() + " Starter Kit") for name in kits})
    options["force"] = option("force", "Install the Dangerous Starter Kit")
    options["using"] = option("using", "Install the Custom Starter Kit", accepts=True)
    if blank:
        options["no-authentication"] = option("no-authentication")
    if extra:
        for name in ("workos", "teams", "livewire-class-components", "phpunit", "boost", "no-node"):
            options[name] = option(name)
    return json.dumps({"name": "new", "definition": {"options": options}})


class LaravelAdapterTests(unittest.TestCase):
    def test_discovery_tracks_installed_help_without_static_list(self):
        choices = discover_starter_kits(help_fixture())
        self.assertEqual(set(choices), {"none", "react", "svelte", "vue", "livewire"})
        self.assertEqual([item["label"] for item in choices.values()],
                         ["Blade", "React", "Svelte", "Vue", "Livewire"])
        self.assertEqual(choices["vue"]["flags"], ["--vue"])
        changed = discover_starter_kits(help_fixture(("future-kit", "react"), blank=False))
        self.assertEqual(set(changed), {"none", "future-kit", "react"})
        self.assertIn("--future-kit", scaffold_argv("laravel", "demo", "future-kit", choices=changed))
        with self.assertRaises(AdapterError):
            scaffold_argv("laravel", "demo", "livewire", choices=changed)
        for invalid in ("not json", "{}", help_fixture((), blank=False)):
            with self.assertRaises(AdapterError):
                discover_starter_kits(invalid)

    def test_conditional_options_match_official_prompts(self):
        from common import resolve_installer_options
        kits, fields = discover_capabilities(help_fixture())
        def argv(kit, selected):
            return scaffold_argv("laravel", "demo", kit, choices=kits, fields=fields, selected=selected)
        self.assertEqual(resolve_installer_options("livewire", fields, {}),
                         {"auth-provider": "laravel", "livewire-components": "single-file", "teams": "no",
                          "testing": "pest", "boost": "no", "assets": "yes"})
        self.assertIn("--livewire-class-components", argv("livewire", {"livewire-components": "class"}))
        self.assertIn("--teams", argv("livewire", {"teams": "yes"}))
        self.assertIn("--teams", argv("react", {"teams": "yes"}))
        self.assertIn("--workos", argv("livewire", {"auth-provider": "workos"}))
        for kit in ("react", "svelte", "vue", "livewire"):
            with self.subTest(kit=kit):
                command = argv(kit, {"auth-provider": "none"})
                self.assertIn("--" + kit, command)
                self.assertIn("--no-authentication", command)
                self.assertNotIn("--workos", command)
                self.assertNotIn("--teams", command)
                self.assertNotIn("--livewire-class-components", command)
                self.assertEqual(resolve_installer_options(kit, fields, {"auth-provider": "none"}),
                                 {"auth-provider": "none", "testing": "pest", "boost": "no", "assets": "yes"})
                self.assertNotIn("--no-authentication", argv(kit, {}))
                with self.assertRaises(AdapterError):
                    argv("blank-" + kit, {})
        no_blank_kits, no_blank_fields = discover_capabilities(help_fixture(blank=False))
        with self.assertRaises(AdapterError):
            scaffold_argv("laravel", "demo", "livewire", choices=no_blank_kits,
                          fields=no_blank_fields, selected={"auth-provider": "none"})
        basic_kits, basic_fields = discover_capabilities(help_fixture(extra=False))
        self.assertEqual(resolve_installer_options("react", basic_fields, {}), {"auth-provider": "laravel"})
        self.assertIn("--no-authentication", scaffold_argv("laravel", "demo", "react", choices=basic_kits,
                          fields=basic_fields, selected={"auth-provider": "none"}))
        with self.assertRaises(AdapterError):
            scaffold_argv("laravel", "demo", "react", choices=basic_kits,
                          fields=basic_fields, selected={"auth-provider": "workos"})
        self.assertIn("--phpunit", argv("none", {"testing": "phpunit"}))
        self.assertIn("--boost", argv("none", {"boost": "yes"}))
        self.assertIn("--no-node", argv("none", {"assets": "no"}))
        self.assertNotIn("--npm", argv("none", {"assets": "no"}))
        for kit, selected in (
            ("livewire", {"livewire-components": "class", "teams": "yes"}),
            ("livewire", {"auth-provider": "workos", "teams": "yes"}),
            ("livewire", {"auth-provider": "workos", "livewire-components": "class"}),
            ("livewire", {"auth-provider": "none", "teams": "yes"}),
            ("livewire", {"auth-provider": "none", "livewire-components": "class"}),
            ("react", {"auth-provider": "none", "teams": "yes"}),
            ("none", {"auth-provider": "none"}), ("none", {"auth-provider": "workos"}),
            ("vue", {"livewire-components": "class"}), ("react", {"testing": "--force"}),
            ("react", {"force": "yes"}), ("react", {"teams": []}),
        ):
            with self.subTest(kit=kit, selected=selected), self.assertRaises(AdapterError):
                argv(kit, selected)
        old_kits, old_fields = discover_capabilities(help_fixture(blank=False, extra=False))
        self.assertEqual(old_fields, [])
        with self.assertRaises(AdapterError):
            scaffold_argv("laravel", "demo", "livewire", choices=old_kits, fields=old_fields,
                          selected={"teams": "yes"})

    def test_help_failure_keeps_installer_version_and_maintenance_visible(self):
        adapter = LaravelAdapter(pathlib.Path("/home/dev"), io.StringIO())
        with patch("laravel.adapter.shutil.which", return_value="/usr/bin/composer"), \
             patch.object(adapter, "installer", return_value=pathlib.Path("/fake/laravel")), \
             patch.object(adapter, "command", side_effect=["Laravel Installer 5.33.0", "not-json"]):
            self.assertEqual(adapter.status(), {"composer": True, "installed": "5.33.0", "starter_kits": None, "installer_options": None})

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
                 patch.object(adapter, "installer", return_value=pathlib.Path("/fake/laravel")), \
                 patch.object(adapter, "capabilities", return_value=discover_capabilities(help_fixture())):
                with self.assertRaises(AdapterError) as error:
                    adapter.scaffold("demo", "livewire", "sqlite", {"url": "https://demo.test"})
                self.assertEqual(error.exception.code, "frontend_npm")
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
                    (root / "composer.lock").write_text(json.dumps({"packages": [], "packages-dev": [{"name": "pestphp/pest"}]}))
                    (root / ".env").write_text("APP_KEY=unchanged\nDB_CONNECTION=mariadb\nDB_USERNAME=root\n")
                    (root / ".env.example").write_text("DB_PASSWORD=\n")
                return "0001_create_users ... [1] Ran" if "migrate:status" in argv else ""
            with patch.object(adapter, "require_frontend_tools"), \
                 patch.object(adapter, "installer", return_value=pathlib.Path("/fake/laravel")), \
                 patch.object(adapter, "capabilities", return_value=discover_capabilities(help_fixture())), \
                 patch("laravel.adapter.shutil.which", return_value="/usr/bin/php"), \
                 patch.object(adapter, "command", side_effect=fake_command):
                self.assertEqual(adapter.scaffold("demo", "livewire", "mariadb", settings),
                                 {"scaffolded": True, "driver": "mariadb", "webroot": "public",
                                  "installer_options": {"auth-provider": "laravel", "livewire-components": "single-file",
                                                        "teams": "no", "testing": "pest", "boost": "no", "assets": "yes"}})
            self.assertIn("--database=mariadb", calls[0][0])
            self.assertNotIn("example_token", " ".join(calls[0][0]))
            self.assertEqual(calls[0][1]["env"]["DB_PASSWORD"], "example_token")
            self.assertEqual(calls[0][1]["env"]["DB_DATABASE"], "demo_db")
            self.assertNotIn("DB_PASSWORD", calls[1][1]["env"])
            self.assertIn("DB_PASSWORD=example_token", (root / ".env").read_text())
            self.assertNotIn("example_token", (root / ".env.example").read_text())
            self.assertFalse(any("migrate" in argv for argv, _ in calls))

    def test_local_mail_is_opt_in_and_uses_only_fixed_loopback(self):
        original = "APP_KEY=keep\nMAIL_URL=smtp://old.example.test\nMAIL_MAILER=log\nMAIL_HOST=external.example.test\nMAIL_PASSWORD=fixture\n"
        base = {"url": "https://demo.test"}
        self.assertEqual(environment_values(base), {"APP_URL": "https://demo.test"})
        self.assertEqual(environment_values({**base, "local_mail": False}), environment_values(base))
        values = environment_values({**base, "local_mail": True})
        self.assertEqual(values["MAIL_HOST"], "127.0.0.1")
        self.assertEqual(values["MAIL_PORT"], "1025")
        self.assertEqual(values["MAIL_FROM_ADDRESS"], "hello@demo.test")
        self.assertEqual(values["MAIL_URL"], "null")
        updated = rewrite_environment(original, values)
        self.assertIn("APP_KEY=keep", updated)
        self.assertIn("MAIL_MAILER=smtp", updated)
        self.assertIn("MAIL_PASSWORD=null", updated)
        self.assertNotIn("external.example.test", updated)
        self.assertNotIn("old.example.test", updated)
        for bad in ("yes", 1, None, {}, []):
            with self.assertRaises(AdapterError):
                environment_values({**base, "local_mail": bad})
        with self.assertRaises(AdapterError):
            environment_values({**base, "local_mail": True, "mail_host": "external.example.test"})

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
                 patch.object(adapter, "capabilities", return_value=discover_capabilities(help_fixture())), \
                 patch.object(adapter, "command", side_effect=fail):
                with self.assertRaises(AdapterError):
                    adapter.scaffold("demo", "livewire", "sqlite", {"url": "https://demo.test"})
            self.assertTrue((root / "partial").is_file())


if __name__ == "__main__":
    unittest.main()
