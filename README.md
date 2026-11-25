# DevStack

**Local PHP development environment for Windows 11 and Ubuntu on WSL2.**

Manage development projects, web servers, databases and local HTTPS from one web dashboard. DevStack includes Nginx, PHP-FPM 8.5, MariaDB, Redis and mkcert, with optional Windows widgets for quick access and service management.

## Features

- Project management with local `.test` domains, configurable document roots and individual PHP-FPM pools.
- Local HTTPS certificates and automatic synchronization of managed Windows hosts entries.
- MariaDB databases, per-project accounts and connection tests.
- Nginx configuration management with syntax validation.
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

Create a project in the dashboard, choose its local domain, source directory, document root, HTTPS and optional database. Open the generated URL in your browser. Use the project settings to manage its server configuration. Save your work before stopping Ubuntu or restarting services.

## Framework installers

When creating a project, select no engine or Laravel, then choose the supported
installation variant. In Settings, manage the official installer (requires
Composer, PHP, Node.js 20.19+ on 20.x or 22.12+, npm 11+ for Laravel, and enabled panel password authentication). The
installer creates a fresh directory before DevStack configures hosting; existing
source directories are never overwritten. Long operations continue independently
of the web request, and interrupted jobs retain files for review. See the
[adapter contract](framework-installers/README.md) for the draft workflow and acceptance tests.

## Documentation

- [Installation and Windows integration](docs/INSTALL-WINDOWS-WSL.md)
- [Windows widgets](windows/widgets/README.md)
- [Updating](docs/UPGRADING.md)
- [Local HTTPS certificate trust](docs/WINDOWS-CA.md)
- [Backups and recovery](docs/BACKUP-RESTORE.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Security](SECURITY.md)

Use DevStack for trusted local development. Keep credentials, certificate keys and backups private.

MIT — [LICENSE](LICENSE).
