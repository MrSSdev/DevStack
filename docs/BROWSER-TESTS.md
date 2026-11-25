# Browser acceptance

The **DevStack browser and real mailbox** workflow installs the production
build and actual DevStack services on an isolated Ubuntu runner. Chromium
uses the fixture CA as a trusted certificate; HTTPS errors are not ignored.
Mail is sent through real SMTP and read through the authenticated PHP API.
The test does not mock requests or privileged operations.

Coverage includes all primary screens and settings categories in both themes
at 640, 853, 1024, 1280 and 1920 CSS pixels; persisted themes; keyboard drawer
navigation; project creation; installer links; widget persistence; confirmed
Redis stop/start; HTML/plain/source views; exact attachment downloads;
search, pagination, deletion, polling and recovery from a stopped Mailpit;
long project names; project search, detected Composer versions, home link and shared favicon; unified Nginx editor with both stored rule types, safe validation and reload; authentication, session rotation,
CSRF, origin validation, input rejection and login rate limiting.

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
