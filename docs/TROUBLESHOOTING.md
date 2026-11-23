# DevStack troubleshooting

This is a **local development** service for Windows 11 + Ubuntu 26.04 WSL2, not a production hosting stack. Never paste private secrets, database passwords or mkcert CA private keys into issues.

## Quick checks

Run inside **Ubuntu**, not Windows PowerShell:

~~~bash
systemctl is-active nginx php8.5-fpm mariadb redis-server devstackd
sudo php-fpm8.5 -t
sudo nginx -t
sudo journalctl -u devstackd -n 50 --no-pager
curl -ksS --noproxy '*' --resolve devstack.test:443:127.0.0.1 https://devstack.test/api/session
~~~

The curl -k option is for a localhost diagnostic before certificate trust is configured. Normal browsing should validate HTTPS certificates.

## CSRF error after installation

If GET refresh works, but saving a preference or project gives an invalid CSRF-token error, a legacy version may have stored PHP sessions in a root-only directory. The panel worker must be able to traverse the session directory.

~~~bash
sudo install -d -m 0700 -o devstackweb -g devstackweb /var/lib/devstack-web/sessions
sudo sed -i 's|/var/lib/devstack/web-sessions|/var/lib/devstack-web/sessions|g' /etc/php/8.5/fpm/pool.d/devstack-manager.conf
sudo php-fpm8.5 -t && sudo systemctl reload php8.5-fpm
~~~

Close and reopen https://devstack.test. This repairs a **known legacy path only**; check the file before running commands on a customized installation.

## Project creation returns HTTP 502

Older releases synchronously reloaded php8.5-fpm during an in-flight FastCGI request, interrupting it. Current code validates PHP-FPM and defers its reload to a short-lived systemd timer.

Update the installed application using [UPGRADING.md](UPGRADING.md), then inspect the real error. **Do not repeatedly recreate a project without checking** the project list, existing project directories, and services.

~~~bash
sudo journalctl -u devstackd -n 50 --no-pager
~~~

## Project creation fails with "runuser cannot set user ID (EPERM)"

The helper has \`NoNewPrivileges=yes\`. Earlier versions used \`runuser\` to create developer-owned directories; current code uses a transient systemd service running with the developer account identity.

Follow [UPGRADING.md](UPGRADING.md) to update the installed helper and run unit tests before trying again.

## MariaDB connection smoke test

Open the project's page and select **Sprawdź połączenie, zapis i odczyt MariaDB**. DevStack connects to \`127.0.0.1:3306\` as the project's limited DB user, tests a temporary table, and does not expose credentials.

An older revision incorrectly wrote literal backslash-n characters to a temporary MariaDB client configuration; update the installed daemon using [UPGRADING.md](UPGRADING.md) if this specific test fails. Do not change user passwords or drop the database solely because the test fails.

## Flux JavaScript/CSS resources return 404

For applications using Laravel Flux, add the following to **Dodatkowe lokalizacje zasobów JS/CSS** for the project, not into \`location /\`:

~~~nginx
location ~* ^/flux/(flux|editor)(\.min)?\.(js|css)$ {
    expires off;
    try_files $uri $uri/ /index.php?$query_string;
}
~~~

DevStack places the validated server-level block before protected PHP handlers, checks \`nginx -t\` and rolls back invalid changes. The block alone does not install Laravel or Flux: the application must supply the routes and assets.

## Windows hosts synchronization

See [INSTALL-WINDOWS-WSL.md](INSTALL-WINDOWS-WSL.md). On current B1 code the logon task starts a **Windows-only WMI listener** that waits for external `wsl.exe` process-start events, rather than immediately launching Ubuntu or reconnecting `wsl -d` every 15 seconds. The listener consumes some Windows RAM while idle. Once Ubuntu is confirmed running, it attaches to the same root-owned Linux `inotify` event stream and updates only the managed hosts block. **Replacing the installed script requires a separate manual Windows installer step; `git pull` alone does not update `C:\ProgramData\DevStack`.** The older installed task may still wake Ubuntu until the new version passes local validation and is installed.

To inspect (Windows PowerShell):

~~~powershell
Get-ScheduledTask -TaskName 'DevStack Hosts Event Sync'
Get-ScheduledTaskInfo -TaskName 'DevStack Hosts Event Sync'
Get-Content "$env:WINDIR\System32\drivers\etc\hosts"
~~~

If running `& "$env:ProgramData\DevStack\watch-hosts.ps1" -Once` directly fails with `PSSecurityException` / "running scripts is disabled on this system", start **Windows PowerShell as Administrator** and use a one-time child process instead:

~~~powershell
& "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe" `
    -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass `
    -File "$env:ProgramData\DevStack\watch-hosts.ps1" -Once
$LASTEXITCODE
~~~

Exit code `0` indicates successful reconciliation. This does not persistently relax PowerShell's execution policy, register a new scheduled task or launch the long-running watcher. It **does** contact Ubuntu and can start WSL if it is stopped — invoke it only when you intentionally need to synchronize DevStack domains.

Before deploying the B1 listener to Task Scheduler, test the new source script manually as described in [INSTALL-WINDOWS-WSL.md](INSTALL-WINDOWS-WSL.md), section 5a, on the exact Windows/WSL workstation. Test when Ubuntu is running, after `wsl --terminate Ubuntu-26.04`, and after a user-initiated `wsl -d Ubuntu-26.04 -- true`; verify that no automatic wake-up occurs. **Do not claim B1 is complete from PowerShell syntax CI or a generic WMI detection test alone.**

## Authentication and theme

Panel authentication defaults to password mode. An administrator can change modes from the Ubuntu shell:

~~~bash
sudo php /opt/devstack/scripts/configure-auth.php --status
sudo php /opt/devstack/scripts/configure-auth.php --password
~~~

The theme is stored on the server and rendered before React loads. If a legacy build flashes light when the dark theme is selected, update the backend, daemon and built frontend through [UPGRADING.md](UPGRADING.md).

For security boundaries, see [SECURITY.md](../SECURITY.md).
