# DevStack Widgets

Two large widgets for the Windows 11 **Win + W** panel, with light and dark themes.

- **Ubuntu:** distribution status, CPU, RAM, disk usage, uptime, and Start/Stop controls.
- **DevStack:** Nginx, PHP-FPM, MariaDB and Redis status, response time, memory, service details, confirmed restarts, project count and dashboard shortcuts.

## Requirements

Windows 11 x64 with a compatible Web Widgets host and Windows App Runtime 2.7. See [Microsoft's host requirements](https://learn.microsoft.com/en-us/windows/apps/develop/widgets/web-widget-providers). The supplied package targets WSL2 `Ubuntu-26.04` and Linux user `ubuntu`. Use systemd, Python 3 and a working DevStack installation for service integration.

## Download

From a successful [Windows Widgets build](https://github.com/MrSSdev/DevStack/actions/workflows/widgets.yml), download the **DevStack-Windows-Widgets-0.5.3.0** artifact. Extract the complete archive to `%USERPROFILE%\Downloads\DevStack-Windows-Widgets-x64`.

Keep the MSIX, CER, installers, frontend ZIP, and `telemetry/` and `services/` directories from the same build together.

## Installation

### 1. Windows package

For a fresh installation, first record your SID in ordinary Windows PowerShell (`[Security.Principal.WindowsIdentity]::GetCurrent().User.Value`). Run Windows PowerShell 5.1 as Administrator under the same account and substitute that SID below. For an existing legacy installation, use the complete migration bundle described in `WINDOWS-COMPONENT-LAYOUT.md` before installing this MSIX.

```powershell
$dir = "$env:USERPROFILE\Downloads\DevStack-Windows-Widgets-x64"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$dir\Install-Widgets.ps1" -ExpectedUserSid 'S-1-5-21-...'
if ($LASTEXITCODE -ne 0) { throw 'Widget installation failed.' }
Get-AppxPackage -Name 'MrSSdev.DevStack.Widgets' | Select-Object Name, Version, Status
```

The installer verifies the signature and registers the package. Follow its first-open instructions: open **Win + W**, add **Ubuntu** and **DevStack** separately if needed, close the panel, and press Enter in PowerShell. The finalizer resets the widget processes once more after that first initialization. Reopen Win + W.

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

This installs authenticated service readings and restart controls. The default `-SkipFrontend` preserves the existing dashboard; request static frontend replacement only deliberately with `-SkipFrontend:$false`. Run integration setup separately in ordinary Windows PowerShell; it does not require the elevated MSIX installer.

Reopen **Win + W**.

## Using the widgets

**Ubuntu:** select **Uruchom Ubuntu** to start the distribution. Select **Zatrzymaj Ubuntu** and confirm to stop it; save work first. Click a resource tile for details.

**DevStack:** select a service tile for details. Choose **Uruchom ponownie** and confirm to restart that service. The status line reports progress and verification; a successful result clears after 30 seconds. A later healthy reading clears an earlier service warning. **Dodaj projekt** and **Projekty** open their exact DevStack routes in the registered PWA, or the default browser if no suitable handler exists. The PWA manifest requests navigation in an existing app window on supported browsers, rather than opening a new window for each click.

Both cards refresh when visible. Opening the panel preserves the current Ubuntu lifecycle state.

| Reading | Measurement |
| --- | --- |
| Ubuntu CPU / RAM | WSL kernel CPU utilization and used memory. |
| Disk / uptime | Root filesystem usage and time since distribution PID 1 started. |
| Nginx / PHP-FPM response | Shared end-to-end HTTPS request. |
| MariaDB / Redis response | TCP connection establishment time. |
| Service RAM / CPU | systemd cgroup memory including cache and short CPU delta. 100% CPU represents one logical processor. |

## Extending the managed service catalog

The shared, administrator-controlled [service catalog](../../daemon/service-catalog.json) is the allowlist for the dashboard and the v2 widget bridge. Widget settings select which registered services to display; they do not register arbitrary systemd units. A catalog entry for an already installed, supported service can be deployed through the normal Linux update without rebuilding the current Windows package.

For example, if Memcached were installed and listening on its default TCP port, a maintainer could add this entry to the catalog (this is an example, not an installed dependency):

```json
{
  "id": "memcached",
  "unit": "memcached.service",
  "name": "Memcached",
  "description": "Pamięć podręczna",
  "probe": "tcp",
  "port": 11211,
  "widget_default": false,
  "panel_dependency": false
}
```

Use unique IDs and systemd service names. The schema allows up to 24 entries and requires the exact fields shown above; IDs and unit names are validated by [managed_services.py](../../daemon/managed_services.py). Mark panel dependencies accurately. The installed registry must remain root-owned, non-symlinked and not group/world writable. Never accept an arbitrary unit name or shell command from the dashboard or widget.

The bridge resolves the service ID against this catalog, checks that the unit is loaded, blocks conflicting installer/service operations and invokes a bounded `systemctl --no-ask-password restart <unit>` (12-second subprocess timeout, no automatic retry). The widget verifies a fresh post-command snapshot: the unit must be active and the selected service must pass its configured probe. Successful verification is displayed in the widget itself for 30 seconds, without a second PWA toast. Recovery after an error or unconfirmed command is not proof that the restart succeeded.

| Probe | Meaning and limitation |
| --- | --- |
| `tcp` | Windows connects to the configured port on 127.0.0.1; this measures reachability, not application-level health or process identity. Requires the service to be reachable through WSL networking. |
| `unit` | Systemd active state only; use port 0 when no network probe is applicable. |
| `https` | Shared fixed DevStack HTTPS session endpoint, not a general configurable URL or independent HTTP health check for a new service. Nginx and PHP-FPM retain their dedicated interpretation of that response. |

Test any new catalog entry with real systemd status, selection persistence, metrics, successful and failed restart verification, and the existing no-WSL-wake behavior. Slow-starting services or services requiring application-specific readiness checks may need additional implementation. Keep the legacy v1 bridge behavior intact where required.

## Updating

Extract a complete matching bundle. The installer resets the host/provider before and immediately after verified package deployment. A second, guided post-initialization reset is now part of the normal update: open Win+W once, let the cards begin loading, close the panel, and press Enter. It resets only the current Windows session and exits. No fixed delay, background watcher, task or WSL restart is used.

For a package-only update on the protected layout, repeat step 1. The layout migration preserves the existing Linux integrations and credentials. If a later release explicitly requires service integration, update DevStack in Ubuntu and keep Ubuntu running. Run this separately in ordinary Windows PowerShell:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$dir\Install-DevStackWidget.ps1" -SkipFrontend
```

The separate integration uses `-SkipFrontend`. Existing WSL telemetry stays untouched. If you need to complete setup separately or use a noninteractive installation, pass `-DeferFirstOpenReset` and run `Complete-WidgetUpdate.ps1` explicitly after setup. The finalizer can also be rerun without redeploying the MSIX. Verify both cards after reopening; CI cannot confirm the actual Windows shell lifecycle on your machine.

## Diagnostics

Windows PowerShell:

```powershell
Get-AppxPackage -Name 'MrSSdev.DevStack.Widgets' | Select-Object Name, Version, Status
wsl.exe --list --running
$settings = 'C:\DevStack\Widgets'
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
