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

The variants are plain Laravel and the official Livewire Starter Kit.
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
