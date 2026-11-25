# DevStack services widget 0.4.2

Implements the user's approved **DevStack-widget-prototyp-03.html**. No redesign:
transparent dark outer surface, original icons/type/layout, 2x2 service tiles,
response time and RAM in each footer, small information popover, confirmed
restart in that popover, plain project count line and two browser links.
The source prototype is retained with a pinned SHA-256. Its demo script is
NOT shipped; generated HTML/CSS uses production CSP and the native message bridge.

## Install (Windows account that owns Ubuntu)

Extract the complete archive into:
`%USERPROFILE%\Downloads\DevStack-Windows-Widgets-x64`.

1. Run `Install-Widgets.ps1` in an elevated PowerShell on the usual account.
   Expected version **0.4.2.0**. It updates in place; do not unpin, remove user
   state or reinstall `Install-WslTelemetry.ps1`.
2. Only for a new integration, start Ubuntu deliberately using the existing WSL widget, then run
   `Install-DevStackWidget.ps1` from this same bundle. This is a NEW integration
   setup, not replacement WSL telemetry. It refuses to run when Ubuntu is off.
3. The installer stops WidgetBoard and DevStack.Widgets before deployment,
   then always attempts the same reset after successful deployment and
   verification of the installed version and Status Ok. Reopen Win+W.
   No separate manual reset is normally needed. A failed deployment skips
   the post-update reset; a process-stop error produces a warning. Never
   reset Ubuntu or make this a per-open step.

The new setup installs two separate authenticated loopback socket listeners:
43872 provides service statistics, 43873 accepts one explicitly confirmed
service restart. Separate random read/control credentials stay in protected
Linux files and the current user's ACL-protected `services.json`. They never
enter HTML, logs, URLs or command-line arguments. Do not paste that file.
Existing WSL telemetry on 43871 and its credential remain unchanged.

By default setup also deploys the matching **static frontend only** for
`https://devstack.test/#/new` and `#/projects`. The old index is backed up in
`/var/backups/devstack/widget-frontend-*`; old hashed assets are retained.
No PHP/API, password, CA, database, project registry or WSL setting is changed.
`-SkipFrontend` opts out, but exact page navigation then requires a dashboard
that already supports these hash routes. Normal DevStack login is preserved.

## Truthful readings

Availability/latency is measured from Windows: one end-to-end HTTPS request
for Nginx and PHP-FPM, TCP connection setup for MariaDB and Redis. The two web
timings are the same request, NOT separate per-server processing times.
RAM is systemd `MemoryCurrent` for each service cgroup (includes cache), not
sum of RSS. CPU is a short on-demand delta of CPUUsageNSec; 100% is one logical
CPU. Unavailable accounting is shown as a dash, never a fictional zero.
Project count is the number of entries in DevStack's protected registry;
only the count leaves Ubuntu, not names, paths or credentials.

Linux uses one short process per request, no resident collector loop. Windows
probes only when this widget is active, cancels reads on hide and never runs
`wsl -d` to collect status. WSL offline means no service requests or control.
WSL layout, collector and its resource/command implementation are unchanged;
only the top-level provider dispatches DevStack events to a separate handler.

## Restart security and limits

A restart needs active/authenticated native page, fresh WSL and service state,
a service-bound one-use confirmation expiring after 30 seconds, and a second
explicit click. Hide/navigation/cancel invalidates confirmation. The Linux
control helper accepts only POST to one exact endpoint, no browser Origin,
valid bearer credential, strict body and four fixed unit names: nginx.service,
php8.5-fpm.service, mariadb.service, redis-server.service. There is no arbitrary
command, unit or WSL start endpoint. Only one restart runs at once; request IDs
are recorded before execution. No automatic retries after a timeout.

A confirmed request already sent may finish after the panel is closed. Its
result is checked by the next visible read. Save work before testing a real
restart: the selected service's active connections can be interrupted.

