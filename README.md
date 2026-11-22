# DevStack

**Local development environment manager** for **Windows 11 + WSL2 + Ubuntu 26.04 LTS**. DevStack hosts trusted local PHP/Laravel projects using Nginx, PHP-FPM 8.5, MariaDB, Redis and mkcert, with a lightweight Polish web dashboard.

> **Status: 0.1 — working development preview.** Tested on Windows/Ubuntu WSL2 for the core project lifecycle. Not production hosting, not a public control panel and not a sandbox for untrusted project code. Installations and upgrades require an administrator's review and backups.

## What works

- **Local dashboard:** React 19, TypeScript, Tailwind CSS 4; persisted light/dark theme; service status for Nginx, PHP-FPM, MariaDB and Redis.
- **Project lifecycle:** create project in `~/projects/<slug>`, assign its `.test` domain, document root, optional HTTPS and isolated database account; remove managed project configuration through a confirmation flow **while preserving the source directory**.
- **MariaDB:** cryptographically generated per-project credentials, password reveal behind panel authentication, and a TCP connection/read/write test using a temporary table.
- **Nginx:** separate PHP-FPM socket per project, managed virtual hosts, protected `location /` directives and validated server-level JavaScript/CSS asset `location` blocks (including the documented Laravel Flux rule). A save runs `nginx -t` before reload and rolls back failed validation.
- **Windows integration:** local mkcert CA trust with explicit consent; event-driven Windows `hosts` synchronization when project domains change. Only the DevStack-managed block is modified; unrelated hosts entries are preserved. **Settings now provide a public CA certificate download and Windows import instructions**; they never silently trust or expose the CA private key.
- **Security:** loopback-only Nginx listeners, dedicated non-root PHP web account, CSRF/session/origin protection, local Unix socket to a narrowly scoped root helper, root-only settings and credentials. Authentication is required by default, with a deliberate CLI opt-in for passwordless use on a trusted workstation.

**Known limitations:** a staged in-place updater with rollback attempts is now included, but it still requires real-machine validation. MariaDB dump and disposable restore were tested on a WSL workstation; full WSL archive import was also verified for the key DevStack files. Scheduled backups and tested live service recovery from the imported distro are not yet available. The current Windows scheduled hosts watcher starts at Windows login and can keep or restart WSL; on-demand-only lifecycle management is not implemented. Changing a project's domain/slug, automatic certificate renewal, and a Windows 11 native widget are not currently shipped. Laravel + Livewire 4 + Flux have not yet been tested end-to-end as a real application. See [SECURITY.md](SECURITY.md).

## Requirements

- Windows 11 with WSL2, `Ubuntu-26.04`, `systemd`, and an ordinary Ubuntu user with `sudo`.
- Developer projects stored under Linux `~/projects` on the WSL filesystem, **not under `/mnt/c`**.
- A supported current Node.js version for the frontend **build only**; no Node.js server runs at runtime.
- Initial interactive Windows Administrator access for local CA trust and the managed `hosts` integration.

## Fresh installation (new workstation only)

**Ubuntu terminal:**

~~~bash
sudo apt update
sudo apt install -y git ca-certificates
mkdir -p ~/projects
git clone https://github.com/MrSSdev/DevStack.git ~/projects/devstack-source
cd ~/projects/devstack-source

# Inspect before executing privileged setup:
less scripts/bootstrap-ubuntu.sh
less scripts/install.sh

bash scripts/bootstrap-ubuntu.sh
~~~

The bootstrap prepares build dependencies, builds the frontend, runs tests, then invokes the initial installer. The installer asks for local authentication configuration and sets up Nginx, PHP-FPM, MariaDB, Redis, mkcert and the DevStack helper. If the supported Node version is unavailable, the build stops instead of silently installing third-party packages.

**Windows PowerShell (run as Administrator, once, under the Windows user that owns WSL):**

