# Local test email

DevStack can use the official Mailpit SMTP capture service without an
external account, Docker container or real outbound mail server.
The native **E-mail** tab accesses the local mailbox through a small fixed
PHP API adapter, using the panel's existing session and CSRF checks.
Only mail explicitly sent to this local SMTP listener is captured. Do not
treat this as a network firewall for arbitrary application code.

## Install once

Review the installer from the current checkout, then in Ubuntu:

```bash
cd ~/projects/devstack-source
sudo bash scripts/install-mailpit.sh
systemctl is-active devstack-mailpit.service
```

Refresh the panel and open **E-mail**. The service starts automatically with
Ubuntu; it does not start a stopped WSL distro. The installation downloads
the official, SHA-256-pinned Mailpit v1.34.0 archive (amd64 or arm64), installs
the executable under /opt/devstack-mailpit and uses a dedicated systemd
service with an unprivileged dynamic user. It never modifies existing
projects or replaces an existing Mailpit installation. Port conflicts
are rejected before installation.

| Setting | Value |
| --- | --- |
| SMTP | 127.0.0.1:1025 |
| SMTP TLS/auth | None (local development only) |
| Native inbox | https://devstack.test/#/mail |
| Full upstream inbox (optional) | http://127.0.0.1:8025/ |
| Retention | At most 500 messages, 10 MB per message |
| Storage | /var/lib/devstack-mailpit/mailpit.db (systemd-managed private state) |

Both listeners bind to IPv4 loopback. No upstream SMTP relay is configured.
Do not expose either port to a LAN or the internet. Direct access to the
upstream UI is passwordless and independent of DevStack authentication;
use it only on the trusted local workstation. The native inbox requires
DevStack's ordinary access policy, including CSRF for deletion. Opening a
message marks it as read according to Mailpit's API. The native HTML preview
uses a dedicated sandboxed iframe with an opaque origin and a separate
Content Security Policy. It preserves sender CSS (including inline styles
and media queries), document colors, legacy table attributes and passive
HTML independently of the dashboard theme. Supported bounded raster CID
and data images are embedded without network requests. Remote HTTPS images
are blocked by default; the per-message **Wczytaj obrazy zewnętrzne** action
allows browser image requests and warns that they may contact sender servers
or tracking pixels. External stylesheets and fonts, scripts, forms, active
links, nested frames, SVG markup and refresh/base tags are not enabled.
Use the current-message Mailpit shortcut for active links or unsupported
email features. Browser preview is not an Outlook/Gmail compatibility test.
The preview requires PHP 8.5 XML/DOM (installed by fresh setup; existing
installations lacking it must install php8.5-xml before updating).
Attachment downloads are forced to be files rather than active documents.
The native JSON preview is limited to 4 MiB; larger messages can be inspected
in the full upstream inbox. Search, list and delete are limited to reviewed
Mailpit endpoints; selected-message deletion (up to 40 IDs) and clearing the mailbox require explicit
confirmation. Clearing always includes every project and messages outside the
current search. No arbitrary upstream path, send or relay endpoint is proxied
by DevStack.

## New projects

Enable **Lokalna poczta testowa** in the new-project form after installing
Mailpit. The choice is off by default, must be explicitly selected and is
saved with the project metadata. If enabled:

- The project's own PHP-FPM pool uses Mailpit's sendmail transport for
  PHP mail(), without changing global PHP configuration.
- Laravel additionally receives SMTP values in its private .env during
  normal project creation. Its sender defaults to hello@<project-domain>.
  There are no extra migrations or dependencies, and the original
  framework installer remains responsible for the application scaffold.
- The service must be active when the project is created. Stopping it
  later makes that local delivery unavailable; it does not fall back to
  an external server through the configured transport.
- A custom transport or later manual changes to app configuration can
  still send elsewhere. The checkbox is not a guarantee for arbitrary code.

## Existing projects

The installer and DevStack updater deliberately do not alter existing
projects or their application secrets. For an existing Laravel project,
edit the relevant local .env entries (back it up privately first), replacing
the example domain:

```dotenv
MAIL_MAILER=smtp
MAIL_SCHEME=null
MAIL_URL=null
MAIL_HOST=127.0.0.1
MAIL_PORT=1025
MAIL_USERNAME=null
MAIL_PASSWORD=null
MAIL_ENCRYPTION=null
MAIL_FROM_ADDRESS="hello@twoj-projekt.test"
```

Run php artisan config:clear from that project if configuration has been
cached, then exercise its normal mail flow. This manual setup does not change
the DevStack metadata flag. Do not copy local-only values to production.
For a generic existing project use SMTP explicitly; do not change the
global sendmail path.

## Maintenance

```bash
systemctl status devstack-mailpit.service
sudo systemctl stop devstack-mailpit.service
sudo systemctl start devstack-mailpit.service
```

The four core services and Windows widgets are unchanged. The optional service
is not restarted or upgraded by the ordinary DevStack updater. Stopping and
starting it retains captured messages. Review messages before deleting them:
they may contain sensitive development data or reset links. Keep mailbox
files private; they are not included in a project database backup.

## Acceptance

The GitHub smoke workflow installs the pinned release and checks loopback
listeners, SMTP capture, PHP mail(), PHP API list/search/message/attachment,
single and selected-message deletion, confirmed mailbox clearing and retention
across service restart. The browser workflow installs the real stack and verifies
the native inbox, isolated styled HTML, CID images, explicit remote-image opt-in, blocked active content, downloads, pagination, polling and compact layouts
in both themes. See [browser tests](BROWSER-TESTS.md). A representative
Laravel installer smoke enables local mail and sends through Laravel's
configured mailer. The ordinary regression tests check the service opt-in,
fixed upstream paths and the bounded HTML5 preview renderer. Local browser/WSL acceptance
is still necessary after deploying.
