# Framework installer adapters (work in progress)

DevStack manages the project lifecycle; adapters delegate scaffolding and tool
maintenance to the framework's official CLI. An engine is not a template
bundled by DevStack. No framework package is executed as root.

## Contract

The root-owned coordinator selects an allowlisted engine and action. An
unprivileged, short-lived worker runs the adapter as the configured developer,
with the developer's home and Linux projects directory. The public API accepts
typed options, never commands, flags, paths, or arbitrary package names.

Actions: status, check, install-tool, update-tool, scaffold, configure, migrate.
The runner emits a small JSON result or a bounded error. Command diagnostics
remain in a private developer-owned log; no database credentials in argv,
journal, API responses or shared logs. Composer and npm can run third-party
code, so these actions require explicit authenticated user interaction.

Laravel uses the globally installed Composer package laravel/installer.
Resolve its executable using the developer's Composer global bin directory,
not the root account or the web worker's PATH. Use the official "laravel new"
command with a previously absent project directory. Never use --force.

The Laravel adapter supports plain Laravel and the first-party Livewire Starter
Kit. Use SQLite for the installer's initial migrations, then configure the
project for the database provisioned by DevStack if selected. Build assets with
npm; do not launch a development server. The infrastructure coordinator must
grant the public directory ACL after scaffolding and preserve existing project
metadata and the current no-engine creation path.

Status is local, checking latest is explicit, and updating the installer is
scoped to its Composer package/dependencies. It does not upgrade existing
Laravel applications. A major-version constraint change requires a deliberate
separate decision.

## Integration still required

- Authenticated new-project dropdown and conditional starter-kit options.
- A durable job lifecycle outside the request timeout; no idle polling or new
  resident watcher. Reserve the slug and domain, serialize conflicting work,
  preserve partial files, and report failure honestly.
- Root provisioning only after successful scaffolding; pass database settings
  via private stdin to the unprivileged adapter after provisioning. Mark ready
  only after the application configuration and migration checks succeed.
- Settings UI for tool status, install, check and update.
- Package the root-owned adapter code in both fresh installs and updates.
- Mock-based tests, CI, and a real isolated WSL acceptance test.
