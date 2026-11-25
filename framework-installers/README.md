# Framework installers

DevStack prepares environments using the official framework packages and CLI
tools. Adapters never run database migrations or choose application-level
configuration on the developer's behalf. There are no bundled starter templates.

## Structure and adding another framework

- catalog.json: root-owned catalog of supported frameworks and variants.
- registry.py: explicit mapping to reviewed adapters.
- runner.py: allowlisted unprivileged entry point.
- common.py: shared non-sensitive adapter error contract.
- laravel/: Laravel implementation and its specific documentation.

Add one directory per framework, document its requirements and explicit
preparation commands, register its adapter and catalog entry, include its
files in both install/update allowlists, and add unit/real-tool CI tests.
Do not allow browser-supplied executables, modules, paths or package names.

## Contract

Actions: status, check, install-tool, update-tool, scaffold, configure.
Workers run as the configured Linux developer, not as root or devstackweb.
The root-owned coordinator reserves one operation, validates options,
prepares hosting only after successful scaffold, and finishes after configure.
No automatic migrations, seeders, queues or development servers. No automatic
retry or removal of partial sources. The original no-engine workflow remains.

Tool installation/updates require an authenticated panel. Tool output stays
in the developer's private log; generated database credentials reach the
configure worker over private stdin, not argv or job state. A successful
environment preparation means code and infrastructure are ready, not that
the application schema or business features have been configured.

## Acceptance

CI tests both official Laravel variants and verifies empty SQLite, a private
.env, commented MariaDB settings, assets, and refusal of existing paths.
Actual web/daemon integration, WSL sandbox, MariaDB, Nginx, HTTPS, Windows
domain sync, failure recovery and update must also be verified locally.
See [Laravel](laravel/README.md) for its specific setup.
