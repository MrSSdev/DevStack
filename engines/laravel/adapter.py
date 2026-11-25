"""Laravel's official installer; this module must run as the developer."""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
from typing import Any, TextIO

PACKAGE = "laravel/installer"
SLUG = re.compile(r"[a-z][a-z0-9-]{1,39}\Z")
VERSION = re.compile(r"v?[0-9]+\.[0-9]+\.[0-9]+\Z")
DB_IDENT = re.compile(r"[a-z0-9_]{1,64}\Z")
DB_SECRET = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
URL = re.compile(r"https?://[a-z][a-z0-9-]{0,39}\.test\Z")


from common import AdapterError


def version(value: Any) -> str | None:
    return value.lstrip("v") if isinstance(value, str) and VERSION.fullmatch(value) else None


def require_supported_frontend(node: str, npm: str) -> None:
    """DevStack's supported frontend baseline, checked before creating files.

    Current official starter kits use Vite and npm alias overrides. Ubuntu's
    npm 9.2.0 failed on npm:@voidzero-dev/vite-plus-core during the WSL smoke
    test. npm 11.19.1 passed the official installer CI. Do not patch the kit
    or silently replace system tools.
    """
    node_version = version(node)
    npm_version = version(npm)
    if node_version is None:
        raise AdapterError("Cannot determine Node.js version.", "frontend_node")
    node_parts = tuple(map(int, node_version.split(".")))
    if not ((node_parts[0] == 20 and node_parts >= (20, 19, 0)) or node_parts >= (22, 12, 0)):
        raise AdapterError("Use Node.js 20.19+ (20.x) or 22.12+ for Laravel assets.", "frontend_node")
    if npm_version is None or int(npm_version.split(".")[0]) < 11:
        raise AdapterError("DevStack requires npm 11+ for Laravel. Update npm before creating a project.", "frontend_npm")


def project_path(projects: pathlib.Path, slug: str, *, exists: bool) -> pathlib.Path:
    if not isinstance(slug, str) or not SLUG.fullmatch(slug):
        raise AdapterError("Invalid project identifier.")
    if projects.is_symlink() or not projects.is_dir():
        raise AdapterError("The developer projects directory is unavailable.")
    root = projects / slug
    if root.is_symlink():
        raise AdapterError("Refusing a symbolic-link project directory.")
    if exists and not root.is_dir():
        raise AdapterError("Project directory does not exist.")
    if not exists and root.exists():
        raise AdapterError("Project directory already exists; nothing overwritten.")
    return root


def scaffold_argv(installer: str, slug: str, kit: str) -> list[str]:
    if not isinstance(slug, str) or not SLUG.fullmatch(slug):
        raise AdapterError("Invalid project identifier.")
    if kit not in ("none", "livewire"):
        raise AdapterError("Unsupported Laravel starter kit.")
    args = [installer, "new", slug, "--no-interaction", "--no-ansi",
            "--database=sqlite", "--pest", "--npm", "--no-boost"]
    if kit == "livewire":
        args.append("--livewire")
    return args


def tool_argv(composer: str, action: str) -> list[str]:
    if action == "install-tool":
        return [composer, "global", "require", PACKAGE, "--no-interaction",
                "--no-ansi", "--no-progress", "--prefer-dist"]
    if action == "update-tool":
        return [composer, "global", "update", PACKAGE, "--with-dependencies",
                "--no-interaction", "--no-ansi", "--no-progress", "--prefer-dist"]
    raise AdapterError("Unsupported tool operation.")


def rewrite_environment(original: str, values: dict[str, str]) -> str:
    """Replace only known keys (including commented defaults), preserving the rest."""
    result = []
    written = set()
    for line in original.splitlines():
        match = re.match(r"^\s*(?:#\s*)?([A-Z_][A-Z0-9_]*)\s*=", line)
        key = match.group(1) if match else None
        if key in values:
            if key not in written:
                result.append(f"{key}={values[key]}")
                written.add(key)
        else:
            result.append(line)
    for key, value in values.items():
        if key not in written:
            result.append(f"{key}={value}")
    return "\n".join(result) + "\n"


def environment_values(settings: Any) -> dict[str, str]:
    if not isinstance(settings, dict) or not isinstance(settings.get("url"), str) or not URL.fullmatch(settings["url"]):
        raise AdapterError("Invalid application URL.")
    if set(settings) not in ({"url"}, {"url", "db_name", "db_user", "db_password"}):
        raise AdapterError("Invalid application configuration.")
    values = {"APP_URL": settings["url"]}
    if "db_name" in settings:
        for key, pattern in (("db_name", DB_IDENT), ("db_user", DB_IDENT), ("db_password", DB_SECRET)):
            if not isinstance(settings.get(key), str) or not pattern.fullmatch(settings[key]):
                raise AdapterError("Invalid database configuration.")
        values.update(DB_CONNECTION="mariadb", DB_HOST="127.0.0.1", DB_PORT="3306",
                      DB_DATABASE=settings["db_name"], DB_USERNAME=settings["db_user"],
                      DB_PASSWORD=settings["db_password"])
    return values


