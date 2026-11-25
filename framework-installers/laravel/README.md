# Laravel

## Official tool

DevStack uses the official Laravel Installer from Composer's global package
`laravel/installer` for the developer. Settings can check, install and update
the tool. There is no custom fork, patched vendor file or duplicated installer
implementation. PHP CLI, Composer, Node.js 20.19+ (20.x) or 22.12+, and npm 11+
are required.

The adapter invokes the ordinary official command in the Linux projects
directory with a fresh identifier and reviewed non-interactive options:

```sh
laravel new example --no-interaction --no-ansi --database=sqlite --pest --npm --no-boost
laravel new example --no-interaction --no-ansi --database=mariadb --pest --npm --no-boost --livewire
```

## Discovering starter kits

The dropdown is not a maintained list of kit names. The adapter invokes the
installed binary as the developer:

```sh
laravel help new --format=json --no-interaction --no-ansi
```

Symfony Console supplies the command definition. The adapter recognizes
value-less switches described by the installer as "Install the ... Starter Kit"
and offers Blade (no kit), the recognized first-party kits and their blank
variants when the official `--no-authentication` switch is available. In
Installer 5.33.0 this yields React, Svelte, Vue and Livewire, plus the four
without application authentication. These are different from the dashboard's
authentication setting. Status is read when opening/selecting the framework
form, manually refreshing it, and after an installer job; the server and
worker validate the choice again before installation. There is no periodic
background discovery or network lookup for this list.

The JSON help is official, but its descriptions are not a dedicated stable
starter-kit schema. If the option metadata changes incompatibly, discovery
fails closed while the installer version and maintenance remain available.
Do not forward arbitrary flags or package URLs. Community starter kits
(`--using`) are unbounded external package choices and are not automatically
listed.

## Installer options

The adapter publishes conditional fields only when the installed CLI exposes
the corresponding switches. Symfony's help does not describe interactive
dependencies, so the Laravel adapter mirrors the reviewed upstream rules
(v5.33.0); it never parses terminal screens or patches vendor code.

- Authenticated React/Svelte/Vue/Livewire: built-in Laravel authentication
  (default) or WorkOS (`--workos`, requires a separate WorkOS account and
  configuration for functional sign-in).
- Livewire with built-in authentication: single-file components (default)
  or stand-alone classes (`--livewire-class-components`).
- Built-in authenticated kits with single-file components when applicable:
  optional Teams (`--teams`). The official interactive installer does not
  ask for Teams with WorkOS, blank kits or class-based Livewire.
- Testing: Pest (default) or PHPUnit.
- Laravel Boost: optional, off by default.
- Install/build npm dependencies: on by default, optional skip
  (`--no-node`); when skipped the environment is registered but the developer
  must build assets before using pages that require them. Other package
  managers are not managed by this adapter. No external repository creation,
  arbitrary community packages or development-release flags are exposed.

A newly discovered frontend kit is listed automatically; advanced
branch-specific capabilities are restricted to reviewed compatible stacks.
New interactive dependencies may require an adapter update. The original
Laravel CLI handles all selected steps. The adapter checks requested
dependencies, team migration files/class components where applicable, actual
migration status and the asset manifest when the build was requested. No
extra Composer installation or migration is added by DevStack.

The public webroot is always `public` for Laravel, supplied by the
root-owned framework catalog and verified after scaffolding. The panel
does not let users override the framework's public directory.

## Standard migrations


The Laravel Installer v5.33.0 does not provide a no-migrations option. Its
normal initial migrations and Composer hooks are therefore retained, on the
database selected in the panel. DevStack does not run artisan migrate or
seed commands separately. A read-only migration-status check verifies the
installer result.

## Database choice

- SQLite: no MariaDB database/account is provisioned. Laravel creates and
  migrates its default local SQLite file.
- MariaDB: DevStack generates an isolated database and user first. The
  worker receives the credentials through private stdin and supplies the
  DB_* process environment to the official installer and its child
  processes, including early Composer hooks. The installer performs its
  normal migrations on that database. The adapter persists the same
  connection and APP_URL in the private .env before publishing hosting.

Example (placeholders only):

```dotenv
DB_CONNECTION=mariadb
DB_HOST=127.0.0.1
DB_PORT=3306
DB_DATABASE=your_database
DB_USERNAME=your_user
DB_PASSWORD=your_password
```

Real values are generated per project and written only to .env and protected
DevStack credential storage, never .env.example. The original installer
configuration, dependency resolution, feature selection and asset build are
not reimplemented. All subsequent development, migrations and application
configuration belong to the developer.

## Failure and maintenance

Existing source directories are never overwritten; do not use --force.
On failure retain partial files. A successfully created but unregistered
database reservation can be rolled back once the worker is confirmed stopped.
An interrupted or otherwise uncertain reservation is kept in the root-only
`/var/lib/devstack/engine-db-reservation.json` and requires administrator
reconciliation before another MariaDB installation. Do not print the file
or automatically drop a database based only on its name. Registered
projects keep their own database/account. Updating the installer does not
update existing projects.

Upstream: [Laravel Installer](https://github.com/laravel/installer),
[official installation documentation](https://laravel.com/docs/13.x/installation).
