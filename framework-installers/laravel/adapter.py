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


from common import AdapterError, resolve_installer_options


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


KIT_FLAG = re.compile(r"[a-z][a-z0-9-]{1,28}\Z")
KIT_DESCRIPTION = re.compile(r"Install the ([A-Za-z0-9][A-Za-z0-9 .+/#-]{0,39}) Starter Kit\.?", re.I)
# Never interpret operational flags as a starter-kit selection, even if
# upstream changes a description. The browser cannot supply arguments.
NON_KIT_FLAGS = frozenset({
    "none", "force", "using", "dev", "git", "branch", "github",
    "organization", "database", "workos", "teams", "no-authentication",
    "livewire-class-components", "pest", "phpunit", "npm", "pnpm", "bun",
    "yarn", "no-node", "boost", "no-boost", "help", "quiet", "verbose",
    "version", "ansi", "no-ansi", "no-interaction", "format",
})


def discover_capabilities(raw: str) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Read Symfony's official JSON help, not the interactive screen or files.

    There is no dedicated upstream starter-kit enumeration endpoint. A
    first-party kit is a value-less option described as "Install the ... Starter
    Kit". If the metadata contract changes, fail closed rather than guessing
    flags or silently presenting a stale list.
    """
    try:
        if len(raw) > 65536:
            raise ValueError("oversized help")
        document = json.loads(raw)
        if document["name"] != "new":
            raise ValueError("unexpected command")
        options = document["definition"]["options"]
        if not isinstance(options, dict) or not {"database", "pest", "npm", "no-boost"} <= options.keys():
            raise ValueError("missing installer metadata")
    except (KeyError, TypeError, ValueError):
        raise AdapterError("Unable to discover starter kits from the official installer.", "starter_kits") from None

    def switch(name: str, option: Any) -> bool:
        return (isinstance(option, dict) and option.get("name") == "--" + name
                and option.get("accept_value") is False
                and option.get("is_multiple") is False
                and option.get("default") is False)

    found: dict[str, dict[str, Any]] = {}
    for name, option in options.items():
        if (not isinstance(name, str) or not KIT_FLAG.fullmatch(name)
                or name in NON_KIT_FLAGS or name.startswith(("no-", "blank-"))
                or not switch(name, option)):
            continue
        description = option.get("description")
        match = KIT_DESCRIPTION.fullmatch(description) if isinstance(description, str) else None
        if match:
            found[name] = {"label": match.group(1), "flags": ["--" + name]}
    if not found or len(found) > 16:
        raise AdapterError("Unable to discover starter kits from the official installer.", "starter_kits")

    # One entry per frontend technology. Authentication belongs to the
    # conditional options, not to synthetic duplicate starter-kit IDs.
    result: dict[str, dict[str, Any]] = {"none": {"label": "Blade", "flags": []}}
    result.update(found)
    # CLI help gives available switches, not the interactive dependency graph.
    # The graph below mirrors reviewed upstream Laravel prompts. New frontend
    # kits still appear automatically; advanced branch-specific settings are
    # exposed for the first-party stacks whose combinations have been reviewed.
    fields: list[dict[str, Any]] = []
    authenticated = [name for name in ("react", "svelte", "vue", "livewire") if name in found]
    def available(name: str) -> bool:
        return switch(name, options.get(name))

    def field(name: str, label: str, default: str, choices: list[tuple[str, str]],
              kits: list[str] | None = None, when: dict[str, list[str]] | None = None) -> None:
        fields.append({"id": name, "label": label, "default": default,
                       "choices": [{"id": key, "label": title} for key, title in choices],
                       "kits": kits or [], "when": when or {}})

    auth_when = {}
    auth_choices = [("laravel", "Wbudowane Laravel")]
    if available("no-authentication"):
        auth_choices.append(("none", "Bez logowania"))
    if available("workos"):
        auth_choices.append(("workos", "WorkOS (wymaga konta)"))
    if authenticated and len(auth_choices) > 1:
        field("auth-provider", "Logowanie w aplikacji", "laravel",
              auth_choices, authenticated)
        auth_when = {"auth-provider": ["laravel"]}
    team_when = dict(auth_when)
    if "livewire" in authenticated and available("livewire-class-components"):
        field("livewire-components", "Komponenty Livewire", "single-file",
              [("single-file", "Single-file components"), ("class", "Oddzielne klasy i widoki")],
              ["livewire"], auth_when)
        team_when["livewire-components"] = ["single-file", ""]
    if authenticated and available("teams"):
        field("teams", "Obsługa zespołów (Teams)", "no",
              [("no", "Nie"), ("yes", "Tak")], authenticated, team_when)
    if available("phpunit") and available("pest"):
        field("testing", "Framework testów", "pest", [("pest", "Pest"), ("phpunit", "PHPUnit")])
    if available("boost") and available("no-boost"):
        field("boost", "Laravel Boost", "no", [("no", "Nie"), ("yes", "Tak — wsparcie AI")])
    if available("no-node") and available("npm"):
        field("assets", "Instalacja i budowanie zasobów npm", "yes",
              [("yes", "Tak — przygotuj aplikację"), ("no", "Nie — zbuduję samodzielnie")])
    return result, fields


def discover_starter_kits(raw: str) -> dict[str, dict[str, Any]]:
    return discover_capabilities(raw)[0]


def scaffold_argv(installer: str, slug: str, kit: str, driver: str = "sqlite",
                  *, choices: dict[str, dict[str, Any]],
                  fields: list[dict[str, Any]] | None = None,
                  selected: dict[str, str] | None = None) -> list[str]:
    if not isinstance(slug, str) or not SLUG.fullmatch(slug):
        raise AdapterError("Invalid project identifier.")
    if not isinstance(kit, str) or kit not in choices:
        raise AdapterError("Starter kit is unavailable in the installed Laravel version.", "starter_kit_unavailable")
    if driver not in ("sqlite", "mariadb"):
        raise AdapterError("Unsupported Laravel database driver.")
    try:
        options = resolve_installer_options(kit, fields or [], {} if selected is None else selected)
    except ValueError:
        raise AdapterError("Invalid or unavailable installer options.", "installer_options") from None
    args = [installer, "new", slug, "--no-interaction", "--no-ansi",
            "--database=" + driver,
            "--phpunit" if options.get("testing") == "phpunit" else "--pest",
            "--no-node" if options.get("assets") == "no" else "--npm",
            "--boost" if options.get("boost") == "yes" else "--no-boost",
            *choices[kit]["flags"]]
    if options.get("auth-provider") == "none":
        args.append("--no-authentication")
    elif options.get("auth-provider") == "workos":
        args.append("--workos")
    if options.get("livewire-components") == "class":
        args.append("--livewire-class-components")
    if options.get("teams") == "yes":
        args.append("--teams")
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
                capture: bool = False, timeout: int = 900,
                env: dict[str, str] | None = None) -> str:
        # No shell, no arbitrary user arguments. Child output stays in a
        # developer-owned private log, not the panel or privileged daemon.
        try:
            result = subprocess.run(argv, cwd=cwd or self.home, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE if capture else self.log,
                                    stderr=self.log, text=True, timeout=timeout, env=env,
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

    def capabilities(self, installer: pathlib.Path) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        raw = self.command([str(installer), "help", "new", "--format=json",
                            "--no-interaction", "--no-ansi"], capture=True, timeout=12)
        return discover_capabilities(raw)

    def status(self) -> dict[str, Any]:
        if not shutil.which("composer"):
            return {"composer": False, "installed": None, "starter_kits": [], "installer_options": []}
        installer = self.installer(required=False)
        if installer is None:
            return {"composer": True, "installed": None, "starter_kits": [], "installer_options": []}
        raw = self.command([str(installer), "--version", "--no-ansi"], capture=True, timeout=15)
        match = re.fullmatch(r"Laravel Installer (v?[0-9]+\.[0-9]+\.[0-9]+)", raw)
        if not match:
            raise AdapterError("Could not determine the installed Laravel Installer version.")
        # Keep maintenance available when a new upstream help format cannot
        # be classified. Do not fall back to an old hard-coded kit list.
        try:
            choices, fields = self.capabilities(installer)
            kits = [{"id": name, "label": item["label"]} for name, item in choices.items()]
        except AdapterError:
            kits = fields = None
        return {"composer": True, "installed": version(match.group(1)),
                "starter_kits": kits, "installer_options": fields}

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

    def scaffold(self, slug: str, kit: str, driver: str,
                 settings: dict[str, Any]) -> dict[str, Any]:
        root = project_path(self.projects, slug, exists=False)
        if not isinstance(settings, dict):
            raise AdapterError("Invalid application configuration.")
        settings = dict(settings)
        selected = settings.pop("installer_options", {})
        if not isinstance(selected, dict):
            raise AdapterError("Invalid installer options.", "installer_options")
        values = environment_values(settings)
        if driver not in ("sqlite", "mariadb") or ("db_name" in settings) != (driver == "mariadb"):
            raise AdapterError("Database choice does not match the supplied configuration.")
        binary = self.installer()
        choices, fields = self.capabilities(binary)
        argv = scaffold_argv(str(binary), slug, kit, driver, choices=choices, fields=fields, selected=selected)
        if "--no-node" not in argv:
            self.require_frontend_tools()
        # Laravel's own installer and its Composer hooks perform their normal
        # migrations. PHP's dotenv loader preserves process environment values.
        # Override the connection for the whole installer process, including
        # early starter-kit hooks. Never put credentials in command arguments.
        db_keys = {"DB_CONNECTION", "DB_HOST", "DB_PORT", "DB_DATABASE",
                   "DB_USERNAME", "DB_PASSWORD", "DB_URL", "DATABASE_URL",
                   "DB_SOCKET"}
        clean_env = {key: value for key, value in os.environ.items() if key not in db_keys}
        installer_env = {**clean_env, **values, "DB_CONNECTION": driver}
        self.command(argv, cwd=self.projects, env=installer_env)
        project_path(self.projects, slug, exists=True)
        public = root / "public"
        if public.is_symlink() or not public.is_dir():
            raise AdapterError("Laravel public directory is missing or unsafe.")
        required_files = ["artisan", "composer.json", "public/index.php", "vendor/autoload.php"]
        if "--no-node" not in argv:
            required_files.append("public/build/manifest.json")
        for rel in required_files:
            target = root / rel
            if target.is_symlink() or not target.is_file():
                raise AdapterError("Expected Laravel application or built assets are missing.")
        # The upstream installer may return success even if an optional
        # Composer step failed. Check requested feature dependencies without
        # re-running Composer or changing the generated application.
        expected = {"phpunit/phpunit" if "--phpunit" in argv else "pestphp/pest"}
        if "--boost" in argv:
            expected.add("laravel/boost")
        if "--workos" in argv:
            expected.add("laravel/workos")
        try:
            lock = root / "composer.lock"
            if lock.is_symlink() or lock.stat().st_size > 8 * 1024 * 1024:
                raise ValueError("unsafe composer lock")
            packages = json.loads(lock.read_text(encoding="utf-8"))
            installed = {p["name"] for p in packages.get("packages", []) + packages.get("packages-dev", [])}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            raise AdapterError("Cannot verify installed Laravel dependencies.") from None
        if not expected <= installed:
            raise AdapterError("Requested installer dependencies are missing.", "installer_options")
        if "--no-authentication" in argv and {"laravel/fortify", "laravel/workos"} & installed:
            raise AdapterError("Unexpected authentication scaffolding.", "installer_options")
        if "--teams" in argv and not any((root / "database/migrations").glob("*create_teams_table.php")):
            raise AdapterError("Requested team scaffolding is missing.", "installer_options")
        if "--livewire-class-components" in argv and not (root / "app/Livewire/Settings").is_dir():
            raise AdapterError("Requested Livewire class components are missing.", "installer_options")
        # Persist the generated connection in .env after the official tool
        # finishes. Verify the normal migrations against the actual saved
        # configuration, without executing any additional migrations.
        self.configure(slug, settings)
        php = shutil.which("php")
        if not php:
            raise AdapterError("PHP CLI not available.")
        status = self.command([php, "artisan", "migrate:status", "--no-interaction", "--no-ansi"],
                              cwd=root, env=clean_env, capture=True)
        if not re.search(r"\bRan\s*$", status, re.MULTILINE) or re.search(r"\bPending\s*$", status, re.MULTILINE):
            raise AdapterError("The official installer did not complete its default migrations.")
        return {"scaffolded": True, "driver": driver, "webroot": "public",
                "installer_options": resolve_installer_options(kit, fields, selected)}

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

