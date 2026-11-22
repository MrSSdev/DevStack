# Updating an existing DevStack installation

This guide is for a **previously installed** DevStack on Ubuntu 26.04 under WSL2. **Never rerun the first-install-only bootstrap or scripts/install.sh**.

DevStack provides an updater that runs tests and builds as your ordinary Ubuntu user, stages the installed application under root, keeps a backup of the previous application code, verifies services, and attempts to roll back if deployment fails.

**Important limitations:** This updater does not migrate service units, Nginx/FPM system configuration, the local CA, the Windows hosts scheduled task, or MariaDB schemas. It is not a substitute for a verified WSL export and SQL backups. Unexpected power loss or a forced process termination between two directory renames may require restoring from the root-only previous-code backup. The updater requires local WSL/Windows validation before being considered proven for a particular workstation.

## 1. Back up and inspect

Follow [BACKUP-RESTORE.md](BACKUP-RESTORE.md) before applying changes to an important workstation. A full offline WSL export can restore both the installed application and its private data. Never commit backups or mkcert private keys to Git.

Close long-running project operations; updating the PHP-FPM manager pool and DevStack helper can briefly interrupt dashboard requests.

In the **Ubuntu shell** as your ordinary account (not under sudo):

~~~bash
cd ~/projects/devstack-source
git status --short
git pull --ff-only
git log -3 --oneline
~~~

Review incoming changes. The update script requires a clean tracked checkout on `main` in `~/projects/devstack-source`.

## 2. One-command deployment

~~~bash
cd ~/projects/devstack-source
bash scripts/update.sh
~~~

The update entry point:

1. Checks the normal user, expected checkout path, clean tracked tree and installed DevStack.
2. Fast-forwards only the `main` branch.
3. Runs Python/PHP tests, PHP/Python syntax checks and the repository's security scan.
4. Builds the frontend as the **ordinary user**, never with sudo.
5. Asks for sudo only to run `scripts/apply-update.sh`, which validates the configured developer, stages a copy of `/opt/devstack`, updates only allowlisted application files, checks Nginx/PHP syntax, and makes a root-only backup at `/var/backups/devstack/update-*/installed`.
6. Replaces the installed code, restarts `devstackd`, reloads PHP-FPM, and checks Nginx, PHP-FPM, MariaDB, Redis, the helper, and the HTTPS dashboard.
7. On a detected failure during deployment, attempts to restore the previous `/opt/devstack` and restart the services.

**The updater never reinitializes** `/var/lib/devstack`, `/etc/devstack`, the mkcert CA, project source directories, MariaDB accounts/databases, or Windows certificate trust. It updates only allowlisted application code and compiled frontend files in `/opt/devstack`.

The protected code backup is not a complete system backup and should not be moved to public storage. Old backups are intentionally not deleted automatically.

## 3. Check after an update

~~~bash
systemctl is-active nginx php8.5-fpm mariadb redis-server devstackd
sudo nginx -t
sudo php-fpm8.5 -t
curl -ksS --noproxy '*' --resolve devstack.test:443:127.0.0.1 https://devstack.test/api/session
~~~

Refresh https://devstack.test using Ctrl+F5. Use a disposable project to verify creation, database reads/writes, guarded removal, and Windows hosts changes if those parts were updated.

The updater does not reconfigure the **Windows** hosts task or certificate trust. See [INSTALL-WINDOWS-WSL.md](INSTALL-WINDOWS-WSL.md) for explicit changes to that integration.

## 4. Manual rollback / emergency recovery

If the updater returns an error, read its rollback message and check service status; do not assume every failed update rolled back perfectly. Its root-only installed-code backups are created under:

~~~text
/var/backups/devstack/update-XXXXXXXX/installed
~~~

The exact directory is printed by the script. **The archive from your offline WSL export remains the safest full-system rollback point.**

If a hard power loss happened during a directory swap, inspect `/opt/devstack`, `/opt/.devstack-stage.*` and `/var/backups/devstack` before taking any action. Do **not** blindly delete anything or overwrite running databases. Restore only affected installed application code after inspecting the backup and paths. Verify `nginx -t`, `php-fpm8.5 -t`, `systemctl` status and dashboard access afterwards.

Existing workstation updates must not involve the initial installation script.

References:
- [Backups and restoration](BACKUP-RESTORE.md)
- [Windows and Ubuntu fresh installation](INSTALL-WINDOWS-WSL.md)
- [Troubleshooting](TROUBLESHOOTING.md)
- [Security](../SECURITY.md)
