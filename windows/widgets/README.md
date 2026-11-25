# DevStack Widgets

Two large widgets for the Windows 11 **Win + W** panel, with light and dark themes.

- **Ubuntu:** distribution status, CPU, RAM, disk usage, uptime, and Start/Stop controls.
- **DevStack:** Nginx, PHP-FPM, MariaDB and Redis status, response time, memory, service details, confirmed restarts, project count and dashboard shortcuts.

## Requirements

Windows 11 x64 with a compatible Web Widgets host and Windows App Runtime 2.7. See [Microsoft's host requirements](https://learn.microsoft.com/en-us/windows/apps/develop/widgets/web-widget-providers). The supplied package targets WSL2 `Ubuntu-26.04` and Linux user `ubuntu`. Use systemd, Python 3 and a working DevStack installation for service integration.

## Download

From a successful [Windows Widgets build](https://github.com/MrSSdev/DevStack/actions/workflows/widgets.yml), download the **DevStack-Windows-Widgets-x64** artifact. Extract the complete archive to `%USERPROFILE%\Downloads\DevStack-Windows-Widgets-x64`.

Keep the MSIX, CER, installers, frontend ZIP, and `telemetry/` and `services/` directories from the same build together.

## Installation

### 1. Windows package

Run Windows PowerShell as Administrator, under the account that owns Ubuntu:

```powershell
$dir = "$env:USERPROFILE\Downloads\DevStack-Windows-Widgets-x64"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$dir\Install-Widgets.ps1"
if ($LASTEXITCODE -ne 0) { throw 'Widget installation failed.' }
Get-AppxPackage -Name 'MrSSdev.DevStack.Widgets' | Select-Object Name, Version, Status
```

The installer verifies the signature and registers the package. Press **Win + W → +** to add **Ubuntu** and **DevStack** separately.

### 2. Ubuntu resource readings

Start Ubuntu explicitly. Run this one-time setup in Windows PowerShell:

```powershell
$dir = "$env:USERPROFILE\Downloads\DevStack-Windows-Widgets-x64"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$dir\Install-WslTelemetry.ps1"
if ($LASTEXITCODE -ne 0) { throw 'Telemetry setup failed.' }
```

### 3. DevStack service integration

With Ubuntu running and DevStack installed:

```powershell
$dir = "$env:USERPROFILE\Downloads\DevStack-Windows-Widgets-x64"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$dir\Install-DevStackWidget.ps1"
if ($LASTEXITCODE -ne 0) { throw 'Service setup failed.' }
```

This installs authenticated service readings and restart controls. Setup also updates the bundled static dashboard frontend for project links, keeping a previous-index backup. Use `-SkipFrontend` if the installed frontend already supports the project routes.

Reopen **Win + W**.

## Using the widgets

**Ubuntu:** select **Uruchom Ubuntu** to start the distribution. Select **Zatrzymaj Ubuntu** and confirm to stop it; save work first. Click a resource tile for details.

**DevStack:** select a service tile for details. Choose **Uruchom ponownie** and confirm to restart that service. The status line reports progress and verification; a successful result clears after 30 seconds. A later healthy reading clears an earlier service warning. **Dodaj projekt** and **Projekty** open the dashboard in the default browser.

Both cards refresh when visible. Opening the panel preserves the current Ubuntu lifecycle state.

| Reading | Measurement |
| --- | --- |
| Ubuntu CPU / RAM | WSL kernel CPU utilization and used memory. |
| Disk / uptime | Root filesystem usage and time since distribution PID 1 started. |
| Nginx / PHP-FPM response | Shared end-to-end HTTPS request. |
| MariaDB / Redis response | TCP connection establishment time. |
| Service RAM / CPU | systemd cgroup memory including cache and short CPU delta. 100% CPU represents one logical processor. |

## Updating

Extract a complete new matching bundle and repeat step 1. The installer verifies deployment and automatically attempts to reset `WidgetBoard` and `DevStack.Widgets` after success. Keep the existing Linux integrations; repeat their setup only if required by a release.

## Diagnostics

Windows PowerShell:

```powershell
Get-AppxPackage -Name 'MrSSdev.DevStack.Widgets' | Select-Object Name, Version, Status
wsl.exe --list --running
$settings = Join-Path $env:LOCALAPPDATA 'DevStack\Widgets'
Test-Path (Join-Path $settings 'telemetry.json')
Test-Path (Join-Path $settings 'services.json')
$log = Join-Path $settings 'wsl-bridge.log'
if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log -Tail 40 }
```

In a running Ubuntu terminal:

```bash
systemctl is-active devstack-widget-metrics.socket devstack-widget-services.socket devstack-widget-control.socket
systemctl is-active nginx php8.5-fpm mariadb redis-server
```

For a one-time widget process reset:

```powershell
Get-Process -Name 'WidgetBoard', 'DevStack.Widgets' -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue
```

Reopen Win + W. Review logs before sharing them; the JSON settings contain credentials.

## Removal

Use the card's Windows menu to unpin it. To remove the Windows package:

```powershell
Get-AppxPackage -Name 'MrSSdev.DevStack.Widgets' | Remove-AppxPackage
```

Linux integration and project data remain in place. To remove only the Ubuntu telemetry collector, start Ubuntu and use `Install-WslTelemetry.ps1 -Remove`.

[Technical reference](DEVSTACK-0.4.md) · [Release notes](CHANGELOG.md) · [Security](../../SECURITY.md)
