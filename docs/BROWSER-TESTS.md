# Browser acceptance

The **DevStack browser and real mailbox** workflow installs the production
build and actual DevStack services on an isolated Ubuntu runner. Chromium
uses the fixture CA as a trusted certificate; HTTPS errors are not ignored.
Mail is sent through real SMTP and read through the authenticated PHP API.
The main acceptance scenarios do not mock requests or privileged operations. A separate toast lifecycle scenario uses a synthetic engine_job in the real GET /api/state response solely to exercise presentation and acknowledgement without installing a new framework; all authentication and writes remain real. The browser context guards external HTTP(S) requests from the start. Only the two controlled image fixtures are fulfilled after explicit image opt-in; all other requests reaching the interception boundary fail the test. Chromium reports some CSS URL attempts as request events followed by a CSP failure before reaching the network. The test accepts only that explicit CSP rejection, never a generic failure or DNS error. Both HTML and CSS images must load after opt-in, and switching back must stop new external requests.

Coverage includes all primary screens and settings categories in both themes
at 640, 853, 1024, 1280 and 1920 CSS pixels; persisted themes; keyboard drawer
navigation; project creation; installer links; widget persistence; confirmed
Redis stop/start; isolated HTML with sender CSS, table layouts, CID images and explicit HTTPS image opt-in, HTML/plain/source views, and exact attachment downloads;
search, pagination, deletion, polling and recovery from a stopped Mailpit;
long project names; project search, detected Composer versions, home link and shared favicon; shared fixed Base UI toasts, multiple notices, non-dismissible installer progress across polling phases and a six-second success timeout starting only after completion, persistent installer failure details, navigation, acknowledgement across reloads, retirement of older installer results, accessible dismissal without hover (including keyboard activation), and notices inside native service dialogs; unified Nginx editor with both stored rule types, safe validation and reload; authentication, session rotation,
CSRF, origin validation, input rejection and login rate limiting.

The dedicated PWA/history scenario exercises the real manifest, service worker, empty cache, live SMTP notification, cross-tab deduplication, read/delete/clear and persistence without deleting mail. All scenarios allow the real network-only service worker. Playwright 1.60's built-in service_workers='block' init script reads navigator.serviceWorker in sandboxed frames and throws a SecurityError in the deliberately opaque mail preview. The mail scenario explicitly verifies the top-level worker is active and the cache is empty before exercising the unchanged sandbox, CSP, external HTTP guard and image opt-in assertions. The worker handles only same-origin root navigations, not API calls or image requests; no browser runtime errors are filtered or ignored. PHP tests cover presence and push claims with a fake subscription; the suite does not contact real push providers or certify Windows notification delivery.

Screenshots, traces and the screenshot manifest are uploaded for one day.
Only generated fixture data is used. These tests do not certify Windows/WSL
integration or acceptance on a physical workstation.

To reproduce, use a disposable VM with DevStack and Mailpit installed, the
official Laravel Installer available to its developer account, devstack.test
resolving to loopback, and the local CA trusted by the OS and Chromium. Install
Playwright 1.60.0 and Chromium in a Python virtual environment, then run:

```bash
NODE_EXTRA_CA_CERTS='/path/to/public/rootCA.pem' \
  DEVSTACK_E2E_ISOLATED=1 DEVSTACK_E2E_PASSWORD='<fixture panel password>' \
  python tests/browser-smoke.py
```

This deliberately clears the entire Mailpit inbox, creates a project and
database, changes theme/widget settings, stops/starts Redis and exercises the
shared login limiter. Never run it against a personal or shared installation.

The installed-stack workflow also reproduces a legacy root-owned, mode-0755
`/var/lib/devstack-web` before applying the real privileged updater. It checks
that directory ownership and permissions are repaired, the sandboxed collector
completes successfully, the timer is active, and a previously stored SQLite
preference survives the update. The real SMTP/browser scenarios run afterward.
