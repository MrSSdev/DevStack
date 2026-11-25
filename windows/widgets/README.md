# DevStack Widgets

Two independent large cards for the Windows 11 **Win + W** board, delivered in one signed x64 MSIX package. Current package version: **0.4.2.0**.

**Ubuntu** manages the lifecycle of one WSL distribution and displays its resources. **DevStack** displays local service availability, response time, memory and project count, offers guarded service restarts, and opens project screens in the system browser. The DevStack card never starts or stops WSL.

## Requirements

- Windows 11 x64 with the Widgets Board available. The package targets Windows build 22621 or newer, but that manifest minimum alone does not guarantee Web Widget support.
- A compatible Windows App Runtime 2.7 installation. The package includes its .NET runtime but does not bundle Windows App Runtime as self-contained.
- A host eligible for Microsoft's Web Widgets. Microsoft currently documents EEA availability; do not bypass regional restrictions. [Platform requirements](https://learn.microsoft.com/en-us/windows/apps/develop/widgets/web-widget-providers).
- WSL2 distribution named `Ubuntu-26.04`, with systemd and Python 3. The supplied Ubuntu Start action uses the Linux account `ubuntu`. These names are currently fixed in source; there is no distribution selector.
- A working DevStack installation and trusted local HTTPS for `devstack.test` for service readings and project navigation. The HTML cards themselves load from the MSIX even when Ubuntu is off.
- Windows Administrator access under the same Windows account that owns the distribution for MSIX installation. Do not switch to a different Windows administrator account: package registration and settings are per user.

## Download

Use a successful **DevStack Windows Widgets** run in [GitHub Actions](https://github.com/MrSSdev/DevStack/actions/workflows/widgets.yml) for the source revision you intend to install. Download the **DevStack-Windows-Widgets-x64** artifact, not the source snapshot or preview artifacts. GitHub may require sign-in and artifacts expire according to the workflow retention period.

Keep the complete bundle together:

```text
DevStack-Windows-Widgets-x64/
  DevStack-Widgets-x64.msix
  DevStack-Widgets-Dev.cer
  Install-Widgets.ps1
  Install-WslTelemetry.ps1
  Install-DevStackWidget.ps1
  DevStack-Frontend.zip
  READ-ME-FIRST.md
  telemetry/
  services/
```

The MSIX, CER and installer must come from the same run. CI creates a development signing certificate per run. Never mix a new MSIX with an old CER. This signing certificate is not the mkcert HTTPS CA and must not be added to the TLS root store.

## Installation

### 1. Install the Windows package

Extract the complete bundle to `%USERPROFILE%\Downloads\DevStack-Windows-Widgets-x64`. Extraction alone does not install the app.

Run **Windows PowerShell as Administrator, as your usual WSL-owning account**:

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $dir = "$env:USERPROFILE\Downloads\DevStack-Windows-Widgets-x64"
    $installer = Join-Path $dir 'Install-Widgets.ps1'
    if (!(Test-Path -LiteralPath $installer -PathType Leaf)) {
        throw "Installer not found: $installer. Check the extracted folder."
    }
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer
    if ($LASTEXITCODE -ne 0) { throw 'Widget package installation failed.' }
    Get-AppxPackage -Name 'MrSSdev.DevStack.Widgets' |
        Select-Object Name, Version, Status
}
```

The installer validates package identity/version and matching signer, imports the matching CER into `LocalMachine\TrustedPeople`, verifies the signature, stops `WidgetBoard` and `DevStack.Widgets`, and updates the package in place. It checks the installed version and `Status=Ok`, then **always attempts a post-update reset of those two Windows processes**. A deployment failure skips that final reset; a stop failure produces a warning. It does not terminate Ubuntu, remove pinned data, or change Linux services.

Open **Win + W**, use **+ / Add widgets**, and add **Ubuntu** and **DevStack** separately. Only the large size is provided. A native loading spinner and brief background-color changes are normal host behavior in some sessions; the package does not replace that initial system screen.

### 2. Optional Ubuntu resource readings — one-time setup

Without the collector, the Ubuntu card can show lifecycle state, but resource fields display dashes. Deliberately start Ubuntu first, then run from the same Windows account:

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $dir = "$env:USERPROFILE\Downloads\DevStack-Windows-Widgets-x64"
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$dir\Install-WslTelemetry.ps1"
    if ($LASTEXITCODE -ne 0) { throw 'Ubuntu resource setup failed.' }
}
```

