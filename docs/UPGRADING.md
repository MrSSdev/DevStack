# Updating DevStack

Update the web application and Windows widgets separately. Save work and prepare [backups](BACKUP-RESTORE.md) before deployment.

## DevStack application

In Ubuntu as your ordinary developer account:

```bash
cd ~/projects/devstack-source
git status --short
git pull --ff-only
bash scripts/update.sh
```

Use a clean tracked working tree on `main`. The updater checks and builds the source, deploys the application, keeps a protected previous-code backup and verifies the services. Project data, databases and certificates are preserved.

Verify the result:

```bash
for unit in nginx php8.5-fpm devstackd devstack-notifications.timer; do
    systemctl is-active "$unit"
done
sudo nginx -t
sudo php-fpm8.5 -t
```

Refresh https://devstack.test in the browser.

### Optional project database and cache services

The application state remains in the existing protected JSON files and SQLite journals; there is no format migration and no change to existing project databases, certificates or preferences. MariaDB and Redis are optional project services. An ordinary update neither starts, stops, enables, disables nor installs them.

The updater recognizes the exact previous DevStack helper unit, saves a protected copy at `/var/backups/devstack/update-*/devstackd.service.before` and removes its old MariaDB dependency. Custom unit definitions are refused rather than overwritten. Effective dependencies are checked before restarting the helper, including local overrides. This compatible unit correction intentionally survives a previous-code rollback so rollback cannot reintroduce automatic MariaDB startup. The previous unit is retained for manual inspection, not automatic restoration. Restarting an old unit with the obsolete dependency would wake MariaDB.

Operations requiring MariaDB (create/delete of managed MariaDB projects, database connection checks, dump and restore verification) explicitly require an installed, running MariaDB. Start it yourself if needed; a stopped or missing service is not an error for the dashboard, SQLite projects, hosts synchronization or notification history.

There is no need to reinstall the Windows MSIX for this application change. Existing installed v1/v2 bridge contracts and state files are preserved when MariaDB/Redis are installed but stopped. If deploying on a core-only machine or removing the server packages entirely, use a matching updated service-integration bundle: the v1 reader now accepts explicit missing-unit responses while v2 omits unavailable services. Do not treat a missing bus response as a stopped service.

If local Windows notifications are already enabled, the same update also
refreshes their Windows launcher using the existing opt-in marker. Run
from the signed-in Windows user's ordinary Ubuntu session with WSL Interop
available. No extra flag is needed for subsequent updates; an installation
without the opt-in marker remains unchanged unless explicitly requested with
`--windows-local-notifications`. See [local notifications](LOCAL-WINDOWS-NOTIFICATIONS.md)
for setup and rollback. Local WinRT is the intended system delivery path;
no browser Web Push subscription or Internet delivery is required.
The Windows notification and Linux code deployments
are separate stages; an error is reported rather than silently skipping an
enabled component.

## Optional local mail

A normal DevStack update neither installs nor replaces Mailpit. If you want the
local mailbox, use [the separate one-time installer](LOCAL-MAIL.md). Captured
messages, project mail settings and the required web/helper services are independent of
this optional service. Its first-install script refuses to overwrite an
existing installation; a future Mailpit version upgrade requires a reviewed
upgrade procedure.

## Windows widgets

Download and extract the complete matching `DevStack-Windows-Widgets-x64` bundle from [GitHub Actions](https://github.com/MrSSdev/DevStack/actions/workflows/widgets.yml).

Run in Windows PowerShell as Administrator under the WSL-owning account:

```powershell
$dir = "$env:USERPROFILE\Downloads\DevStack-Windows-Widgets-x64"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$dir\Install-Widgets.ps1"
if ($LASTEXITCODE -ne 0) { throw 'Widget update failed.' }
Get-AppxPackage -Name 'MrSSdev.DevStack.Widgets' | Select-Object Name, Version, Status
```

The installer verifies the matching signature and package and updates in place. Follow its prompt: open **Win + W** once, wait until the cards begin loading or show content, close the panel, then press Enter. It performs the final process reset automatically. Reopen Win+W and verify both cards. The additional reset is deliberately tied to first initialization, not a guessed delay.

If a release also requires a service integration update, use `Install-Widgets.ps1 -UpdateServiceIntegration` while Ubuntu is already running. This invokes the bridge installer with `-SkipFrontend` before the final reset; existing WSL telemetry is unchanged. For deferred or noninteractive setup, use `-DeferFirstOpenReset`, then run `Complete-WidgetUpdate.ps1` once in the interactive Windows session. Keep existing Linux integration settings otherwise.

For installation and setup, see the [widget guide](../windows/widgets/README.md).

## Windows hosts integration

To update the installed synchronization task, follow the [Windows hosts update](INSTALL-WINDOWS-WSL.md#5-update-windows-hosts-integration).

## Recovery

Previous application code is stored under `/var/backups/devstack/update-*/installed`. The widget frontend setup keeps its previous index under `/var/backups/devstack/widget-frontend-*`. Review the updater result and affected files before restoring a backup.

See [troubleshooting](TROUBLESHOOTING.md) and [backups and recovery](BACKUP-RESTORE.md).

## Framework installer access after upgrade

An upgrade does not alter the panel's authentication mode. A passwordless
panel keeps framework operations disabled until a local administrator opts in
using the command in [Security](../SECURITY.md). Existing authenticated
installations do not need this step. The policy is stored under /etc/devstack,
not in the versioned source or the browser, so subsequent updates retain it.
