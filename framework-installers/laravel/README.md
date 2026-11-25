# Laravel

## Official tools and packages

- Composer: prepares `laravel/laravel` (13.x stable) or the first-party
  `laravel/livewire-starter-kit` (dev-main, the current official Laravel 13
  / Livewire 4 kit; old stable kit tags are Laravel 12).
- PHP CLI, Node.js 20.19+ on 20.x or 22.12+, npm 11+.
- Laravel Installer (`laravel/installer`) is optional for manual work.
  Settings can check/install/update it in the developer's global Composer
  directory. It is not used for scaffold: v5.33.0 does not expose a
  no-migrations flag and can run migrations itself and via Composer hooks.

## Preparation

The adapter invokes Composer create-project with --no-scripts, in an absent
project directory. It initializes the private .env from the official
.env.example, creates an empty SQLite file, discovers Laravel packages,
generates APP_KEY and prepares the kit's default features with its official
non-interactive feature command if present. It runs npm install and npm run
build. The kit's default test framework and features are left as shipped.
Neither Composer setup/post-create-project-cmd nor artisan migrate/seed is
invoked. A read-only SQLite check rejects any schema unexpectedly created by
upstream changes. Keep the user-owned tree on the Linux filesystem.

After scaffold, DevStack creates Nginx/PHP-FPM, public access, optional
MariaDB database and isolated account, SSL and domain sync. It writes APP_URL
and, if selected, the generated MariaDB settings as comments in .env only:

```dotenv
DB_CONNECTION=sqlite
# DB_HOST=127.0.0.1
# DB_PORT=3306
# DB_DATABASE=your_database
# DB_USERNAME=your_user
# DB_PASSWORD=your_password
```

Values are generated per project, not shared. Keep DB_DATABASE commented
while using SQLite (otherwise Laravel interprets the MariaDB name as the
SQLite path). To use MariaDB, uncomment the five settings and change
DB_CONNECTION to mariadb. Run migrations only when you decide to do so.
The starter kit's session/cache defaults may require tables before the
application responds successfully; DevStack does not change those defaults.
No credentials are written to .env.example or source control.

The adapter preserves failures for inspection. Never use --force to retry an
existing directory. Updating Laravel Installer does not update application
packages or apply migrations.

Upstream: [Installer v5.33.0](https://github.com/laravel/installer/blob/v5.33.0/src/NewCommand.php),
[Laravel skeleton](https://github.com/laravel/laravel),
[Livewire Starter Kit](https://github.com/laravel/livewire-starter-kit),
[Packagist](https://packagist.org/packages/laravel/livewire-starter-kit).
