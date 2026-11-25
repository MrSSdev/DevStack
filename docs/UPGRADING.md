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

## Windows widgets

Download and extract the complete matching `DevStack-Windows-Widgets-x64` bundle from [GitHub Actions](https://github.com/MrSSdev/DevStack/actions/workflows/widgets.yml).

Run in Windows PowerShell as Administrator under the WSL-owning account:

```powershell
$dir = "$env:USERPROFILE\Downloads\DevStack-Windows-Widgets-x64"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$dir\Install-Widgets.ps1"
if ($LASTEXITCODE -ne 0) { throw 'Widget update failed.' }
Get-AppxPackage -Name 'MrSSdev.DevStack.Widgets' | Select-Object Name, Version, Status
```

The installer verifies the matching signature and package, updates in place and resets the Windows widget processes after successful deployment verification. Reopen **Win + W**. Keep existing Linux telemetry and service integration settings; run their setup scripts only when the release instructions require it.

For installation and setup, see the [widget guide](../windows/widgets/README.md).

## Windows hosts integration

To update the installed synchronization task, follow the [Windows hosts update](INSTALL-WINDOWS-WSL.md#5-update-windows-hosts-integration).

## Recovery

Previous application code is stored under `/var/backups/devstack/update-*/installed`. The widget frontend setup keeps its previous index under `/var/backups/devstack/widget-frontend-*`. Review the updater result and affected files before restoring a backup.

See [troubleshooting](TROUBLESHOOTING.md) and [backups and recovery](BACKUP-RESTORE.md).
