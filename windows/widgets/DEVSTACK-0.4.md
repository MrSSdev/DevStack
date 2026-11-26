## Version 0.5.3.0 — PWA navigation

The two DevStack navigation actions prefer a registered web+devstack handler, including the Edge AppX registration. Otherwise the exact HTTPS route opens in the default browser. No changes to card design, service controls, telemetry or notifications.

## Version 0.5.2.0 — protected Windows component layout

The current package uses C:\DevStack\Widgets for private configuration,
cache and logs. For an existing installation use the matching verified bundle
and its Migrate-DevStackWindows.ps1 entry point; see WINDOWS-COMPONENT-LAYOUT.md.
For fresh installation, record the intended user SID in ordinary PowerShell,
then run Install-Widgets.ps1 -ExpectedUserSid <SID> in elevated Windows
PowerShell 5.1. Run any deliberately needed Linux integration separately
in ordinary PowerShell. Existing credentials are preserved by the migration,
not regenerated. The first-open reset is unchanged.

The remainder of this file is historical documentation for older packages.

## Version 0.5.1.0 update — first-open reset

This maintenance release changes the Windows installation sequence, not the widget UI or provider behavior. If the release requires service integration, use `Install-Widgets.ps1 -UpdateServiceIntegration` after updating DevStack in Ubuntu and while Ubuntu is already running. The script preserves the dashboard and WSL telemetry. For a package-only update, omit the switch. After deployment and any integration setup, follow the prompt: open Win+W, wait for the cards to begin loading or display content, close the panel, then press Enter. The installer resets the widget processes again and exits. Reopen Win+W and verify both cards. To finish manually deferred setup, run `Complete-WidgetUpdate.ps1` with the same process-scoped PowerShell execution policy. No scheduled task or background process is created. No need to reinstall a working 0.5.0.0 solely to exercise this maintenance change.

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

Open **Win + W**, add Ubuntu and DevStack separately. For package updates, run only the matching `Install-Widgets.ps1` unless the release requires a Linux integration update. The installer verifies deployment and includes the guided first-open reset described above.

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