~~~powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
$setup = '\\wsl.localhost\Ubuntu-26.04\home\<ubuntu-user>\projects\devstack-source\windows\setup-windows.ps1'
Get-Content -LiteralPath $setup
& $setup
~~~

Replace `<ubuntu-user>` with the actual Linux username. Review the CA fingerprint before approving trust. The private CA key stays inside Ubuntu.

Open **https://devstack.test** in a Windows browser.

**For a detailed clean-install guide and verification:** [docs/INSTALL-WINDOWS-WSL.md](docs/INSTALL-WINDOWS-WSL.md).

## Existing installation — do not rerun the installer

The bootstrap and `scripts/install.sh` are first-install-only and refuse to overwrite an existing installation. For an existing DevStack instance, run `bash scripts/update.sh` **as your normal Ubuntu user** to test, build, stage and deploy the update with a protected code backup and health checks. The updater has not yet been validated on every workstation; review its code and make backups first. Details: **[docs/UPGRADING.md](docs/UPGRADING.md)**.

## Common tasks

**Create a project:** open the dashboard, select new project, choose a folder, relative webroot (typically `public`), local domain, HTTPS and optional MariaDB.

**Test MariaDB:** open the project detail view and choose **Sprawdź połączenie, zapis i odczyt MariaDB**.

**Edit Nginx:** the basic field accepts a limited set of `location /` directives. The **Dodatkowe lokalizacje zasobów JS/CSS** field supports validated extra server-level asset routing. An example for Flux is documented in [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

**Remove a project:** open its detail view, choose **Usuń projekt z DevStack**, type its exact identifier, and separately confirm destruction of its managed MariaDB database if present. **The project source directory stays on disk. Database removal is irreversible without a backup.**

**Back up MariaDB (Ubuntu):** `sudo bash scripts/backup-project-db.sh <project-slug>`; validate the archive with `scripts/verify-project-db-backup.sh`. For WSL export and restoration procedures, see [docs/BACKUP-RESTORE.md](docs/BACKUP-RESTORE.md). The MariaDB dump and temporary restore workflow has been tested on a WSL workstation; verify it on your own data as well.

**Check services (Ubuntu):**

~~~bash
systemctl is-active devstackd nginx php8.5-fpm mariadb redis-server
sudo nginx -t
sudo php-fpm8.5 -t
~~~

## Architecture

~~~text
Windows browser (https://devstack.test)
          │
       Nginx (WSL loopback only)
          ├── static React assets
          └── PHP-FPM manager pool (devstackweb)
                  │ Unix domain socket + peer UID check
                  ▼
             devstackd helper
                  ├── managed Nginx and PHP-FPM project pools
                  ├── local MariaDB accounts/databases
                  ├── local mkcert certificates
                  └── root-only state and credentials

Windows scheduled task ← inotify watcher ← projects.json changes
           └── updates only the managed Windows hosts block
~~~

Project PHP-FPM pools run as the trusted Ubuntu developer account. DevStack does not isolate one project's source files from another's.

## Development and testing

~~~bash
cd ~/projects/devstack-source
python3 -m unittest discover -s tests -v
php tests/php-security.php
bash scripts/build-frontend.sh
~~~

CI checks TypeScript/Vite builds, Python/PHP validation, Windows PowerShell syntax and Windows hosts transformations. CI tests are not a substitute for live smoke tests on the target Windows/WSL setup.

## Documentation

- [Windows 11 + Ubuntu WSL2 fresh installation](docs/INSTALL-WINDOWS-WSL.md)
- [Installing or restoring Windows local CA trust](docs/WINDOWS-CA.md)
- [Updating an existing installation](docs/UPGRADING.md)
- [Troubleshooting common problems](docs/TROUBLESHOOTING.md)
- [Backups and disaster recovery](docs/BACKUP-RESTORE.md)
- [Security policy and known risks](SECURITY.md)

## License

MIT — [LICENSE](LICENSE).
