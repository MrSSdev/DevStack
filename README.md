# DevStack

**A local PHP development environment for Windows 11 and Ubuntu on WSL2.** Manage local projects, HTTPS certificates, Nginx configuration and MariaDB from a web dashboard, and control Ubuntu and check services from two separate Windows widgets.

DevStack uses **Nginx, PHP-FPM 8.5, MariaDB, Redis and mkcert**. Its Polish-language dashboard is built with React and TypeScript; it is served as static assets plus a PHP API, not a continuously running Node.js development server.

> For trusted local development only. DevStack is not production hosting, a public control panel, or a security sandbox for untrusted project code. Review privileged installers and keep backups. The dashboard and Windows widget package have separate version numbers: the widget package described here is **0.4.2.0**.

## Features

| Area | What DevStack provides |
| --- | --- |
| Projects | Create a project under Linux `~/projects`, choose its relative document root and `.test` domain, configure HTTPS and an optional MariaDB database. Removing managed configuration preserves the source directory. |
| PHP and Nginx | Separate PHP-FPM pool/socket per project; guarded Nginx edits with syntax validation and rollback on invalid configuration. |
| Local HTTPS | mkcert certificates and explicit Windows trust of the public local CA. The CA private key remains in Ubuntu. |
| Databases | Per-project database accounts, generated credentials, authenticated password reveal and a connection/read/write smoke test. Database removal needs a separate confirmation. |
| Windows hosts | An elevated, on-demand scheduled task updates only the DevStack-managed hosts block. No resident Windows logon watcher is required. |
| Ubuntu widget | Running/offline state, explicit Start, confirmed Stop, CPU, RAM, disk and uptime. |
| DevStack widget | Four service tiles, response time and RAM, details and confirmed restart, total project count, and browser links to project screens. |
| Maintenance | Staged application update, protected previous-code backup, database dump and disposable restore verification, documented WSL backup procedures. |

The widgets are separate cards in **Win + W**, not tray windows. They use locally packaged HTML with a native Windows provider. Displaying a card does not start Ubuntu. The dark outer surface is transparent, and both themes use the same information layout. See the [complete widget guide](windows/widgets/README.md).

## Requirements and scope

- Windows 11 and WSL2 with `systemd` enabled in Ubuntu 26.04.
- An ordinary Ubuntu developer account with `sudo`; project files on the Linux filesystem, not `/mnt/c`.
- Node.js/npm for frontend builds only. The bootstrap checks supported versions and stops rather than silently installing a third-party runtime.
- Initial Windows Administrator access, using the Windows account that owns the WSL distribution, for CA trust, hosts integration and widget package installation.
- For the supplied **x64 widgets**, a compatible Windows Widgets Board, Windows App Runtime 2.7, and Microsoft Web Widget eligibility. The current widget configuration targets **`Ubuntu-26.04`**; its explicit Start action uses Linux user **`ubuntu`**. Other distribution/user names require a reviewed source configuration and rebuild, not a UI setting.

