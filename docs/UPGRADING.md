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
systemctl is-active nginx php8.5-fpm mariadb redis-server devstackd
sudo nginx -t
sudo php-fpm8.5 -t
```

Refresh https://devstack.test in the browser.

If local Windows notifications are already enabled, the same update also
refreshes their Windows launcher using the existing opt-in marker. Run
from the signed-in Windows user's ordinary Ubuntu session with WSL Interop
available. No extra flag is needed for subsequent updates; an installation
without the opt-in marker remains unchanged unless explicitly requested with
`--windows-local-notifications`. See [local notifications](LOCAL-WINDOWS-NOTIFICATIONS.md)
for setup and rollback. The Windows notification and Linux code deployments
are separate stages; an error is reported rather than silently skipping an
enabled component.

## Optional local mail

A normal DevStack update neither installs nor replaces Mailpit. If you want the
local mailbox, use [the separate one-time installer](LOCAL-MAIL.md). Captured
messages, project mail settings and the four core services are independent of
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
