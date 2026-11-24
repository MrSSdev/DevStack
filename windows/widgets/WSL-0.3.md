# WSL 0.3: approved prototype, real native bridge

This revision replaces **only the Ubuntu/WSL content**. The DevStack services
widget still uses the unchanged `WidgetDesign.DevStack` renderer and existing
service probes. The PR remains a device-test candidate, not a merge to main.

## Design source

The user explicitly accepted `DevStack-WSL-prototyp-01.html` in this conversation.
Its SHA-256 is `cb44936549a1a2b5fc9176a07790ecc0b7ed56a2268b0142df28e9367fdecfd3`.
An exact, deterministically gzipped copy is stored under `design/`.
`Build-WslWeb.py` verifies that hash and extracts the approved SVGs, card markup
and **unmodified original CSS**. The prototype workspace/editor, simulation,
fake readings and scenario buttons are not shipped as the widget runtime.

The Windows adapter changes only page sizing, scrolling, demo copy and native
header integration. Windows owns the system ellipsis/Move/Remove overlay; its
click target cannot be replaced. The original internal card layout remains the
reference. A supported host exposes the full header region; on a shorter web
viewport the duplicated content header is hidden rather than clipping actions.

## Why HTML rather than a new Adaptive Card approximation

Windows Web Widgets carry an Adaptive Card 1.6 payload with `metadata.webUrl`.
The existing COM provider now also implements `IWidgetResourceProvider` and
`IWidgetProviderMessage`. All page resources are returned from the signed MSIX
under an allowlisted reserved origin (`https://devstack-widgets.invalid`).
There is **no website server needed to draw the card**, no external graphics,
fonts or scripts, no localhost HTML server and no dependency on Ubuntu being on.
Unknown requests receive a local 403 page, never a network fallback.

Microsoft restricts Web Widgets to EEA users and supporting host versions.
An unsupported host receives an explicit explanatory fallback, not a claim that
it is displaying the approved prototype. Actual host support and system chrome
still have to be verified on the ASUS. Do not bypass regional restrictions.

## Real interactions, not the prototype's simulation

- Every page instance has a random session nonce, client handshake, increasing
  sequence counter and bounded JSON messages. Only known verbs are accepted.
- The host-expanded authenticated-context flag gates Start/Stop. The page
  cannot submit a distribution name, command, arbitrary URL or executable.
- Stop requires a server-held one-use random confirmation token, valid for 30
  seconds. Cancel, expiry, reload and hiding clear that confirmation.
- Start/Stop use the already tested explicit WSL commands, recheck the current
  state and verify the result. Repeated clicks are blocked across instances.
- Once explicitly issued, a command may finish after closing the panel; hiding
  cannot undo it. No additional passive verification is started while hidden.
- Visible activation triggers the existing fast read; the timer and in-flight
  resource requests stop when the relevant widget is hidden. Merely opening
  the card does not invoke `wsl -d` or a WSL UNC path.

## Real CPU / RAM / disk / uptime

The HTML never receives credentials or calls a Linux endpoint. The Windows
provider requests authenticated, read-only measurements at loopback port 43871
only when Ubuntu is known to be running and the WSL card is visible/ready.
Without the optional collector the UI shows dashes and an explanation, not demo
values or fabricated zero usage. Failed, stale and unknown states are distinct.

`Install-WslTelemetry.ps1` is a deliberate one-time setup, run by the user's
Windows account **after Ubuntu is already running**. It installs a fixed Python
stdlib collector and a systemd socket/service pair. The socket is loopback-only;
a short DynamicUser process is started for each authenticated request and exits.
There is no resident polling Python process. The kernel socket remains listening
while Ubuntu is running. The script offers `-Remove` to uninstall this module.

A per-install random bearer secret is stored in a Windows user/SYSTEM-only file
and a root-only Linux file, delivered to the DynamicUser via systemd credentials.
The endpoint rejects unexpected Origin/Host, non-GET methods, duplicate headers,
oversized requests and invalid tokens. It has no control/restart/write endpoint.
The collector runs with read-only filesystem protections and process limits.

Measurement meaning:
- CPU: delta of `/proc/stat`, normalized across logical CPUs; shared WSL kernel.
- RAM: `MemTotal - MemAvailable` in `/proc/meminfo`; shared WSL kernel, not total
  Windows RAM. With multiple distributions these two readings may be shared.
- Disk: used/capacity of this distro's `/` filesystem, **not VHDX physical size**.
- Uptime: time since this distro's PID 1 started, not Windows uptime.
The metric popovers explain these scopes. No fabricated per-service readings.

## Build and verification

CI verifies the approved-source hash, unchanged inner geometry, dark/light
states, production JavaScript controls via a mocked native transport, absence
of simulated readings, offline/unknown/error behavior, confirmation expiry,
keyboard cancel and resource popovers. Separate C# tests exercise the actual
provider state machine against mocked WinRT/WSL transport. Linux unit tests cover
collector authorization, request bounds, parsing and real procfs collection.
The signed Windows build separately checks actual WinRT API compilation.

Tests and previews are **not** evidence that the actual ASUS Windows host has
passed. CPU/RAM scope, local socket reachability, native header placement,
regional host support, actual commands, sleep/resume and hidden-board resource
behavior need device validation. No completed acceptance is claimed prematurely.

## Installation

Use MSIX and CER from the same successful workflow and the fixed directory
`%USERPROFILE%\Downloads\DevStack-Windows-Widgets-x64`.
Run `Install-Widgets.ps1` in an elevated PowerShell as the normal Windows user.
The script checks identity/version and matching signer before installation and
uses an in-place update rather than deleting the working package first.
Expected package version: **0.3.0.0**.

Then intentionally start Ubuntu and run `Install-WslTelemetry.ps1` once for live
resource readings. Neither script changes the DevStack website or service setup.
The unchanged DevStack project links still require the matching website routing
update if that has not yet been deployed.

Microsoft API references:
- https://learn.microsoft.com/en-us/windows/apps/develop/widgets/web-widget-providers
- https://learn.microsoft.com/en-us/windows/apps/develop/widgets/widget-header-customization
- https://learn.microsoft.com/en-us/windows/apps/develop/widgets/widgets-create-a-template
