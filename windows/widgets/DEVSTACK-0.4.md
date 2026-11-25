# Windows widgets: technical reference

Applies to widget package **0.4.2.0** and its current installer. See [README](README.md) for end-user setup. This document describes implementation boundaries, not a workstation-specific deployment log.

## Provider and UI

`DevStack.Widgets.csproj` targets .NET 8, Windows x64 and Windows App SDK 2.7.0. One out-of-process COM provider registers `DevStack_WSL` and `DevStack_Services`. Both definitions expose one large size. The manifest uses an Adaptive Card metadata URL with a local resource filter for each HTML page.

| Card | Reserved resource origin | Native implementation |
| --- | --- | --- |
| Ubuntu | `https://devstack-widgets.invalid` | `WidgetProvider`, `WslWebContent`, `WslResources`, `StateProbe` |
| DevStack | `https://devstack-services.invalid` | `DevStackWidget`, `DevStackWebContent`, `DevStackData`, `DevStackNavigation` |

The `.invalid` origins are intercepted by the provider, not external services or local web servers. HTML, CSS, icons and JavaScript are packaged in MSIX. Unknown resources are denied locally rather than falling through to network fetching. Production pages have content security policies with hashes for inline code/styles; no runtime CDN is used. Design fixtures under `design/` are build/test references, not deployed simulations.

The dark outer card is transparent. Windows owns its backing surface, system menu and initial loader. No provider API guarantee is made that the WebView survives every panel closure. Short spinner/background transitions are a known limitation, not an instruction to keep Ubuntu or polling active.

