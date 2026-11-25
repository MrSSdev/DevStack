# Framework installer adapters (work in progress)

DevStack manages the project lifecycle; adapters delegate scaffolding and tool
maintenance to the framework's official CLI. An engine is not a template
bundled by DevStack. No framework package is executed as root.

## Adding an engine

The root-owned catalog describes the label, tool, document root and allowed
installation variants. The UI and API use its registered identifiers. Add a
separate adapter directory, explicitly register its class in registry.py, add
its catalog entry, include the files in the trusted installation/update
package, and add tests. Do not resolve arbitrary Python module names or CLI
arguments from a browser request.

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

## Integration and acceptance (draft)

- The new-project form includes the engine selector and starter kit. Laravel
  requires the public document root and an authenticated panel (not the
  passwordless mode). No-engine projects retain the original workflow.
- A single root-owned job record reserves conflicting operations, and the daemon
  runs only developer-identity workers outside the HTTP request. The daemon
  checks for completion while a job is active, not when idle. Restart records
  an interrupted job without automatically retrying or deleting files.
- Root provisioning follows successful scaffolding; the configure worker reads
  the generated DB settings through private stdin. The migration worker runs
  as the developer. Failure preserves partial files and any already-registered
  project, marking its engine status as failed instead of ready.
- Settings exposes explicit local version, check, install and update operations.
- Both fresh installation and update package the root-owned adapter code.
- Required before release: CI plus a real isolated WSL test of the worker
  identity, constraints, complete workflow, Composer and npm versions, TLS,
  database configuration, failure/restart handling and no-engine regression.
