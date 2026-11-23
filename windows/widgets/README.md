# DevStack — Windows 11 widgets (Win+W)

Two **separate native widgets**, built from the same public DevStack repository:

**Current draft PR #2: responsive native widget UI (pending ASUS visual QA).**
Windows 11 supports **small / medium / large** sizes for each widget. These are
six purpose-built Adaptive Card layouts, not scaled copies. Original coloured
glyphs are generated as transparent PNGs by CI, packaged in MSIX and embedded
as data URIs; the cards don't fetch icon graphics over the network.

- **Ubuntu**: quick ON/OFF status, verified Start and two-step confirmed Stop,
  one action in the small size and more context on medium/large.
- **DevStack**: one-glance service status, four compact Nginx/PHP-FPM/MariaDB/
  Redis tiles on medium/large, and native **Projects** / **Add project** website
  actions (when Ubuntu is online). The small size intentionally has one link.
- **Navigation**: `https://devstack.test/#/projects` and `#/new` are
  authenticated React hash routes. URLs do not call `wsl.exe` and do not
  automatically start Ubuntu. The user logs in normally if required.
- **Theme**: default Windows 11 host colours, readable in light/dark modes,
  with restrained DevStack/ROG accents rather than a custom web renderer.

The last live-tested pre-redesign Windows build is `e2730b0`, which the ASUS
owner confirmed really starts and terminates Ubuntu from native widget buttons.
The redesigned layouts require a fresh on-device test before acceptance.

* **WSL / Ubuntu** — Windows-only state probe; deliberate Start and two-step Stop of
  `Ubuntu-26.04` only; no system-wide WSL shutdown.
* **DevStack** — passive availability probes and **Open dashboard**. No WSL start/stop
  controls. When Ubuntu is off, it displays offline status and makes no service requests.

Both cards are registered by one C# COM provider and one x64 MSIX package. No
long-running WSL watcher, background service, local IDE, or .NET SDK is required
on the target device to compile the provider: GitHub Actions does the build.
The provider uses the official widget host **Activate/Deactivate** callbacks:
opening Win+W triggers an **immediate asynchronous state check** and shows the
last known card without resetting it to an artificial loading state. WSL status
is published as soon as the read-only `wsl.exe --list --running --quiet` command
finishes; HTTPS/TCP reachability follows independently. Duplicate refresh
requests are coalesced, not silently discarded. While all cards are hidden,
the 20-second refresh timer is disabled and in-flight probes are canceled;
no WSL command or network poll is started. After an idle period the COM
server can exit, independently of whether cards remain pinned.

**A fresh live result cannot be guaranteed at the exact instant Win+W opens:**
WSL and network probes are asynchronous, so the previous rendered result is
displayed immediately and its last-check timestamp indicates freshness.
The provider does not keep Ubuntu awake just to maintain a cache.

## Architecture and security boundaries

* Only explicit user actions call `wsl.exe -d Ubuntu-26.04 ...` or
  `wsl.exe --terminate Ubuntu-26.04`. The second action requires an extra
  confirmation. Neither the passive refresh nor the DevStack card can start WSL.
* The state probe uses `wsl.exe --list --running --quiet` (safe/offline) and
  Windows HTTPS / TCP. The PHP/Nginx check requests the existing public
  `https://devstack.test/api/session` endpoint (normal certificate validation,
  no bypass). MariaDB and Redis checks only test Windows loopback TCP ports
  3306 and 6379. **These are connectivity hints, not authenticated
  `systemctl is-active` results.** TCP forwarding/firewall may show
  "Brak połączenia" for a healthy Linux service.
* No password, app session cookie, CA key, internal project list, local path
  or sensitive machine settings are sent to GitHub.
* No changes are made to the PHP login boundary, DevStack `hosts` task, CA
  or WSL lifecycle config.

## GitHub Actions build

Workflow: [`.github/workflows/widgets.yml`](../../.github/workflows/widgets.yml).
On every change to this directory, it uses `windows-latest` to generate
original PNG assets, check the manifest, run pure C# tests, build an x64 MSIX,
sign it with a **temporary self-signed development certificate**, and upload
a `DevStack-Windows-Widgets-x64` artifact containing the MSIX and **public
CER only**. The private signing key is generated and deleted on the runner.

Because the certificate is temporary, subsequent builds have different signer
thumbprints and may require uninstall/trust steps again. A future production
pipeline should use a persistent certificate stored in GitHub Secrets or a
recognized signing provider, and must never commit a PFX or password.

## ASUS installation (after CI succeeds)

1. Go to the **Actions → DevStack Windows Widgets → successful run →
   Artifacts** for this repository. Download `DevStack-Windows-Widgets-x64`.
2. Extract the archive, review the `DevStack-Widgets-Dev.cer` thumbprint,
   and verify the run originated from this repository. Never import an
   unrelated certificate.
3. The MSIX is self-signed for testing. In an **administrator PowerShell**,
   explicitly trust the public certificate for app installation:
   `Import-Certificate -FilePath .\DevStack-Widgets-Dev.cer -CertStoreLocation Cert:\LocalMachine\TrustedPeople`
4. Install with `Add-AppxPackage -Path .\DevStack-Widgets-x64.msix`. If
   Windows asks for Developer Mode for testing, enable it intentionally in
   **Settings → System → For developers**, not via a registry bypass.
5. Press **Win + W** → Add widgets. Pin **WSL — Ubuntu** and **DevStack — usługi**
   separately. Check offline state, deliberate Start/Stop, services and URL.

The original `DevStack` Windows app certificate is **not the same certificate**
as the Ubuntu mkcert CA. Never import this development signing certificate
into web/SSL trust as if it were a web CA.

**Staged ASUS testing (23 Nov 2026):** COM activation, two pinned cards,
reopen persistence, fast WSL ON/OFF state, DevStack HTTPS/TCP availability,
and deliberate WSL Start/Stop through the widget are **confirmed on device**.
The redesigned six layouts, their project deep links and all resize transitions
still require testing in the Windows 11 widget host. The Stop→Cancel and
confirmation-timeout paths, hidden-board battery/RAM, logout/login and
sleep/resume are separate acceptance criteria. CI success is not equivalent
to passing this physical UI test. The public PR stays draft until validation.
