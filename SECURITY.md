# Security

DevStack is intended for trusted local development. Keep the dashboard on loopback, use password authentication and protect your Windows account, Ubuntu files and backups.

## Dashboard and services

The dashboard uses authenticated sessions, CSRF/origin checks and a restricted local privileged helper. Per-project PHP-FPM pools currently run as the same configured developer account. Separate pools do **not** isolate projects from each other. Use trusted project code and keep database credentials and environment files private.

[Project isolation and AI integration](docs/PROJECT-ISOLATION-AI.md) defines the planned boundary; it is not an implemented security feature. Do not grant untrusted agents project access on the assumption that DevStack currently sandboxes them.

Choose password authentication during installation. Passwordless mode grants administration access to local clients that can reach the dashboard. To configure it in Ubuntu:

```bash
sudo php /opt/devstack/scripts/configure-auth.php --password
```

### Framework installers without a panel password

Password authentication is recommended. Installing a framework or updating
its official tool can execute third-party Composer/npm code as the Linux
developer account. On a trusted local-only workstation the administrator
may explicitly allow these operations in the existing passwordless mode:

```bash
sudo php /opt/devstack/scripts/configure-auth.php --allow-local-installers --i-understand-local-risk
```

This is a one-time permission, not a browser setting. It leaves the panel
authentication mode unchanged and is stored in a root-controlled
`/etc/devstack/engine-access.mode` file. PHP and the privileged local helper
both enforce the policy; an absent/invalid consent file denies passwordless
framework operations. Ordinary authenticated operation needs no such flag.
HTTPS, same-origin/CSRF checks, peer identity, the adapter allowlist and the
unprivileged systemd worker are unchanged. Do not expose the passwordless
panel to a LAN or other untrusted clients. Local reachability alone does not
authenticate a person; any client that can reach a passwordless panel may
request installation or updates.

Revoke the permission or inspect status at any time:

```bash
sudo php /opt/devstack/scripts/configure-auth.php --disallow-local-installers
sudo php /opt/devstack/scripts/configure-auth.php --status
```

The permission persists across code updates until explicitly revoked.
Revocation blocks new framework operations, not an already-running installer.

### Project framework badges

Project versions are best-effort labels from installed Composer metadata, read
by a fixed isolated Python process launched by systemd with the configured
developer identity, read-only home and system directories, no new privileges,
and inaccessible protected DevStack state. The daemon passes only registered
project slugs.
The reader rejects symlinks and non-regular or oversized metadata files, executes
no project code and returns only recognized package names and bounded versions.
Missing, unreadable or malformed metadata does not block the dashboard. Labels
are informational, not a security or integrity attestation.

### Email preview isolation

The ordinary dashboard keeps its restrictive CSP, with frame sources limited
to the dedicated mail-message path prefix. All ordinary API/HTML responses
remain non-frameable. An authenticated preview response alone is frameable
by the dashboard and has CSP sandbox plus an iframe sandbox limited to
allow-popups and allow-popups-to-escape-sandbox. The preview retains an
opaque origin, no scripts, same-origin access, forms or top navigation.
The HTML5 renderer reconstructs elements and attributes, validates absolute
HTTP(S) links and forces a separate browsing context without opener or
referrer. No sender-supplied target, ping or download attribute is retained.
It strips refresh/base tags and nested frames, bounds input/nesting and
inlines only bounded raster attachments. CSS is permitted only in the
preview document. By default its CSP prevents external network resources,
including CSS URLs. Enabling external images for the selected message allows
HTTPS image requests in the browser, including CSS background images; it
does not authorize PHP to fetch remote URLs. Such requests can disclose
message opening to a sender or tracker. No referrer is sent. External
stylesheets, fonts, scripts and non-HTTP(S) links remain blocked; ordinary
attachment downloads retain their attachment disposition.

### DevStack Mail CLI

