# Notifications and installable PWA

## Install

Open the trusted HTTPS dashboard in Microsoft Edge, use the browser's
app installation action and pin DevStack to Start or the taskbar.
The manifest has a standalone display mode and self-hosted Blocks icons.
The app uses the normal session boundary and the existing theme.
Its service worker is network-only: no offline cache of authenticated
HTML, API responses, mail, credentials or project data. A failed root
navigation shows a static unavailable page; it does not start WSL.

## One event history

The bell shows the latest 200 events (up to 30 days). Each entry can be
read or deleted individually; **Wyczyść wszystko** removes all entries.
Deletion affects the journal, not source mail, project data or in-flight
operations. A separate source cursor prevents deleted mail/job records
from returning on refresh. Deleted job IDs retain a bounded-lifetime tombstone (90 days, or while the latest job remains current) so an older snapshot cannot resurrect a cleared result. Mail notification bursts can be grouped.

Authenticated operation results are recorded at the API boundary. Running
installer states and the final result use one stable job record; the
existing non-dismissible loading toast and terminal timing are preserved.
Service control is recorded as an accepted command, not a verified restart.
The collector separately detects transitions to failed and recovery.
Routine refresh, clipboard and form validation messages remain temporary.

The private SQLite journal is under /var/lib/devstack-web. The timer runs
a fixed, unprivileged collector in Ubuntu only while the distro is running.
It reads the existing fixed Mailpit API and a read-only helper snapshot.
It starts with a mailbox baseline and does not replay old mail on install.
A poll scans up to 200 newest messages; an unusually large burst between
polls may be summarized/incompletely represented. No Windows polling
agent or privileged outbound network process is installed.

## Optional Web Push

Install the distro package on Ubuntu 26.04 if needed:

    sudo apt install python3-pywebpush

In Settings → Notifications, enable Web Push and grant browser permission.
One browser profile is subscribed per workstation. Enabling a new profile
replaces the previous server-side subscription. Disable it in settings to
remove that subscription; password logout also removes it. The default
is disabled. The four important categories can be switched independently.
The private VAPID key persists with the journal. No message subjects,
addresses, URLs, project names, passwords or command output are sent in
the encrypted push payload. Only event ID, category and severity are sent.
The approved provider endpoints are restricted and redirects/proxies are
disabled. No third-party notification service is configured by DevStack.

The browser and its push infrastructure require Internet connectivity,
permission and OS notification settings. While a page is open it renews
a lease; a normal pagehide releases it. Minimized/hidden pages also renew.
If a browser crashes, freezes or suspends a tab, detection can be delayed
up to the lease interval (180 seconds). A push already in transit can
race with a newly opened page. Edge requires visible push messages; an
already sent message cannot be silently suppressed with perfect reliability.
Short TTL (30 seconds), per-event claims and stable notification tags limit
duplicates. Unknown delivery failures are not retried automatically;
the event remains in history. Do not treat this as a guaranteed alerting
service. Push is not sent for history-only old events when enabled.
Automatic mail events are eligible for Web Push in the collector cycle
that discovers them, as long as no client lease is active and the mail
category is enabled. They no longer wait for an additional timer cycle.
Other events retain their existing grace period. The normal collector
interval remains eight seconds after completion (plus timer accuracy and
processing time); the open panel polls every five seconds. Delivery by
the browser provider is asynchronous and outside DevStack's control.
The collector logs category, journal age, send duration and numeric HTTP
status (or unavailable) without message content, endpoint or key material.
HTTP acceptance does not prove Windows displayed a banner.
A click opens DevStack or the specific mail message if still available.
Native Windows/Edge delivery and installability must be checked on the
actual workstation; Chromium CI uses an isolated browser and does not
send real push messages to an external provider.

## Maintenance

The standard installer/updater deploys the timer and PHP SQLite/mbstring
are prerequisites. The timer may be checked with:

    systemctl status devstack-notifications.timer
    journalctl -u devstack-notifications.service --no-pager -n 30

The installer and updater start the actual collector once before reporting
success. A one-shot service normally returns to `inactive` with
`Result=success` and `ExecMainStatus=0`. If an older installation reports
SQLite "unable to open database file", inspect the private state directory:

    sudo namei -l /var/lib/devstack-web/notifications.sqlite3

The updater repairs the fixed directory to `devstackweb:devstackweb`, mode
`0700`. It does not recursively change ownership or remove the journal,
sessions, or VAPID key. A symlink or non-directory at that location is
rejected. Do not run the collector manually as root.

Do not copy the private journal, subscription endpoints or VAPID private
key to a public repository. The update preserves this data and does not
change Mailpit, application projects or Windows widgets.
