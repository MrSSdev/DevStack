# Backups and recovery

Use database dumps for individual projects and a full WSL export for the entire environment. Store backups securely, preferably on a separate encrypted drive.

## 1. Database backup

In Ubuntu, replace `my-project` with the project's slug:

```bash
cd ~/projects/devstack-source
sudo bash scripts/backup-project-db.sh my-project
```

The script creates a compressed SQL dump and SHA-256 checksum under `~/devstack-backups`. Pause schema-changing operations during the backup.

Verify a trusted dump by restoring it into a temporary database:

```bash
sudo bash scripts/verify-project-db-backup.sh \
  ~/devstack-backups/my-project-YYYYMMDDTHHMMSSZ-XXXXXXXX.sql.gz
```

Use the actual filename reported by the backup command. An optional second argument names a table to check for restored rows. Run verification only on trusted dumps: SQL executes with local database administrator privileges.

## 2. Full WSL backup

Save work and stop project jobs and database writes. In Ubuntu:

```bash
sudo systemctl stop nginx php8.5-fpm devstackd redis-server mariadb
sync
exit
```

In Windows PowerShell under the account that owns Ubuntu:

```powershell
wsl --terminate Ubuntu-26.04
if ($LASTEXITCODE -ne 0) { throw 'WSL termination failed.' }
$backupDir = Join-Path $env:USERPROFILE 'DevStackBackups'
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
$archive = Join-Path $backupDir ("Ubuntu-26.04-" + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.tar')
wsl --export Ubuntu-26.04 $archive
if ($LASTEXITCODE -ne 0) { throw 'WSL export failed.' }
if (!(Test-Path -LiteralPath $archive) -or (Get-Item -LiteralPath $archive).Length -eq 0) {
    throw 'Backup is missing or empty.'
}
Get-FileHash -Algorithm SHA256 -LiteralPath $archive
```

Copy the archive and its checksum to secure independent storage. The on-demand hosts task can remain installed. Resume normal work by starting Ubuntu explicitly.

## 3. Verify or restore a WSL archive

Prefer an isolated test machine. Import the archive under a new distribution name and into a new directory. Keep the original distribution and backup intact. If using the same Windows host, stop the original distribution before starting the restored instance to avoid port conflicts.

```powershell
# Set $archive to the actual verified backup path.
$restoreDir = Join-Path $env:USERPROFILE 'DevStackRestoreCheck'
if (Test-Path -LiteralPath $restoreDir) { throw 'Restore directory already exists.' }
wsl --import DevStackRestoreCheck $restoreDir $archive --version 2
if ($LASTEXITCODE -ne 0) { throw 'Test import failed.' }
wsl -d DevStackRestoreCheck -u root -- /bin/sh -c 'test -f /etc/devstack/server.json && test -f /var/lib/devstack/projects.json'
if ($LASTEXITCODE -ne 0) { throw 'Restored state check failed.' }
wsl --terminate DevStackRestoreCheck
```

Verify files, project data and service startup before using a restored environment.

For a database-only restore, verify the dump, take a fresh backup of the target database and restore to the reviewed target schema. Preserve the managed database accounts and project configuration.

Full exports contain credentials, project files and CA private keys. Keep them confidential.

[WSL command reference](https://learn.microsoft.com/en-us/windows/wsl/basic-commands) · [Updating](UPGRADING.md) · [Security](../SECURITY.md)