`devstack-mail` is for the trusted developer and their tests. It reads the
shared mailbox through the fixed loopback API of DevStack-managed Mailpit
v1.34.0. The adapter uses only the readiness check, the bounded message
list, and the raw MIME endpoint for a validated message ID. In this Mailpit
version the JSON message-detail endpoint marks a message read. Raw MIME
retrieval preserves the read flag and leaves the panel and notification
journal alone. The CLI has no operations for mailbox changes and uses no
environment proxies, redirects or supplied request destinations.

Text, HTML and extracted links are data. The tool neither renders HTML nor
opens URLs, and error diagnostics exclude message content. JSON output can
contain reset tokens and verification links. Keep command output out of
shared logs, Playwright traces and committed artifacts. An origin filter
does not verify the sender or make returned content trustworthy.

Recipient addresses, subject lines, origins and project names are selectors
available to the caller. They confer no authority. Keep the direct Mailpit
API and this developer adapter outside managed agent sandboxes. Future
access requires per-operation authorization tied to a trusted project
registration and the agent session. See [DevStack Tools](extensions/devstack-tools/README.md)
and the [AI integration plan](docs/PROJECT-ISOLATION-AI.md).

### Installed extensions and project instructions

Core discovers extensions only at approved locations and reads the active,
root-owned release with bounded, non-following reads and a shared release
lock. A manifest selects known schema components, data-only text and typed
operations. The panel shows instruction Markdown as text and offers terminal
commands only for copying. Authentication, CSRF, and the existing notification
journal apply to the instruction controls. Installation and rollback stay
with the trusted terminal publisher.

Project writes use a restricted worker running as the configured developer.
It checks descriptor-relative directories and recorded link identities.
User-owned skills, existing manual links and AGENTS.md are outside managed
file ownership. A user may explicitly migrate a verified legacy link.
Declaring or installing an instruction confers no permission to use a tool.
Do not expose the privileged socket, administrator panel session, direct
Mailpit transport, or shared DevStack Mail command to managed isolated agents.

### Notification journal and PWA

The authenticated, CSRF-protected notification API stores bounded events
in the existing private devstackweb directory. The unprivileged systemd
timer polls the fixed Mailpit API and read-only daemon snapshot; no worker
starts WSL or executes arbitrary user commands. No browser-provided notice
text or route is stored. Clear/delete remove records, not underlying
resources. The PWA service worker does not cache authenticated content.
The intended system notification transport is local Windows WinRT,
explicitly selected with a protected root-owned marker. The fixed launcher
runs as the Ubuntu developer, not root, through a bounded one-shot systemd
unit. Only a validated event ID, category and severity cross the WSL
boundary. No Internet notification provider, browser subscription or
VAPID key is needed for this transport. Windows installation ACLs protect
the launcher; its user-writable installation must not contain code
executed by an elevated scheduled task. Native notification delivery
respects Windows settings. Presence leases and single-event delivery
claims reduce duplicate notifications; closed-window detection is
best-effort, not an authorization boundary.
The legacy browser Web Push sender and subscription creation API are
retired; no valid WinRT marker means no system delivery. Only the old
subscription metadata is removed from the live journal. Browser-side
unsubscribe is best effort. Old VAPID files and historical backups remain
private and inert; no global browser or distro settings are changed.

## Windows integration and widgets

Review privileged installation scripts. Verify the public mkcert CA fingerprint before importing it into Windows. Keep the private CA key inside Ubuntu. Install the widget MSIX using its matching public code-signing certificate and installer from the same trusted build.

Widget resource and control interfaces use authenticated loopback endpoints. Service restart and Ubuntu stop require explicit confirmation. Windows hosts synchronization is restricted to the managed block.

Protect `%LOCALAPPDATA%\DevStack\Widgets\telemetry.json` and `services.json`, as well as Linux credentials under `/etc/devstack-widgets`. Keep tokens and configuration files out of issue reports.

## Backups and reporting

Keep [verified backups](docs/BACKUP-RESTORE.md) in secure storage. Report suspected vulnerabilities privately through GitHub security reporting or contact the maintainer privately. Sanitize logs and avoid publishing credentials, private keys, project data or database exports.