## Validation / device gate

Run unchanged WSL tests plus service authentication/parser/replay tests, actual
provider policy under mocked WinRT/OS, original-prototype geometry/interaction
checks in both themes, static frontend build, x64 MSIX compilation and signing.
Tests with mocked transport do NOT replace the ASUS check. Confirm the real
systemd helper, four timings/RAM, count, browser targets, Cancel, one selected
restart and reopen/offline behaviour on-device. Do not mark sleep/power or
post-upgrade installation acceptance passed without corresponding evidence.

The already accepted native loading spinner/background transitions remain.
No further cosmetic experiments, no changes to public main; PR remains draft.

## 0.4.1 — restart feedback (ASUS follow-up)

The accepted 0.4.0 installation with the reader fix succeeded on ASUS. The
user observed a confirmed restart and live data; the old verification popup
covered controls and stayed visible after reopening. Confirmation is retained;
on explicit confirmation the service details close. A compact inline message
with a service name and icon appears directly below the project count. The
provider transitions restarting -> verifying -> success/error only from a new
post-command snapshot (selected systemd unit active AND Windows probe reachable,
with valid integration). Command failure/unknown outcome is not treated as a
successful restart, and is never retried automatically. Success has an absolute
30-second expiry; hidden time counts. A verification error or unconfirmed
command remains visible until another confirmed restart or provider exit.
No read is scheduled solely to clear a notice; hidden verification waits for
normal visible reads. State is kept in provider memory across page recreation,
not persisted across a full provider-process restart. No credential or command
capability is cached by the page. WSL/UI geometry, Linux reader fix, frontend,
allowlist, credentials and project routes are otherwise unchanged.

Existing 0.4.0 users who completed the reader fix only need Install-Widgets.ps1
from the matching new signed package. Do NOT run either Linux setup again.
The first device test is Cancel; then a single confirmed restart and verify
popover dismissal, inline progression, expiry after closing/reopening, and
normal project links. Automated validation does not replace device acceptance.

## 0.4.2 — consistent popover dismissal and recovered service notices

WSL resource details now close on a click elsewhere inside the widget; clicks
inside details keep them open, another resource opens its own details, and
Escape/close still work. No change to WSL Stop confirmation, layout, metrics,
Ubuntu lifecycle or reader. Both document versions are bumped to avoid stale
cached JS after upgrading. The package version is 0.4.2.0.

The 0.4.1 error lifetime above is superseded: unresolved error, unconfirmed
command or not-run notices remain visible while the affected service is not
verified healthy. A new accepted read must show the same service's systemd
unit active AND its Windows HTTPS/TCP probe reachable, valid integration and
running Ubuntu. Another healthy service, a stale snapshot, just one of those
checks, missing data or an offline/hidden panel cannot resolve the error.
When health returns, replace the error with a service-specific recovery notice
for 30 seconds (absolute expiry including hidden time). If the command itself
was not confirmed or did not run, show availability with that qualification,
never "restart succeeded". This is passive verification only: no automatic
restart, extra polling, Linux setup, privilege or project route change. Like
0.4.1, notices are kept within the provider lifetime, not across process exit.
Existing configured 0.4.x users run only the matching Install-Widgets.ps1.

Regression tests cover WSL inside/outside/Escape/close in both themes and the
DevStack transition from unresolved errors to recovered/availability-only,
including invalid integration, inactive unit, unreachable port, expiry and
no extra restart. Actual Windows host acceptance remains a separate gate.

## Installer maintenance — automatic post-update reset

The post-deployment reset is now part of Install-Widgets.ps1 for all future
bundles. This is an installer-only change; existing 0.4.2.0 binaries and
telemetry need no reinstall for this maintenance. The Windows manifest test
checks ordering and exercises the installer deployment tail with mocked
commands (success, failed deployment, wrong version, bad status, missing
package). Actual Windows process termination remains a device test.
