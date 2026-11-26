# Troubleshooting

## Check DevStack

In Windows PowerShell:

```powershell
wsl.exe --list --running
Get-ScheduledTask -TaskName 'DevStack Hosts Event Sync'
Get-AppxPackage -Name 'MrSSdev.DevStack.Widgets' | Select-Object Name, Version, Status
```

In a running Ubuntu terminal:

```bash
systemctl is-active nginx php8.5-fpm mariadb redis-server devstackd
sudo nginx -t
sudo php-fpm8.5 -t
sudo journalctl -u devstackd -n 50 --no-pager
```

For dashboard access, start Ubuntu, check services and open https://devstack.test. For certificate trust, follow [Windows CA](WINDOWS-CA.md).

## Synchronize Windows hosts

Run in elevated Windows PowerShell:

```powershell
Start-ScheduledTask -TaskName 'DevStack Hosts Event Sync'
Get-ScheduledTaskInfo -TaskName 'DevStack Hosts Event Sync'
```

Check the managed entries in `$env:WINDIR\System32\drivers\etc\hosts`. For updating the task, see [Windows integration](INSTALL-WINDOWS-WSL.md#5-update-windows-hosts-integration).

## Windows widgets

Follow the [widget diagnostic steps](../windows/widgets/README.md#diagnostics). Check the installed package version and matching installer bundle. For a one-time widget process reset:

```powershell
Get-Process -Name 'WidgetBoard', 'DevStack.Widgets' -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue
```

Reopen **Win + W**.

## Project configuration

Check the selected source directory, document root, domain and Nginx configuration in the project settings. Use the project database connection test to check MariaDB access. For extra Nginx locations, use the project configuration fields and validate the configuration before deployment.

## Dashboard authentication

In Ubuntu:

```bash
sudo php /opt/devstack/scripts/configure-auth.php --status
sudo php /opt/devstack/scripts/configure-auth.php --password
```

When reporting a problem, include the version, affected operation and sanitized error messages. Keep passwords, tokens, private keys, project records and environment files confidential.
