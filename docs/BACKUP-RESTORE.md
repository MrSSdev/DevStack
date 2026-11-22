# Backups and recovery

DevStack is a personal WSL2 development environment. Protect project data and the local mkcert CA before relying on the machine for important work. **A CI pass cannot validate backups on your computer.**

Two complementary backups are recommended:

1. A **full WSL export** capturing Linux, files, MariaDB, configuration, DevStack secrets, certificates and the local CA.
2. Regular **logical MariaDB dumps** for each managed project; these are faster to inspect and restore into a scratch database.

Backups contain private code, application data and potentially credentials or certificate keys. Store them with restricted access, ideally on an encrypted external disk or secured storage separate from the WSL VHDX. Never commit backups to Git.

## 1. Per-project MariaDB backup

A registered project with a DevStack-managed MariaDB database is required. For example, replace \`my-project\` with the slug from your dashboard.

**Ubuntu (ordinary developer account):**

~~~bash
cd ~/projects/devstack-source
sudo bash scripts/backup-project-db.sh my-project
~~~

The script:
- Validates the project against root-owned DevStack state and the expected managed database name.
- Uses the root MariaDB Unix socket, **not the project password**, and does not expose credentials on the command line.
- Runs \`mariadb-dump --single-transaction --quick --routines --events --triggers --hex-blob\`; do not run concurrent schema-changing migrations while it works.
- Writes a \`0600\` compressed dump and separate SHA-256 hash under \`~/devstack-backups\` (\`0700\` directory). It never overwrites a named backup.
- Does not include the source tree, DevStack project metadata, database account/grants, or the mkcert CA. Use full WSL export for those items.

**Verify backup readability and restore without touching the original database:**

~~~bash
cd ~/projects/devstack-source
sudo bash scripts/verify-project-db-backup.sh \
  ~/devstack-backups/my-project-YYYYMMDDTHHMMSSZ-XXXXXXXX.sql.gz
~~~

Pass the **actual path reported by the backup script**. The verifier checks SHA-256 and gzip integrity, creates a random throwaway MariaDB database, imports the SQL and removes that scratch database afterwards. **Run it only on trusted backups you created yourself**, as it executes SQL with local MariaDB administrator privileges. Review messages for cleanup failures.

A dump of an empty database can restore successfully but will have zero tables. To verify actual application data, create a disposable project, insert sample rows, dump it, and confirm they are present after restoration (or use a test with application-specific assertions).

The scripts do not automatically schedule backups or copy them off the WSL disk. Both are future work.

## 2. Full Ubuntu WSL2 export (offline, recommended before upgrades)

A full export is sensitive: it includes the local CA private key, passwords, project code, and all MariaDB storage. **Do not share the archive or its contents**.

The current \`DevStack Hosts Event Sync\` Windows scheduled task starts a long-running Linux watcher and reconnects after WSL termination. Temporarily stop it first; otherwise the task can immediately restart Ubuntu.

Close development tools, stop project jobs and database writes. In **Ubuntu**, stop services gracefully:

~~~bash
sudo systemctl stop nginx php8.5-fpm devstackd redis-server mariadb
sync
exit
~~~

Then in **Windows PowerShell as Administrator** (the same Windows user that owns Ubuntu):

~~~powershell
$taskName = 'DevStack Hosts Event Sync'
Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue

wsl --terminate Ubuntu-26.04

# Replace with an encrypted external drive when possible.
$backupDir = Join-Path $env:USERPROFILE 'DevStackBackups'
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
$archive = Join-Path $backupDir ("Ubuntu-26.04-" + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.tar')

wsl --export Ubuntu-26.04 $archive
if ($LASTEXITCODE -ne 0) { throw 'WSL export failed; do not treat the archive as a backup.' }
if (-not (Test-Path -LiteralPath $archive) -or (Get-Item -LiteralPath $archive).Length -eq 0) {
    throw 'WSL export archive is missing or empty.'
}

Get-FileHash -Algorithm SHA256 -LiteralPath $archive
~~~

The archive may be large. Confirm enough free disk space first. Copy the resulting \`.tar\` to an off-machine location; an archive on the same physical SSD does not protect against disk loss.

**Resume normal work** (Windows PowerShell; this intentionally starts Ubuntu again):

~~~powershell
wsl -d Ubuntu-26.04 -u root -- /bin/true
Start-ScheduledTask -TaskName 'DevStack Hosts Event Sync'
~~~

This restores the old watcher until its planned on-demand lifecycle improvement is implemented.

## 3. Non-destructive WSL import test

**Never import over or unregister your existing \`Ubuntu-26.04\`** to verify a backup.

An export is not fully verified until you can import it and read the expected files. Ensure there is enough extra storage space to unpack the tar. Prefer a separate throwaway Windows machine/VM to avoid local port conflicts; importing on the same laptop is possible, but don't run both DevStack server instances concurrently.

In Windows PowerShell (use a terminal under the same Windows user):

~~~powershell
# Use the actual $archive path from the export step.
$restoreDir = Join-Path $env:USERPROFILE 'DevStackRestoreCheck'
if (Test-Path -LiteralPath $restoreDir) { throw 'Restore-check directory already exists.' }

wsl --import DevStackRestoreCheck $restoreDir $archive --version 2
if ($LASTEXITCODE -ne 0) { throw 'Test import failed.' }
wsl -d DevStackRestoreCheck -u root -- /bin/sh -c 'test -f /etc/devstack/server.json && test -f /var/lib/devstack/projects.json'
if ($LASTEXITCODE -ne 0) { throw 'The imported distribution is missing DevStack state.' }
wsl --terminate DevStackRestoreCheck

# Do not unregister the test distribution until you have verified its files
# and confirmed that it does not contain the only copy of any data.
~~~

Importing proves the tar can be restored and its expected files are present; checking service startup and project data is a separate manual test. Stop/terminate the temporary distro after testing to avoid two DevStack instances serving the same local domains.

## 4. Restore overview (when needed)

- **Full WSL:** import an archive as a **new distribution name** and new destination folder. Examine its files and data first; never overwrite the working distro blindly. Read Microsoft WSL documentation on \`wsl --import\` and \`wsl --export\`.
- **MariaDB only:** verify the dump first in an isolated scratch database. When restoring to a real project, take another backup and use the exact reviewed target schema; managed project accounts/credentials remain maintained separately by DevStack. The scratch verifier never overwrites project databases.
- **Local CA:** a restored full export includes the mkcert root key; protect it carefully. Do not reinstall or re-trust certificate authorities just to test SQL restore.
- **After restoring:** check \`nginx -t\`, \`php-fpm8.5 -t\`, service status, project content and Windows \`hosts\` synchronization.

Official WSL reference: https://learn.microsoft.com/en-us/windows/wsl/basic-commands

See also [UPGRADING.md](UPGRADING.md), [INSTALL-WINDOWS-WSL.md](INSTALL-WINDOWS-WSL.md) and [SECURITY.md](../SECURITY.md).
