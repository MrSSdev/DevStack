# Notifications and installable PWA

## Install

Open the trusted HTTPS dashboard in Microsoft Edge, use the browser's
app installation action and pin DevStack to Start or the taskbar.
The manifest has a standalone display mode and self-hosted Blocks icons. Its `launch_handler.client_mode` is `navigate-existing` so supported browsers can reuse an existing DevStack window for a protocol launch. Existing installations must receive the updated manifest; the actual window reuse depends on the browser and is verified separately on Windows.
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
Service control records command acceptance separately from confirmed
completion or failure. The collector also detects independent service
failures and recovery. Instruction synchronization likewise records a separate
terminal event. Its acknowledgement and result share the same operation
receipt in history. The collector reads the bounded `instruction_results`
snapshot without opening project directories or running synchronization.
It deduplicates terminal receipts using the existing SQLite tombstones,
including after deletion or a history clear. A project result links to its
project, while a global operation produces one summary in the Tools view.
These terminal notices use the existing Projects category and the same in-app
and opted-in WinRT delivery rules as other project notices. Only the opaque
event ID, category and kind are passed to the Windows launcher.
Routine refresh, clipboard and form validation messages remain temporary.

### Adding an operation

Register its POST route once in `DevStackNotices::POST_OPERATIONS` in `backend/notifications.php`: category, safe success/error titles, destination and Windows eligibility. Raw request data is never journaled. Return the normal API response through the shared `$respond` boundary in `backend/public/index.php`; it assigns the journal ID automatically. In React, call `runOperation` from `frontend/src/lib/api.ts` with a loading title and optional UI result handler. It owns the toast, exact-ID acknowledgement and terminal presentation. An accepted installer job hands the original loading toast to the existing job observer; systemd acceptance remains informational until the collector imports its result.

Do not create a second journal event or pass a toast ID manually. Use `request` for reads and the explicit `POST_LOCAL` exceptions (authentication, credential reveal, notification maintenance). Installer rejection before an accepted response remains temporary because a failed job can already exist; its durable result comes from the job observer. Validation/authentication failures do not generate operation history. CI compares the actual POST handlers, server policy and frontend local exceptions, and verifies that every registered operation can create a journal event. The runtime rejects an unregistered POST and a raw journaled frontend request. Local UI messages deliberately use the transient toast helpers.

The private SQLite journal is under /var/lib/devstack-web. The timer runs
a fixed, unprivileged collector in Ubuntu only while the distro is running.
It reads the existing fixed Mailpit API and a read-only helper snapshot.
It starts with a mailbox baseline and does not replay old mail on install.
A poll scans up to 200 newest messages; an unusually large burst between
polls may be summarized/incompletely represented. No Windows polling
agent or privileged outbound network process is installed.

## Local Windows notifications (WinRT)

DevStack uses local Windows notifications as the intended system delivery
path. The project does not rely on Web Push, WNS, browser subscriptions,
Internet connectivity, an external notification service, or a browser
running in the background. The local history and panel toasts work even
without the optional Windows component.

Enable the local transport once from the reviewed, installed checkout,
in the ordinary Ubuntu developer session on Windows:

    cd ~/projects/devstack-source
    bash scripts/update.sh --windows-local-notifications

Subsequent updates use the ordinary `bash scripts/update.sh` command and
preserve existing opt-in. An installation without that opt-in is not
automatically enabled. See [local Windows notifications](LOCAL-WINDOWS-NOTIFICATIONS.md)
for installation, ACLs, verification, PWA protocol registration and recovery.

The existing Ubuntu collector invokes a short-lived, developer-identity
GUI launcher in `C:\DevStack\LocalNotifications`. The launcher calls WinRT
and exits. No additional resident Windows agent, scheduled task or automatic
WSL startup is involved. Windows controls whether a banner is displayed.
Opening a new system notification prefers a registered DevStack PWA through
`web+devstack`; without a suitable registration, the same local route and
event ID open over HTTPS in the default browser.

### Delivery and presence

- Open panel, including a normally polling minimized PWA: one in-app toast
  across active clients, no system banner. The taskbar badge reflects unread
  events where the installed PWA supports it.