[Microsoft Web Widget provider documentation](https://learn.microsoft.com/en-us/windows/apps/develop/widgets/web-widget-providers) describes the host requirements, resource interception and messaging surface.

## Native message boundary

Pages use one-argument `window.chrome.webview.postMessage` and native replies. The provider validates the widget definition/session, client correlation, increasing sequence, payload bounds, active state, host-expanded authentication and freshness. A resource-read fallback can supply display data but cannot grant control or reveal a confirmation capability.

WSL lifecycle commands and DevStack service actions remain separate. There is no page-provided shell command, executable, arbitrary distro, arbitrary systemd unit or arbitrary browser URL. Stop/restart confirmation is one-use, service-bound where applicable and expires after 30 seconds. Cancel, navigation and hiding invalidate unused confirmation. A request already dispatched can finish after hiding; it is never automatically retried after an uncertain outcome.

## Read scheduling

Activation requests a fresh read; the service card's visible refresh period is 20 seconds. Visibility changes cancel in-flight reads and disable hidden periodic work. A passive running-distribution list does not start Linux. Linux endpoints are queried only after confirming Ubuntu is running. A short-lived socket handler is started per request; there is no resident Linux sampling loop.

The Windows provider may remain resident before its idle exit. No guarantee of zero RAM or identical power behavior across Windows/WSL versions is implied by stopping probes.

## Linux integration

| Port | Interface | Purpose | Identity |
| --- | --- | --- | --- |
| 43871 | `127.0.0.1`, authenticated read-only | Ubuntu procfs/filesystem measurements | Sandboxed dynamic reader |
| 43872 | `127.0.0.1`, authenticated `GET /v1/services` | Service cgroup measurements and project count | Dedicated non-login `devstack-widget-reader` |
| 43873 | `127.0.0.1`, authenticated health/restart | Guarded `POST /v1/restart` | Restricted root helper |

The service reader explicitly selects its local user/group and retains `DynamicUser=yes` sandbox defaults; the preallocated identity avoids the dynamic-account D-Bus compatibility issue. It has no login, home or supplementary groups. Setup rejects conflicting identities. No D-Bus policy, NSS configuration or broad credential permissions are changed.

systemd supplies protected files through `LoadCredential`. The reader returns only the registry entry count, not project names, paths or secrets. The control helper accepts exactly four unit mappings: `nginx.service`, `php8.5-fpm.service`, `mariadb.service`, `redis-server.service`. It checks Host, rejects browser Origin and malformed/oversized framing, validates a separate bearer token and accepts only the fixed JSON request schema. A lock and pre-consumed request IDs prevent concurrent/replayed operations. It is still a privileged trust boundary, not an independently audited security guarantee.

## Measurements

Ubuntu CPU/RAM reflect the WSL kernel; disk reflects `/`, and uptime reflects PID 1. Service memory is cgroup `MemoryCurrent`, including cache, and CPU is an on-demand `CPUUsageNSec` delta where 100% is one logical CPU.

Windows independently checks endpoint reachability. Nginx/PHP share a single end-to-end HTTPS `/api/session` probe. MariaDB/Redis timings measure TCP connection setup. These are not query/command benchmarks. Unavailable values stay null/dashes. The display and post-restart verification distinguish endpoint availability from systemd state.

## Restart result lifecycle

`DevStackWidget` owns `restarting`, `verifying`, `success`, `error`, `unconfirmed`, `not-run` and recovery/availability states. After explicit confirmation the details popover closes; a small icon and service-specific line appear under the project count, never as an overlapping toast.

Success requires a newly accepted post-command snapshot with running Ubuntu, valid integration, the selected unit active and its endpoint reachable. An unrelated healthy service, stale/incomplete readings or endpoint reachability alone cannot clear a failure. Later healthy reads replace an error with a recovery notice. Unknown/not-executed commands preserve that qualification rather than claiming restart success.

Success/recovery notices expire at an absolute timestamp 30 seconds later; closing the panel does not pause expiry. Clearing a notice performs no extra read. Errors remain unresolved during the provider's lifetime until verified recovery or a superseding action. Notices survive page recreation within that lifetime but are not persisted over full provider-process exit. No capability is restored from a display cache.

## Install/update behavior

`Install-Widgets.ps1` validates expected package identity/version and signer, imports only the matching development CER into TrustedPeople, verifies signature trust, performs an in-place update, checks installed version/Status Ok and attempts a final reset of `WidgetBoard` and `DevStack.Widgets`. A prior reset prevents stale processes during deployment; the final one handles processes relaunched during installation. Failed deployment skips the final reset. Reset errors warn instead of silently asserting success.

`Install-WslTelemetry.ps1` and `Install-DevStackWidget.ps1` are deliberate, one-time Linux setup paths with exact UTF-8 stdin transport. They refuse a stopped distribution. Routine Windows package updates do not require either setup again. Service integration health checks are reads, not restarts. Default service setup updates only the bundled static frontend after successful health checks, preserving a previous-index backup and old hashed assets; `-SkipFrontend` opts out.

## Configuration and diagnostics

Windows settings are stored in `%LOCALAPPDATA%\DevStack\Widgets`: `telemetry.json` and `services.json` contain credentials and must not be shared. The bounded `wsl-bridge.log` contains event codes, hashed widget identifiers and sanitized exception type/HRESULT, not tokens or raw message bodies. Review diagnostics before posting.

Linux integration files are under `/usr/local/lib/devstack-widgets`, `/etc/devstack-widgets` and `/etc/systemd/system/devstack-widget-*`. The service-control replay ledger is under `/run/devstack-widget-control`. Do not remove protected state or reset application services to repair a UI issue.

## Build and validation

The workflow `.github/workflows/widgets.yml` generates page assets, runs manifest/installer-order and provider tests, checks production pages in Chromium, builds the static frontend, compiles x64 MSIX, signs it with a temporary development key, deletes the private PFX, and uploads the matching installer bundle. The public CER is included; the private signing key is not.

The Linux suites cover request bounds, authentication, replay and reader identity. `.github/workflows/widget-reader.yml` exercises the real systemd reader sandbox; `.github/workflows/widgets-installer.yml` checks telemetry installer behavior. Browser fixtures mock native message transport and provider policy tests mock OS actions. These tests complement, not replace, actual host validation and do not certify every sleep, battery or regional configuration.

Use the exact workflow source revision and matching bundle when reporting a defect. The functional widget release has received device-user acceptance; broader application integration and multi-machine reliability testing are separate from that acceptance.
