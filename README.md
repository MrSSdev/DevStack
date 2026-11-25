# DevStack

**Local PHP development environment for Windows 11 and Ubuntu on WSL2.**

Manage development projects, web servers, databases and local HTTPS from one web dashboard. DevStack includes Nginx, PHP-FPM 8.5, MariaDB, Redis and mkcert, with optional Windows widgets for quick access and service management.

## Features

- Project management with local `.test` domains, configurable document roots and individual PHP-FPM pools.
- Local HTTPS certificates and automatic synchronization of managed Windows hosts entries.
- MariaDB databases, per-project accounts and connection tests.
- Nginx configuration management with syntax validation.
- Optional local [Mailpit inbox](docs/LOCAL-MAIL.md) inside DevStack, with per-project test SMTP configuration.
- Two Windows 11 widgets: **Ubuntu** for WSL controls and resource readings, **DevStack** for service monitoring, restarts and project shortcuts.
- Application updates and database backup utilities.

## Requirements

Windows 11, WSL2 with Ubuntu 26.04 and systemd, an Ubuntu account with sudo access, and Windows administrator access for initial integration. Frontend builds require Node.js and npm. Use the Linux filesystem for project files.

## Quick start

In Windows PowerShell as Administrator, install Ubuntu if needed:

```powershell
wsl --install -d Ubuntu-26.04
```

Open Ubuntu, create your developer account, then run:

```bash
sudo apt update
sudo apt install -y git ca-certificates
mkdir -p ~/projects
git clone https://github.com/MrSSdev/DevStack.git ~/projects/devstack-source
cd ~/projects/devstack-source
bash scripts/bootstrap-ubuntu.sh
```

Follow the authentication prompt. Review privileged installation scripts before running them.

Complete the [Windows integration](docs/INSTALL-WINDOWS-WSL.md#3-windows-integration) to configure local certificate trust and domain synchronization. With Ubuntu running, open **https://devstack.test** in your browser.

For existing installations, follow the [update guide](docs/UPGRADING.md).

## Windows widgets

Install the optional widget package using the [widget guide](windows/widgets/README.md). Open **Win + W** and add the two cards separately.

| Widget | Functions |
| --- | --- |
| Ubuntu | Start and stop Ubuntu; view CPU, RAM, disk usage and uptime. |
| DevStack | Check Nginx, PHP-FPM, MariaDB and Redis; view response times and memory; restart individual services; open project screens. |

Readings refresh while the panel is open. Start Ubuntu explicitly when you want to work.

## Daily use

Create a project in the dashboard. The folder identifier follows the project name as you type, and the local .test domain follows the identifier. Either field can be edited independently and restored to automatic generation. Choose the document root, HTTPS and optional database. Open the generated URL in your browser. Use the project settings to manage its server configuration. Save your work before stopping Ubuntu or restarting services.

## Local test email (optional)

Install Mailpit once in Ubuntu from a reviewed checkout:

```bash
cd ~/projects/devstack-source
sudo bash scripts/install-mailpit.sh
```

Open **E-mail** in DevStack to read, search, download attachments and delete individual captured messages. Select **Lokalna poczta testowa** when creating a new project to configure its PHP-FPM mail transport and, for Laravel, its private `.env` SMTP settings. Existing projects are never changed automatically. See [Local test email](docs/LOCAL-MAIL.md) for limits, manual configuration and service management.

## Framework installers

When creating a project, select no framework or Laravel, then choose a frontend technology from the currently installed official installer's reported capabilities. Choose the conditional official installer options (built-in authentication, no authentication or WorkOS where supported, Livewire component style, teams, testing, Boost and npm build where applicable). The public directory is set automatically by the chosen framework. In Settings, manage the official installer (requires
Composer, PHP, Node.js 20.19+ on 20.x or 22.12+, and npm 11+ for Laravel). Installers require panel authentication or a separate, explicit administrator opt-in for passwordless local use (see [Security](SECURITY.md)). The
installer creates a fresh directory before DevStack configures hosting. Select SQLite or MariaDB; the official installer runs its normal initial migrations on that database, and DevStack stores the generated MariaDB connection in the private .env. DevStack never runs extra migrations. Existing source directories are never overwritten. Long operations continue independently
of the web request, and interrupted jobs retain files for review. See the
[adapter contract](framework-installers/README.md) for the workflow and acceptance tests.

## Documentation

- [Installation and Windows integration](docs/INSTALL-WINDOWS-WSL.md)
- [Windows widgets](windows/widgets/README.md)
- [Updating](docs/UPGRADING.md)
- [Local test email](docs/LOCAL-MAIL.md)
- [Local HTTPS certificate trust](docs/WINDOWS-CA.md)
- [Backups and recovery](docs/BACKUP-RESTORE.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Security](SECURITY.md)

Use DevStack for trusted local development. Keep credentials, certificate keys and backups private.

MIT — [LICENSE](LICENSE).

## Managed services and Windows widget selection

The dashboard lists every installed service in the root-owned `daemon/service-catalog.json` registry (including optional Mailpit). Click a service to start, stop or restart it after confirmation. Stopping Nginx/PHP-FPM also stops the dashboard; recover in Ubuntu with `sudo systemctl start nginx php8.5-fpm` or use the widget's existing restart action when available. The panel queues one delayed systemd action; an accepted request is not proof of success. Do not run updates or create projects while changing core services.

Settings → Widżet DevStack w Windows saves the selected services independently of the dashboard. Widget 0.5.0.0 reads the selection through the existing authenticated read bridge and retains the original default four tiles; extra tiles scroll. New managed services are added to the registry, not arbitrary systemd services. The Windows bundle requires both the MSIX update and its `Install-DevStackWidget.ps1 -SkipFrontend` integration update, after the normal Linux updater. Never replace a newer dashboard with a static frontend from an older Windows bundle. WSL telemetry and lifecycle are unchanged.
