## Version 0.5.0.0 update

Update DevStack in Ubuntu first (`bash scripts/update.sh`), then install the matching MSIX/certificate with `Install-Widgets.ps1`. While Ubuntu is already running, update the service integration using `Install-DevStackWidget.ps1 -SkipFrontend`. Do not overwrite the current dashboard with an older static frontend ZIP. Existing read/control credentials are preserved. Select services in the dashboard settings; changes arrive on the next visible widget refresh. The original WSL widget is unchanged. The following historical 0.4 instructions describe the previous release.

# DevStack Widgets — setup and architecture

## Install the bundle

Windows 11 x64, compatible Web Widgets host, Windows App Runtime 2.7, WSL2 Ubuntu-26.04 with user `ubuntu`, and DevStack are required for the complete setup.

Extract the complete matching bundle and run Windows PowerShell as Administrator under the WSL-owning account:

```powershell
$dir = "$env:USERPROFILE\Downloads\DevStack-Windows-Widgets-x64"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$dir\Install-Widgets.ps1"
if ($LASTEXITCODE -ne 0) { throw 'Package installation failed.' }
```

For first-time Linux integration, start Ubuntu explicitly, then run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$dir\Install-WslTelemetry.ps1"
if ($LASTEXITCODE -ne 0) { throw 'Telemetry setup failed.' }
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$dir\Install-DevStackWidget.ps1"
if ($LASTEXITCODE -ne 0) { throw 'Service setup failed.' }
```

Open **Win + W**, add Ubuntu and DevStack separately. For package updates, run only the matching `Install-Widgets.ps1` unless the release requires a Linux integration update. The installer verifies deployment and attempts a final reset of the Windows widget processes.

[Complete widget guide](https://github.com/MrSSdev/DevStack/tree/main/windows/widgets#readme)

## Architecture

One native C# provider (.NET 8, Windows App SDK 2.7) exposes two large cards. HTML, CSS, icons and JavaScript are packaged in MSIX. Each card has its own native messaging and resource boundary.

| Loopback port | Purpose |
| --- | --- |
| 43871 | Authenticated Ubuntu resource readings |
| 43872 | Authenticated service measurements and project count |
| 43873 | Authenticated service restart control |

Linux uses socket-activated handlers. Service reads run as a dedicated non-login account; the restricted control helper handles Nginx, PHP-FPM, MariaDB and Redis. Confirmation, session validation and bounded requests protect lifecycle and restart actions.

Configuration is stored under `%LOCALAPPDATA%\DevStack\Widgets` and `/etc/devstack-widgets`. Keep credentials private.

The provider refreshes visible cards and reads Linux metrics when Ubuntu is running. Ubuntu controls and DevStack service controls are separate.

## Build

The [Windows Widgets workflow](https://github.com/MrSSdev/DevStack/actions/workflows/widgets.yml) builds and tests the package and produces a signed x64 MSIX with the matching public development certificate and installer scripts.
