# Framework installers

DevStack delegates project creation to each framework's official installer,
rather than maintaining its own starter templates or reproducing installer
hooks. Keep upstream default behavior, including any initial migrations it
requires. DevStack never adds migrations, seeders, queue workers or development
servers of its own.

## Structure and adding another framework

- catalog.json: root-owned catalog of supported frameworks and variants.
- registry.py: explicit mapping to reviewed adapters.
- runner.py: allowlisted unprivileged entry point.
- common.py: shared non-sensitive adapter error contract.
- laravel/: Laravel implementation and its specific documentation.

Add one directory per framework, document its requirements, supported
database choices, official installer arguments and maintenance commands,
register the adapter and catalog entry, include its files in both
install/update allowlists, and add unit and real-tool CI tests.
Never accept browser-supplied executables, paths, environment variables,
modules or package names.

## Contract

Actions: status, check, install-tool, update-tool, scaffold, configure.
Workers run as the configured Linux developer, not root or devstackweb.
The root coordinator reserves a single job; if MariaDB is selected, it
creates the isolated database/account before the installer starts.
Credentials reach the worker through private stdin and the child environment,
never in command arguments or public job state. The adapter prepares the
private .env, verifies the result, and returns a small JSON response.
Only then does DevStack publish the Nginx/PHP-FPM configuration and domain.

Tool installation/updates and framework creation require an authenticated
panel or explicit root-controlled consent for passwordless local use.
See [Security](../SECURITY.md) for the one-time opt-in/revocation commands. Tool output is stored in a private developer log. Do not copy
credentials into .env.example or public logs. Partial sources are retained
after failures; a fresh, known unregistered database reservation may be
rolled back. An uncertain reservation is retained for administrator review.
The original no-engine workflow remains unchanged in the form.

## Acceptance

The installer smoke workflow covers plain Laravel and Livewire, with both
SQLite and a real isolated MariaDB service. It checks initial migration
status, .env, private permissions, built assets, and refusal of existing
directories and extra migration operations. WSL-specific web/daemon
integration, Nginx, HTTPS, Windows domain sync and interrupted jobs also
require local verification.

See [Laravel](laravel/README.md) for the precise workflow.