The script refuses to proceed while Ubuntu is stopped. It installs a read-only loopback collector and writes protected per-user configuration. Do not rerun it for routine MSIX updates.

### 3. DevStack service readings and restarts — one-time setup

With Ubuntu deliberately running and DevStack already installed:

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $dir = "$env:USERPROFILE\Downloads\DevStack-Windows-Widgets-x64"
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$dir\Install-DevStackWidget.ps1"
    if ($LASTEXITCODE -ne 0) { throw 'DevStack widget integration setup failed.' }
}
```

This creates a dedicated non-login reader account, installs separate authenticated read/control sockets and checks their health. Health checks do **not** restart Nginx, PHP-FPM, MariaDB or Redis.

By default, setup also deploys the bundled **static dashboard frontend** for the exact `/#/new` and `/#/projects` routes. It backs up the previous `index.html` under `/var/backups/devstack/widget-frontend-*` and retains older hashed assets. It does not replace PHP/API code, database records, authentication, the CA or WSL settings. Use `-SkipFrontend` only when your installed dashboard already supports these routes. Existing Ubuntu telemetry is separate and remains unchanged.

After successful setup, the terminal reports active read/control listeners and `%LOCALAPPDATA%\DevStack\Widgets\services.json` exists. That file contains credentials; never post its contents.

## Routine updates

Replace the extracted bundle with a complete matching new build and run **only `Install-Widgets.ps1`**. The installer performs the post-update process reset; open Win + W again. Do not rerun either Linux setup unless a release specifically changes that integration. No local Visual Studio/.NET SDK is needed to install a CI-built package.

## Using the Ubuntu card

- **Uruchom Ubuntu** deliberately starts the configured distribution. **Zatrzymaj Ubuntu** requires confirmation and terminates only that distribution; save work first. **Anuluj**, hiding the card or expiry invalidates an unused confirmation.
- Click a resource tile for scope details. Click outside the small window within the card, click its close button, or press Escape to dismiss it. Clicking another resource opens that resource's details.
- The synchronization control requests a fresh visible-state read. Opening the card or passive reads never invoke a Linux command that starts a stopped distribution.

| Reading | Meaning |
| --- | --- |
| CPU | `/proc/stat` delta normalized across logical processors of the WSL kernel; it may be shared across distributions. |
| RAM | `MemTotal - MemAvailable`, not total Windows RAM; it may be shared across distributions. |
| Disk | Used/capacity of the distribution's `/` filesystem, in decimal GB; not the physical VHDX file size. |
| Uptime | Time since this distribution's PID 1 started, not Windows uptime. |

## Using the DevStack card

Each service tile displays availability, response time and memory. Click it for details. The small details window has **Uruchom ponownie**, followed by an explicit confirmation and **Anuluj**. Confirmation closes the details window and starts one bounded service command, with no automatic retry. Already dispatched commands can finish after the panel closes.

The inline result under the total-project count names the selected service:

1. Restarting, then verifying after command acknowledgement.
2. Success only after a new read confirms that service's systemd unit is active and its Windows probe is reachable, with a valid integration response.
3. Success/recovery hides after 30 seconds; time spent with the panel hidden counts. An unresolved error remains during the provider's lifetime. A later healthy read replaces it with a recovery notice. Unknown or unexecuted commands show availability with that qualification, not a claim that restart succeeded.

No services are restarted just to clear an error. Closing/reopening the page preserves a provider-held notice; a full provider process exit does not persist it.

**Dodaj projekt** and **Projekty** open the relevant local dashboard page in the default browser. Normal dashboard authentication applies. The line above them is the total registry count, not a project browser. When Ubuntu is off, this card shows unavailability without offering WSL Start/Stop.