Microsoft currently documents Web Widgets as EEA-only. Regional/host support and the brief native loading screen are controlled by Windows. See [Microsoft's provider requirements](https://learn.microsoft.com/en-us/windows/apps/develop/widgets/web-widget-providers).

## First installation

Use this path only on a workstation without an existing DevStack installation. For an existing installation, use [Updating](#updating) instead.

**In the Ubuntu terminal, as your ordinary account:**

```bash
sudo apt update
sudo apt install -y git ca-certificates
mkdir -p ~/projects
git clone https://github.com/MrSSdev/DevStack.git ~/projects/devstack-source
cd ~/projects/devstack-source
less scripts/bootstrap-ubuntu.sh
less scripts/install.sh
bash scripts/bootstrap-ubuntu.sh
```

The bootstrap builds the frontend, checks prerequisites and tests, then invokes the first-install setup. Review the interactive authentication choice; password authentication is recommended.

**In Windows PowerShell as Administrator, under the WSL-owning account:**

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
$setup = '\\wsl.localhost\Ubuntu-26.04\home\<ubuntu-user>\projects\devstack-source\windows\setup-windows.ps1'
Get-Content -LiteralPath $setup
& $setup
```

Replace `<ubuntu-user>` with the actual Linux username. Verify the displayed public CA fingerprint before approving trust. This setup also registers the on-demand hosts task. It is separate from widget installation.

Open **https://devstack.test** in a Windows browser. The URL alone does not start a stopped distribution; open Ubuntu deliberately first. Follow the [detailed installation guide](docs/INSTALL-WINDOWS-WSL.md) for prerequisites and verification.

## Daily use

**Start work:** open Ubuntu deliberately, or use **Uruchom Ubuntu** in the Ubuntu widget. Services configured during setup start with the distribution. Opening the dashboard URL or a widget alone must not wake a stopped Ubuntu instance.

**Create a project:** use the dashboard's new-project screen, choose a source folder, relative webroot (commonly `public`), local domain, HTTPS and optional MariaDB. DevStack manages server configuration; it does not install Laravel or application dependencies for you.

**Open projects:** the DevStack widget's **Dodaj projekt** and **Projekty** buttons open `https://devstack.test/#/new` and `https://devstack.test/#/projects` in the default browser. The dashboard's normal authentication still applies. No project forms are embedded in the widget.

**Inspect or restart a service:** click its DevStack tile, inspect its measurements, choose **Uruchom ponownie** and confirm. Cancel performs no restart. A confirmed restart may interrupt that service's connections. A status line names the service, verifies its state and hides a successful result after 30 seconds. An unresolved error remains until a subsequent read verifies recovery; an unconfirmed command is never reported as a successful restart.

**Stop work:** save work before confirming **Zatrzymaj Ubuntu**. This terminates `Ubuntu-26.04`, not every installed WSL distribution. Closing the browser or Widgets Board does not stop Ubuntu.

**Remove a project:** use the dashboard's guarded removal flow and separately confirm any database destruction. Source files are preserved. Deleted database data requires a backup to recover.

## Updating

There are separate update paths; do not confuse extracting an archive with installing it.

| Component | Procedure |
| --- | --- |
| Existing DevStack website/helper | Run `bash scripts/update.sh` as the ordinary Ubuntu account in `~/projects/devstack-source` on `main`. It tests, builds, stages and deploys with a previous-code backup and health checks. |
| Windows widget package | Extract the complete matching MSIX/CER bundle and run `Install-Widgets.ps1` as Windows Administrator. It updates in place, verifies the installed version/status and resets the widget processes after success. |
| Widget Linux integrations | Install once with Ubuntu already running. Do not rerun on every widget update. Read the release instructions when an integration change is specifically required. |
| Windows hosts task | Updated explicitly through `windows/install-hosts-sync.ps1`; a source pull or Linux application update does not replace an installed Windows task. |

The code updater is not a database, system configuration or CA migration tool. Its rollback is best-effort, not a replacement for verified backups. See [UPGRADING.md](docs/UPGRADING.md) and the [widget installation guide](windows/widgets/README.md#installation).

## Architecture and safety

```text
Windows browser -> WSL loopback Nginx
                     |-- static dashboard
                     `-- PHP-FPM manager (devstackweb)
                           `-- local Unix socket -> restricted root helper
                                 |-- managed projects and service configuration
                                 |-- local databases and certificates
                                 `-- on-demand Windows hosts task

Windows Widgets Board -> packaged C# provider
                          |-- Ubuntu card: passive state / explicit lifecycle actions
                          `-- DevStack card: network probes / confirmed service actions
                                `-- authenticated, socket-activated Linux helpers
```

The UI documents measurement scope. Network availability is not the same as systemd unit state; Nginx and PHP share one end-to-end HTTPS probe. Linux memory readings are not total Windows memory. Missing data uses dashes rather than fabricated zeros.

Project PHP-FPM pools run as the trusted developer account, so projects are not isolated from each other's files. Protect `.env` files, database credentials, local CA private keys and backups. See [SECURITY.md](SECURITY.md).

## Known limits

Only large, x64 widget cards are supplied. Web Widget support depends on Microsoft host/region requirements. A short native loading spinner and temporary background differences may appear when opening the board. No promise of zero resident process memory or universal sleep/battery behavior is made.

Automatic certificate renewal, domain/slug changes, scheduled backups and project-to-project security isolation are not implemented. A full Laravel + Livewire 4 + Flux application remains an integration-validation task; the presence of a Flux Nginx rule alone is not end-to-end framework certification.

## Development and tests

```bash
cd ~/projects/devstack-source
python3 -m unittest discover -s tests -v
php tests/php-security.php
bash scripts/security-scan.sh
bash scripts/build-frontend.sh
```

GitHub Actions also builds and test-signs the Windows x64 MSIX, tests the provider state machines, renders both production widget pages in Chromium, and exercises the Linux reader under real systemd. Some Windows/OS actions are mocked; CI does not establish behavior on every workstation. Build details are in the [widget technical reference](windows/widgets/DEVSTACK-0.4.md).

## Documentation

- [First installation and Windows hosts](docs/INSTALL-WINDOWS-WSL.md)
- [Windows local CA trust](docs/WINDOWS-CA.md)
- [Application and widget updates](docs/UPGRADING.md)
- [Windows widgets: installation, operation and diagnostics](windows/widgets/README.md)
- [Widget architecture and security boundaries](windows/widgets/DEVSTACK-0.4.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Backups and restoration](docs/BACKUP-RESTORE.md)
- [Security policy](SECURITY.md)

MIT — [LICENSE](LICENSE).
