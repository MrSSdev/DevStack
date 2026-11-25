# Troubleshooting DevStack

Identify the failing component before changing configuration. The dashboard, Windows hosts task, widget package and Linux widget integrations have independent installation and failure paths. Do not publish passwords, `.env` files, project registries, widget tokens, CA private keys or unredacted workstation screenshots.

## Read-only starting point

**Windows PowerShell:**

```powershell
wsl.exe --list --running
Get-AppxPackage -Name 'MrSSdev.DevStack.Widgets' | Select-Object Name, Version, Status
Get-ScheduledTask -TaskName 'DevStack Hosts Event Sync' -ErrorAction SilentlyContinue
Get-ScheduledTaskInfo -TaskName 'DevStack Hosts Event Sync' -ErrorAction SilentlyContinue
```

These checks do not start Ubuntu. Opening an Ubuntu terminal or using `wsl -d` does, so do that only deliberately.

**In an already-running Ubuntu terminal:**

```bash
systemctl is-active nginx php8.5-fpm mariadb redis-server devstackd
sudo php-fpm8.5 -t
sudo nginx -t
sudo journalctl -u devstackd -n 50 --no-pager
curl -ksS --noproxy '*' --resolve devstack.test:443:127.0.0.1 https://devstack.test/api/session
```

`curl -k` is a localhost diagnostic, not a normal TLS configuration or proof of trust. Review journal output before sharing it.

## Dashboard cannot be reached

Confirm Ubuntu is running, services are active, `devstack.test` is in the managed Windows hosts block and normal HTTPS trust is valid. The dashboard URL does not wake a stopped distribution. Use [Windows CA guidance](WINDOWS-CA.md) for trust problems; never disable certificate validation globally or export the CA private key.

## Widget installation or loading

The [widget diagnostic guide](../windows/widgets/README.md#diagnostics) includes package/configuration checks and the relevant Linux socket units.

- **Installer not found:** inspect the complete extracted artifact and any extra nested directory. The source/preview artifact is not the installer bundle.
- **Old installed version:** extraction and Linux setup do not install MSIX; run the matching `Install-Widgets.ps1`.
- **Runtime or region incompatibility:** a supported Web Widget host is required in addition to the package's minimum Windows version. An explanatory fallback is not a working full web card.
- **A brief spinner/color change:** this can occur before the local page is rendered; do not reinstall Ubuntu or telemetry for that transient host behavior.
- **Persistent failure after an update:** check version and installer warnings. Current installers attempt a post-success host/provider reset automatically. For a one-time recovery only:

```powershell
Get-Process -Name 'WidgetBoard', 'DevStack.Widgets' -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue
```

Reopen Win + W. This stops Windows processes, not Ubuntu or its applications. A recurring reset requirement is a defect to investigate, not an intended normal workflow.

## Resource data or controls missing

A socket being active means it listens; it does not prove authenticated measurements work. A control health HTTP 200 is not a restart test. A read-only fallback can show data while native control authentication is unavailable; do not force-enable the button.

Check only the existence of `%LOCALAPPDATA%\DevStack\Widgets\telemetry.json` and `services.json`, not their secret contents. Inspect the setup's stage/HTTP status and `wsl-bridge.log`. A failed setup can leave Linux files installed while Windows settings remain unwritten; keep the complete bundle and repair the identified failure.

A service reader returning HTTP 503 while `systemctl show` works as root can indicate a different execution context in the sandbox. Current setup uses a dedicated local non-login reader identity. See [D-Bus reader compatibility](../windows/widgets/DEVSTACK-READER-FIX.md). Do not change D-Bus policy, run the reader as root or make project/token files world-readable.

An unresolved restart error stays visible until a fresh healthy read of the affected service. Recovery changes the notice and expires it after 30 seconds. No automatic restart is performed. An unconfirmed command may already have executed; inspect current state before deliberately trying again.

## Windows hosts synchronization

The installed task is on-demand, elevated and noninteractive S4U. It has no logon trigger and must not wake stopped Ubuntu. `git pull` or a Linux application update does not replace an installed Windows scheduled task.

Inspect the task and hosts file, then use a new **elevated Windows PowerShell** process for a one-shot check:

```powershell
& "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe" `
    -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass `
    -File "$env:ProgramData\DevStack\watch-hosts.ps1" -Once
$LASTEXITCODE
```

Zero means reconciliation succeeded or safely skipped a stopped distro. Verify actual entries and unrelated hosts preservation. `-ExecutionPolicy Bypass` affects only this child process. For migration from an older resident listener, use the [existing-workstation procedure](INSTALL-WINDOWS-WSL.md#5-existing-workstation-install-this-integration-without-reinstalling), not CA reinstallation.

## CSRF error on a legacy installation

Some older installations used a root-only PHP session directory. First inspect the actual manager pool. Apply this only for the known old `/var/lib/devstack/web-sessions` path:

```bash
sudo install -d -m 0700 -o devstackweb -g devstackweb /var/lib/devstack-web/sessions
sudo sed -i 's|/var/lib/devstack/web-sessions|/var/lib/devstack-web/sessions|g' /etc/php/8.5/fpm/pool.d/devstack-manager.conf
sudo php-fpm8.5 -t && sudo systemctl reload php8.5-fpm
```

Then reopen the dashboard. A session-path repair is not a reason to disable CSRF or password authentication.

## Project creation returns HTTP 502 or EPERM

Legacy synchronous PHP-FPM reloads could interrupt a FastCGI request. Current helper code validates FPM configuration and defers its reload using a short systemd timer. Legacy `runuser` directory creation under `NoNewPrivileges=yes` was also replaced with a bounded service running as the developer account.

Update through [UPGRADING.md](UPGRADING.md), read `devstackd` errors and inspect the project registry through the dashboard before retrying. Do not repeatedly recreate a partially created project or weaken helper isolation without evidence.

## MariaDB smoke test

In a project detail page, select **Sprawdź połączenie, zapis i odczyt MariaDB**. It connects using that project's restricted account and a temporary table. A legacy temporary-client-config escaping bug is addressed in current helper code; use the updater rather than changing passwords or dropping databases to fix a failed test.

## Flux JavaScript/CSS 404

For an application that supplies Laravel Flux routes, add this in **Dodatkowe lokalizacje zasobów JS/CSS**, not inside `location /`:

```nginx
location ~* ^/flux/(flux|editor)(\.min)?\.(js|css)$ {
    expires off;
    try_files $uri $uri/ /index.php?$query_string;
}
```

DevStack validates the extra server-level block, runs `nginx -t` and rolls back invalid edits. This rule does not install Laravel/Flux or establish end-to-end application compatibility.

## Authentication and reporting

To inspect or restore password authentication in Ubuntu:

```bash
sudo php /opt/devstack/scripts/configure-auth.php --status
sudo php /opt/devstack/scripts/configure-auth.php --password
```

The dashboard theme is a server-side preference; widget themes follow their host. Include package version, source revision, failing stage and sanitized event codes in a report. Never attach the contents of protected settings or project records. See [SECURITY.md](../SECURITY.md).
