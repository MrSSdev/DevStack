# Experimental local Windows notifications

The Windows 11 test branch delivers system notifications through Windows
PowerShell 5.1 and WinRT. Ubuntu's existing collector claims an event in
SQLite and asks the existing DevStack helper to invoke PowerShell once.
Windows displays the toast and the process exits. There is no Windows
timer, listener, Python installation, or PowerShell module.

Windows receives the generic category and severity plus the journal ID.
The toast link uses the fixed DevStack HTTPS origin and the existing
authenticated notification-click flow. Windows opens the default HTTPS
browser. If that profile needs a login, the existing click flow preserves
the journal ID until the first authenticated poll.

The local transport is off by default. While enabled, it takes the place
of Web Push delivery and shares the same atomic claim, category preferences,
and suppression while a panel lease is active. The Web Push subscription
stays stored for use after switching back. Events already in history, or
whose last update predates activation, are skipped. Delivery failure keeps
the event in history. A PowerShell success status means the Windows API
accepted the toast. The popup itself depends on Windows notification
settings and the interactive session. No Windows component starts WSL.

## ASUS test

Use an isolated DevStack installation and test mailbox. In an already running
Ubuntu distribution, check out the test branch in the existing canonical
source checkout, build the frontend and apply the staged update.

    cd ~/projects/devstack-source
    bash scripts/build-frontend.sh
    sudo bash scripts/apply-update.sh

In a PowerShell window under the signed-in Windows user, run the script
from the test checkout. The registration creates the DevStack app identity
under the current user's registry hive. Try the demo while disconnected
from the Internet. Its link points to the test inbox.

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows-local-notification.ps1 -Mode Register
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows-local-notification.ps1 -Mode Demo

A demo succeeds when the **DevStack** toast appears and its link opens the
test inbox. Keep Windows signed in for the remaining steps.

In Ubuntu, opt in as root and inspect the existing collector's log.

    sudo install -m 0644 -o root -g root /dev/null /etc/devstack/windows-local-notifications
    sudo journalctl -u devstack-notifications.service -f

1. With the panel open, send a new SMTP fixture to local Mailpit. Expect
   a panel toast while the tab polls.
2. Close the last DevStack window. Keep Windows disconnected from Internet
   while the local HTTPS origin and Mailpit remain reachable. Send a second
   fixture and measure from SMTP acceptance to the system toast. Check for
   one toast, then click it after the PowerShell process has exited.
3. Check that the second fixture opens and its own journal entry changes to
   read. Keep an unrelated unread fixture to confirm its status is preserved.
4. Open two DevStack tabs and close one of them. Send another fixture and
   check that the remaining tab still handles it. Repeat with the remaining
   tab minimized.
5. Compare the collector log with the visible toast. An
   outcome=failed entry calls for running Demo in the same Windows user
   session and checking WSL interoperability from the active distribution.
   If Demo works and the integrated attempt fails, record whether WSL was
   started interactively and which account has the local app identity.

To revert the transport, remove the Ubuntu opt-in marker. This restores the
standard Web Push path for later events. To remove the experimental Windows
identity, also run the script in Unregister mode under the same user.

    sudo rm -- /etc/devstack/windows-local-notifications