- All panel windows closed: eligible new events receive local WinRT delivery.
- Disabled system-delivery category: no system banner; the journal and
  in-app toasts remain available, including on reopening the panel.
- Ubuntu stopped: no collection and no automatic startup.

The category switches in Settings → Notifications control system delivery
for new mail, projects/configuration, installers and services. The panel
reports whether the protected local opt-in marker is configured. This
status does not guarantee that a banner is displayed. Windows notification
settings remain authoritative. History and panel toasts do not depend on
the system-delivery categories or opt-in.

Only the existing five-second authenticated notification poll renews a
page's 90-second presence lease; ordinary state requests and operation
results do not. Hidden/minimized pages keep polling as their browser permits.
On `pagehide` the panel stops polling, invalidates queued responses and sends
the retiring ID and current CSRF token to its service worker in memory.
The worker keeps the message event alive while it posts to
`/api/notifications/leave` with the same-origin session cookie and normal CSRF
header. Chromium can abort a document-owned beacon even after it reports
successful queuing. An uncontrolled page uses a keepalive fetch, with a
beacon fallback if the fetch fails while the page still runs. Only this leave
endpoint also accepts a session-bound CSRF token in a JSON body with an
explicit matching origin.

The journal atomically removes the lease and remembers the closed document ID
for 24 hours, preventing delayed polls from reactivating it. The document
records up to 16 explicitly retired IDs in tab-local sessionStorage (no token
or live ID); the next authenticated poll retires them atomically with its new
heartbeat. Restored pages, including BFCache, and new logins receive new
lifetime IDs. A missed leave expires after at most the remaining lease
interval. A frozen or heavily throttled document may lose its lease and
receive a system notification while still open. Delivery already claimed
before a new client opens may race with that client. Single-event claims and
stable identifiers reduce duplicates. Unknown delivery failures are not
automatically retried; the event remains in history. This is not a guaranteed
alerting service.

The normal collector interval is eight seconds after completion (plus timer
accuracy and processing time). Automatic mail events can be delivered in the
cycle that discovers them, when no active lease exists and the category is
enabled. Other events retain their grace period. The original opt-in
timestamp excludes the older backlog and survives ordinary updates. Only a
validated event ID, category and severity cross the launcher boundary.
The collector logs category, journal age, send duration and delivery outcome,
not mail content or secrets. WinRT acceptance does not prove that Windows
displayed a banner.

### Reading notifications

The system notification click carries the exact journal ID to the local
route. The first authenticated poll marks only that entry read, even after
a cold launch and password login. Opening a journal toast follows the same
path. Closing or expiring a terminal toast in a visible document also reads
only its own entry; hidden-document expiry and loading toasts do not mark
entries read. Deleted entries are not recreated. History remains independent
of the mailbox and project files.

An accepted service command is an information notice, not a verified
restart. The collector imports a separate result from the actual systemd
execution and expected service state for commands initiated through the
DevStack panel; see the local Windows guide. Widget controls keep their own
existing inline feedback.

### Retired browser push

Local WinRT is the only system delivery transport. Without a valid protected
opt-in marker, events remain in history and in-app toasts; there is no browser
or network fallback. Old subscription creation endpoints return 410.
The collector removes the obsolete subscription metadata without clearing
history, categories or active client leases. A browser profile visiting the
updated panel attempts to unsubscribe its old DevStack registration on a
best-effort basis. Previously displayed notifications can still open their
local history route. No new browser push event handler is registered.

The update removes the old deployed Web Push helper code. Old VAPID files
remain private until separately reviewed; no global Edge policy or distro
package is silently changed. An earlier troubleshooting test used
ForceBuiltInPushMessagingClient. It is not a DevStack requirement.
See [local Windows notifications](LOCAL-WINDOWS-NOTIFICATIONS.md) for the
optional legacy-state audit.

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

Do not copy the private journal or any legacy subscription endpoints and
VAPID private keys to a public repository. The retired subscription is removed from the live journal; private keys
and historical backups must remain private. The update does not change
Mailpit or application projects.
