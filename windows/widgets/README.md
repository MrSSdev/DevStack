# DevStack — Windows 11 widgets (Win+W)

Two **separate native widgets**, built from the same public DevStack repository:

* **WSL / Ubuntu** — Windows-only state probe; deliberate Start and two-step Stop of
  `Ubuntu-26.04` only; no system-wide WSL shutdown.
* **DevStack** — passive availability probes and **Open dashboard**. No WSL start/stop
  controls. When Ubuntu is off, it displays offline status and makes no service requests.

Both cards are registered by one C# COM provider and one x64 MSIX package. No
long-running WSL watcher, background service, local IDE, or .NET SDK is required
on the target device to compile the provider: GitHub Actions does the build.
The provider only polls when an active card is being shown; it shuts down after
a period without active cards.

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

**Status:** source implementation, not yet device-tested. Acceptance requires
a successful MSIX CI build and an actual two-widget test on Windows.