| Reading | Meaning |
| --- | --- |
| Nginx / PHP latency | One shared end-to-end HTTPS request to `/api/session`, not independent per-process execution times. |
| MariaDB / Redis latency | TCP connection establishment, not a database query or Redis command benchmark. |
| Service RAM | systemd cgroup `MemoryCurrent`, including cache; decimal MB, not the sum of RSS. |
| Service CPU | Short `CPUUsageNSec` delta; 100% means one logical processor. |
| Project count | Count of entries in the protected DevStack registry; no project names or credentials are sent to the widget. |

Unavailable readings remain dashes. A reachable endpoint alone is not a claim about systemd state.

## Visibility and background work

The provider requests new status on activation and refreshes while the relevant card is active. Hiding it cancels pending reads and disables its periodic probing. No status collector repeatedly launches `wsl -d`; Ubuntu must already be running before Linux metrics/control endpoints are contacted. Socket activation starts a short process per request, not a permanent sampling loop. Listening sockets and a temporarily resident Windows provider do not imply ongoing measurement.

## Diagnostics

**Windows PowerShell — does not start Ubuntu:**

```powershell
Get-AppxPackage -Name 'MrSSdev.DevStack.Widgets' | Select-Object Name, Version, Status
wsl.exe --list --running
$settings = Join-Path $env:LOCALAPPDATA 'DevStack\Widgets'
'Telemetry present: ' + (Test-Path (Join-Path $settings 'telemetry.json'))
'Services present: ' + (Test-Path (Join-Path $settings 'services.json'))
$log = Join-Path $settings 'wsl-bridge.log'
if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log -Tail 80 }
```

**In an already-running Ubuntu terminal:**

```bash
systemctl is-active devstack-widget-metrics.socket devstack-widget-services.socket devstack-widget-control.socket
systemctl is-active nginx php8.5-fpm mariadb redis-server
sudo journalctl -u 'devstack-widget-services@*' -u 'devstack-widget-control@*' -n 40 --no-pager
```

Opening an Ubuntu terminal itself deliberately starts a stopped distribution. A socket being `active` proves it is listening, not that authenticated reads succeeded. A control health check is not a restart test.

| Symptom | Next check |
| --- | --- |
| `-File ... does not exist` | Extract the complete artifact, check for an extra nested folder, and verify the script beside the MSIX. |
| Old package version | Run `Install-Widgets.ps1`; extracting or configuring Linux does not install MSIX. |
| Missing runtime or unsupported host | Check Windows App Runtime and Web Widget host/region requirements; the explanatory fallback is not the full card. |
| Dashes or missing `services.json` | Inspect the integration setup result. It may have installed Linux files before a later health check failed. Do not delete credentials as a first step. |
| Service reader HTTP 503 | Read the setup stage/HTTP code and journal. Use the current dedicated-reader setup; see [reader compatibility](DEVSTACK-READER-FIX.md). Do not run the reader as root or relax D-Bus/token permissions. |
| Data works, controls disabled | Inspect the native bridge/authentication state. Read-only data does not authorize actions; never remove the safety gate. |
| Persistent loading after update | Check installer reset warnings. A one-time manual reset below is a recovery step, not a per-open workaround. |

```powershell
Get-Process -Name 'WidgetBoard', 'DevStack.Widgets' -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue
```

Then reopen Win + W. This targets Windows processes, not Ubuntu or its services. Do not restart application services or reinstall telemetry to solve a page-loading issue without diagnosis.

## Removal

Remove a card through its Windows **...** menu to unpin it without removing the app or Linux integrations. To uninstall the Windows package deliberately:

```powershell
Get-AppxPackage -Name 'MrSSdev.DevStack.Widgets' | Remove-AppxPackage
```

This does not uninstall Ubuntu, DevStack, the Linux socket units, or the separate signing trust. To remove only the WSL resource collector, deliberately start Ubuntu and run `Install-WslTelemetry.ps1 -Remove` from a trusted matching bundle. There is no bundled all-in-one service-integration uninstaller; do not delete protected project data or the HTTPS CA as part of widget removal.

See [technical reference](DEVSTACK-0.4.md), [release notes](CHANGELOG.md) and the repository [security policy](../../SECURITY.md).