class LaravelAdapter:
    def __init__(self, home: pathlib.Path, log: TextIO):
        self.home = home
        self.projects = home / "projects"
        self.log = log

    def command(self, argv: list[str], *, cwd: pathlib.Path | None = None,
                capture: bool = False, timeout: int = 900) -> str:
        # No shell, no arbitrary user arguments. Child output stays in a
        # developer-owned private log, not the panel or privileged daemon.
        try:
            result = subprocess.run(argv, cwd=cwd or self.home, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE if capture else self.log,
                                    stderr=self.log, text=True, timeout=timeout,
                                    check=True, close_fds=True)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            raise AdapterError("Official tool failed; inspect the private developer log.") from None
        return (result.stdout or "").strip()

    def composer(self) -> str:
        path = shutil.which("composer")
        if not path:
            raise AdapterError("Composer is not installed or not available to the worker.")
        return path

    def installer(self, *, required: bool = True) -> pathlib.Path | None:
        raw = self.command([self.composer(), "global", "config", "bin-dir", "--absolute",
                            "--no-interaction", "--no-ansi"], capture=True, timeout=15)
        directory = pathlib.Path(raw)
        if not directory.is_absolute() or "\n" in raw:
            raise AdapterError("Composer did not return a valid global binary directory.")
        binary = directory / "laravel"
        if binary.is_file() and os.access(binary, os.X_OK):
            return binary
        if required:
            raise AdapterError("Laravel Installer is not installed.")
        return None

    def status(self) -> dict[str, Any]:
        if not shutil.which("composer"):
            return {"composer": False, "installed": None}
        installer = self.installer(required=False)
        if installer is None:
            return {"composer": True, "installed": None}
        raw = self.command([str(installer), "--version", "--no-ansi"], capture=True, timeout=15)
        match = re.fullmatch(r"Laravel Installer (v?[0-9]+\.[0-9]+\.[0-9]+)", raw)
        if not match:
            raise AdapterError("Could not determine the installed Laravel Installer version.")
        return {"composer": True, "installed": version(match.group(1))}

    def check(self) -> dict[str, Any]:
        current = self.status()
        if current["installed"] is None:
            return current
        raw = self.command([self.composer(), "global", "show", PACKAGE, "--latest",
                            "--format=json", "--no-interaction", "--no-ansi"],
                           capture=True, timeout=120)
        try:
            latest = version(json.loads(raw).get("latest"))
        except (ValueError, AttributeError):
            latest = None
        if latest is None:
            raise AdapterError("Composer did not return a stable latest version.")
        return {**current, "latest": latest}

    def maintain(self, action: str) -> dict[str, Any]:
        current = self.status()
        if action == "install-tool" and current["installed"] is not None:
            raise AdapterError("Installer already installed; use update instead.")
        if action == "update-tool" and current["installed"] is None:
            raise AdapterError("Installer not installed; install it first.")
        self.command(tool_argv(self.composer(), action))
        result = self.status()
        if result["installed"] is None:
            raise AdapterError("Installer version was not confirmed after Composer completed.")
        return result

    def require_frontend_tools(self) -> None:
        node = shutil.which("node")
        npm = shutil.which("npm")
        if node is None:
            raise AdapterError("Node.js is missing from the worker PATH.", "frontend_node")
        if npm is None:
            raise AdapterError("npm is missing from the worker PATH.", "frontend_npm")
        try:
            node_version = self.command([node, "--version"], capture=True, timeout=15)
        except AdapterError:
            raise AdapterError("Unable to run Node.js.", "frontend_node") from None
        try:
            npm_version = self.command([npm, "--version"], capture=True, timeout=15)
        except AdapterError:
            raise AdapterError("Unable to run npm.", "frontend_npm") from None
        require_supported_frontend(node_version, npm_version)

    def scaffold(self, slug: str, kit: str) -> dict[str, Any]:
        root = project_path(self.projects, slug, exists=False)
        self.require_frontend_tools()
        binary = self.installer()
        self.command(scaffold_argv(str(binary), slug, kit), cwd=self.projects)
        project_path(self.projects, slug, exists=True)
        public = root / "public"
        if public.is_symlink() or not public.is_dir():
            raise AdapterError("Laravel public directory is missing or unsafe.")
        for rel in ("artisan", "composer.json", "public/index.php",
                    "vendor/autoload.php", "public/build/manifest.json"):
            target = root / rel
            if target.is_symlink() or not target.is_file():
                raise AdapterError("Expected Laravel application or built assets are missing.")
        return {"scaffolded": True}

    def configure(self, slug: str, settings: dict[str, Any]) -> dict[str, Any]:
        values = environment_values(settings)
        root = project_path(self.projects, slug, exists=True)
        env = root / ".env"
        if env.is_symlink() or not env.is_file() or env.stat().st_size > 1024 * 1024:
            raise AdapterError("Laravel environment file is missing or unsafe.")
        updated = rewrite_environment(env.read_text(encoding="utf-8"), values)
        # This operation runs with the developer UID, never as root.
        temp = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                             dir=root, prefix=".env-devstack-",
                                             delete=False) as handle:
                temp = pathlib.Path(handle.name)
                handle.write(updated)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, env)
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)
        return {"configured": True}

    def migrate(self, slug: str) -> dict[str, Any]:
        root = project_path(self.projects, slug, exists=True)
        php = shutil.which("php")
        if not php:
            raise AdapterError("PHP CLI not available.")
        self.command([php, "artisan", "config:clear", "--no-interaction", "--no-ansi"], cwd=root)
        self.command([php, "artisan", "migrate", "--force", "--no-interaction", "--no-ansi"], cwd=root)
        return {"migrated": True}
