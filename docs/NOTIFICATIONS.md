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
permission and OS notification settings. Only the existing five-second
authenticated notification poll renews a page's 90-second presence lease;
ordinary state requests and operation results do not. Minimized/hidden
pages keep polling as their browser permits. On `pagehide` the panel stops
polling, invalidates queued responses and sends the retiring ID and current
CSRF token to its service worker in memory. The worker keeps the message
event alive while it posts to `/api/notifications/leave` with the same-origin
session cookie and the normal CSRF header. This lets the request run after
the page closes or navigates away. Chromium can abort a document-owned
beacon on navigation even after `sendBeacon` returns `true` (queued).
An uncontrolled page or one with an older worker starts a keepalive fetch
and can try a beacon if the fetch fails while the page still runs. Only the
leave endpoint also accepts a session-bound CSRF token in a JSON body,
with an explicit matching origin, for this fallback.
The journal atomically removes the lease and remembers the closed document
ID for 24 hours, so delayed polls cannot reactivate it within that period.
The document also records up to 16 explicitly retired IDs in tab-local
sessionStorage (no token or live ID). If a teardown request was lost, the
next authenticated poll after a reload/restoration atomically retires these
IDs with its new heartbeat, then clears the acknowledged IDs. A restored
page (including BFCache) or a new login uses a fresh lifetime ID. A hidden
visibility transition keeps the existing lease and polling behavior. If the
browser fails to deliver leave, crashes,
freezes or throttles/suspends a page beyond the lease interval, presence
can still be inaccurate; a missed leave expires after at most the remaining
lease interval, and a frozen open page may receive system push instead of
a toast. Actual Windows/Edge closure and minimized behavior require
workstation testing. A push already in transit can
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
The click carries the exact notification ID in a local URL fragment. The
first authenticated notification poll marks only that journal entry read
and returns the updated unread count, even after a cold launch or password
login. Opening a journal toast uses the same exact-event authenticated
poll acknowledgement and does not mark unrelated entries read. Clicking
a stale, deleted entry does not recreate it. The fragment
is removed after successful acknowledgement; no GET request changes state.
Native Windows/Edge delivery and installability must be checked on the
actual workstation; Chromium CI uses an isolated browser and does not
send real push messages to an external provider.

### Edge registration troubleshooting (optional)

If HTTPS is trusted, permission is granted, the service worker is active
and the VAPID API works, but Edge reports `AbortError: Registration failed
- push service error` during subscription, Microsoft documents the
[ForceBuiltInPushMessagingClient](https://learn.microsoft.com/en-us/deployedge/microsoft-edge-policies/forcebuiltinpushmessagingclient)
policy. It switches Edge to its built-in WNS client instead of the OS
client. This is a **browser-wide** policy (not per profile or per PWA).
Its name is fixed by Microsoft; there is no DevStack-specific equivalent.
On a private, unmanaged Windows PC only, if explicitly desired, use an
administrator PowerShell:

    reg add "HKLM\SOFTWARE\Policies\Microsoft\Edge" /v ForceBuiltInPushMessagingClient /t REG_DWORD /d 1 /f

Record any pre-existing value before changing it. Restart Edge and check
`edge://policy` for the value true, then enable Web Push again. The
workstation test confirmed this resolves the registration error on ASUS.
If the value did not previously exist, remove it to undo the change:

    reg delete "HKLM\SOFTWARE\Policies\Microsoft\Edge" /v ForceBuiltInPushMessagingClient /f

Restart Edge again after restoring/removing it. DevStack never installs or
modifies this global policy automatically.

## Optional local Windows transport

The experimental local transport uses the same notification journal and
delivery claim. Explicit opt-in installs the short-lived GUI launcher and
WinRT script in C:\DevStack\LocalNotifications; it publishes the root-owned
transport marker only after the corresponding Ubuntu deployment succeeds.
No new resident process or external push service is required. WNS remains
configured but is not used while the local selector is enabled. See
[LOCAL-WINDOWS-NOTIFICATIONS.md](LOCAL-WINDOWS-NOTIFICATIONS.md) for the
integrated update, Windows ACL, first-toast HRESULT handling, migration,
verification, and rollback. The normal updater without the opt-in flag
does not install or enable the Windows component.

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
