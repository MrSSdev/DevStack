# DevStack services widget 0.4.0

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
   Expected version **0.4.0.0**. It updates in place; do not unpin, remove user
   state or reinstall `Install-WslTelemetry.ps1`.
2. Start Ubuntu deliberately using the existing WSL widget, then run
   `Install-DevStackWidget.ps1` from this same bundle. This is a NEW integration
   setup, not replacement WSL telemetry. It refuses to run when Ubuntu is off.
3. If an old host session remains after update, close WidgetBoard and
   DevStack.Widgets once. Never reset Ubuntu or make this a per-open step.

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
WSL UI, JS, collector and its resource/command implementation are unchanged;
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
