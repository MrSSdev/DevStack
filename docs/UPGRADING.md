# Updating DevStack

The website/helper, Windows hosts task, Windows widgets and their Linux integrations are separate components. Updating one does not imply the others were updated. **Do not rerun first-install bootstrap or `scripts/install.sh` on an existing installation.**

## Existing website and helper

Before changes, follow [BACKUP-RESTORE.md](BACKUP-RESTORE.md). An application-code backup is not a database backup or full WSL export. Save work; helper restarts and PHP-FPM reloads can interrupt dashboard requests.

In the **Ubuntu shell as the ordinary developer account**:

```bash
cd ~/projects/devstack-source
git status --short
git pull --ff-only
git log -3 --oneline
bash scripts/update.sh
```

Review incoming changes before the last command. The updater requires the checkout on `main`, the expected `~/projects/devstack-source` path and a clean tracked working tree. Do not run npm/builds as root.

The updater validates the installation, fast-forwards source, runs Python/PHP tests, syntax and security checks, and builds the frontend. It requests sudo for `apply-update.sh`, which stages allowlisted application files, checks Nginx/PHP syntax, keeps a root-only previous-code backup under `/var/backups/devstack/update-*/installed`, replaces the installed code, restarts the helper, reloads PHP-FPM and checks services/HTTPS. On a detected deployment failure it attempts rollback.

It does **not** reinitialize project data, databases, CA keys, Windows certificate trust or the hosts task. It is not a general migration tool for system service units, Nginx/FPM system configuration or database schemas. Rollback is best-effort; power loss or forced termination between directory operations can require manual recovery. Backup retention is not automatic.

## Verify the website update

```bash
systemctl is-active nginx php8.5-fpm mariadb redis-server devstackd
sudo nginx -t
sudo php-fpm8.5 -t
curl -ksS --noproxy '*' --resolve devstack.test:443:127.0.0.1 https://devstack.test/api/session
```

The `-k` request diagnoses local response only; it does not verify certificate trust. Refresh the Windows browser with Ctrl+F5 and use normal certificate validation. Test a disposable project's affected workflow without repeatedly recreating a partially created project.

## Windows widget package

Download a complete matching artifact from a successful [Windows Widgets workflow](https://github.com/MrSSdev/DevStack/actions/workflows/widgets.yml). Extract it to `%USERPROFILE%\Downloads\DevStack-Windows-Widgets-x64`. Never mix the installer, CER and MSIX from different runs. Extraction itself is not installation.

In **Windows PowerShell as Administrator under the WSL-owning account**:

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $dir = "$env:USERPROFILE\Downloads\DevStack-Windows-Widgets-x64"
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$dir\Install-Widgets.ps1"
    if ($LASTEXITCODE -ne 0) { throw 'Widget update failed.' }
    Get-AppxPackage -Name 'MrSSdev.DevStack.Widgets' |
        Select-Object Name, Version, Status
}
```

The installer verifies signature and identity, updates in place, checks the installed version/Status Ok and **automatically resets `WidgetBoard` and `DevStack.Widgets` after success**, in addition to the pre-deployment stop. A reset error produces a warning. Open Win + W again. Do not remove the working package first or reset Ubuntu as cleanup.

Routine updates preserve integration configuration. Do not rerun `Install-WslTelemetry.ps1` or `Install-DevStackWidget.ps1` unless a version's instructions specifically require an integration change. The [widget guide](../windows/widgets/README.md) explains first setup, measurement scope and recovery.

## Linux integration and hosts changes

A new widget installation can optionally add the read-only Ubuntu collector and the service read/control integration while Ubuntu is already running. Service setup may deploy only the matching static frontend for project deep links, with an index backup. This is not a general DevStack backend update. Current service bundles include the [reader compatibility correction](../windows/widgets/DEVSTACK-READER-FIX.md).

The Windows hosts task changes only when `windows/install-hosts-sync.ps1` is deliberately run. Follow the [existing-workstation migration](INSTALL-WINDOWS-WSL.md#5-existing-workstation-install-this-integration-without-reinstalling); do not repeat CA setup to update the task.

## Rollback and recovery

Read the updater's actual error and rollback result. Inspect `/opt/devstack`, any `/opt/.devstack-stage.*` directories and `/var/backups/devstack/update-*/installed` before changing anything. Do not blindly remove staged folders or overwrite running databases. Restore affected application files only after reviewing paths and backups, then repeat syntax, service and HTTPS checks.

A static frontend integration backup is stored under `/var/backups/devstack/widget-frontend-*`. It contains the old `index.html`; retained hashed assets let a reviewed rollback reference those assets. It is not a full application or database backup.

MSIX downgrade/reinstallation can affect pinned cards and per-user state. Prefer correcting a failed update in place; do not uninstall merely because a newer signer or host session needs diagnosis. Back up protected settings securely before any deliberate removal, and never post credentials in an issue.

See [security](../SECURITY.md), [troubleshooting](TROUBLESHOOTING.md) and [backup/restore](BACKUP-RESTORE.md).
