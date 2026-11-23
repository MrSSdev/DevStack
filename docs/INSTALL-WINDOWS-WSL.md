# DevStack: clean Windows 11 + Ubuntu 26.04 WSL2 installation

This walkthrough reconstructs a **fresh DevStack workstation** using public repository scripts. Keep the existing Linux projects and backups separate. **Do not rerun the first-install script on an existing machine.**

## 1. Windows: initialize WSL2 once

Open **Windows PowerShell as Administrator**:

```powershell
wsl --install -d Ubuntu-26.04
```

Restart Windows if requested. Open Ubuntu, create your ordinary Ubuntu developer user and password, then enter the Ubuntu shell. Enable systemd if necessary (WSL Ubuntu 26.04 normally includes it); confirm `ps -p 1 -o comm=` displays `systemd`.

## 2. Ubuntu: fetch and install DevStack

In **Ubuntu WSL2**, not Windows PowerShell:

```bash
sudo apt update
sudo apt install -y git ca-certificates
mkdir -p ~/projects
git clone https://github.com/MrSSdev/DevStack.git ~/projects/devstack-source
cd ~/projects/devstack-source
less scripts/bootstrap-ubuntu.sh
less scripts/install.sh
bash scripts/bootstrap-ubuntu.sh
```

The bootstrap installs official Ubuntu build prerequisites, checks the installed Node.js version, builds the frontend as your normal user, runs preflight and tests, then invokes the first-install-only service setup with sudo. During that interactive setup, choose password authentication (recommended) or explicitly opt into passwordless local mode.

If the Ubuntu package's Node.js version is too old for Vite 8, the bootstrap **stops**, rather than running an unreviewed third-party installation script. Install a supported maintained Node.js version, then rerun the bootstrap before the actual installer has started.

Verify Ubuntu services:

```bash
systemctl is-active nginx php8.5-fpm mariadb redis-server devstackd
sudo nginx -t
sudo php-fpm8.5 -t
curl -ksS --noproxy '*' --resolve devstack.test:443:127.0.0.1 https://devstack.test/api/session
```

## 3. Windows: once-only CA authorization and hosts integration

Back in **Windows PowerShell as Administrator** (run as the Windows user owning the WSL distro):

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
$setup = '\\wsl.localhost\Ubuntu-26.04\home\ubuntu\projects\devstack-source\windows\setup-windows.ps1'
Get-Content -LiteralPath $setup
& $setup
```

Change `ubuntu` in the UNC path if the actual Ubuntu user is different. Review the public CA fingerprint shown by the interactive script before typing `YES`. The local CA's private key stays inside Ubuntu.

The Windows installer copies only the trusted sync scripts to an ACL-protected `C:\ProgramData\DevStack` folder, then registers the **DevStack Hosts Event Sync** task to start elevated at Windows user logon. **Only a Windows PowerShell WMI listener starts at logon; it does not start Ubuntu.** The listener observes external `wsl.exe` process starts, checks `wsl --list --running --quiet` and attaches Linux `inotify` only to an already-running `Ubuntu-26.04`. It does not elevate arbitrary WSL commands, execute shell text from web requests, modify Windows trust, or periodically poll WSL.

Confirm:

```powershell
Get-ScheduledTask -TaskName 'DevStack Hosts Event Sync'
Get-ScheduledTaskInfo -TaskName 'DevStack Hosts Event Sync'
Get-Content "$env:WINDIR\System32\drivers\etc\hosts"
```

The hosts file will contain:

```text
# BEGIN DevStack managed domains
127.0.0.1 devstack.test
# END DevStack managed domains
```

## 4. Normal development

Open `https://devstack.test` in the Windows browser. Creating a project through the dashboard updates the root-owned `/var/lib/devstack/projects.json` atomically. A long-lived, read-only WSL `inotify` watcher emits a constant `SYNC` marker; the Windows scheduled task retrieves the validated domain list and updates **only its own marked block** in `hosts` (with a local backup).

When the listener is connected to running Ubuntu, no further manual PowerShell commands or CA prompts are needed after new projects are created. Stale managed entries are removed when the trusted state no longer contains the domain. The panel supports guarded project deletion, including optional irreversible database removal confirmation; domain changes are not yet implemented. The bridge removes the deleted project's Windows hosts entry on the next `inotify` event.

**Resilience and WSL lifecycle (B1):** The Windows task starts a resident Windows PowerShell process at user logon (small but nonzero Windows RAM cost). It only reads WSL running-state and subscribes to `Win32_ProcessStartTrace`; it **never starts a stopped Ubuntu at login**. If Ubuntu is already running, it attaches and performs a full sync. After the user terminates Ubuntu, the Linux watcher exits and Windows **waits for a new external `wsl.exe` start event** instead of retrying `wsl -d` every 15 seconds. There is a brief check/attach race inherent in the public WSL CLI; validate manual shutdown and startup on your own workstation. WMI detects *process creation*, not guaranteed distro startup; VS Code and other launchers must be tested on the target PC. If the Windows account logs out, the task is stopped and starts again at next login.

## 5. Existing workstation: install this integration without reinstalling

Since the CA is already trusted, avoid running the one-time CA wizard again.

In **Ubuntu**:

```bash
cd ~/projects/devstack-source
git pull --ff-only
python3 -m unittest discover -s tests -v
sudo install -m 0644 -o root -g root scripts/watch-domains.py /opt/devstack/scripts/watch-domains.py
```

In **Windows PowerShell as Administrator**:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
$installer = '\\wsl.localhost\Ubuntu-26.04\home\ubuntu\projects\devstack-source\windows\install-hosts-sync.ps1'
Get-Content -LiteralPath $installer
& $installer
```

No Nginx, PHP, database or WSL restart is required.

## 5a. Existing workstation: safe B1 pre-deployment test

**Do not replace the currently installed scheduled task merely because the new source script passed CI.** First stop that task, then test the new watcher from a separate, elevated Windows PowerShell session with Ubuntu already running.

After `git pull --ff-only` in Ubuntu, check that the task reports `Ready`. In a separate **elevated Windows PowerShell** window launch the source script explicitly:

~~~powershell
$testScript = '\\wsl.localhost\Ubuntu-26.04\home\ubuntu\projects\devstack-source\windows\watch-hosts.ps1'
& "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File $testScript
~~~

Expected initial output when Ubuntu is already running: `DevStack hosts: connected to already-running Ubuntu-26.04.` Keep this window open while running WSL stop/restart tests in a **different** Windows PowerShell window. If the test fails, press Ctrl+C to stop the manual listener; the installed scheduled task and `C:\ProgramData\DevStack` remain unchanged.

**Only after this test succeeds**, run the `windows/install-hosts-sync.ps1` installer from section 5 in elevated PowerShell. It replaces the installed scripts and registers a logon-triggered **Windows-only WMI subscriber**. Run the resulting shutdown/startup and project-hosts tests before treating B1 as complete.


## 6. Troubleshooting and removal

The B1 task should not start Ubuntu when the Windows user logs in. To diagnose:

```powershell
Get-ScheduledTask -TaskName 'DevStack Hosts Event Sync'
Get-ScheduledTaskInfo -TaskName 'DevStack Hosts Event Sync'

# Run the installed watcher once in a NEW PowerShell process.
# Avoids PSSecurityException if direct .ps1 invocation is disabled.
& "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe" `
    -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass `
    -File "$env:ProgramData\DevStack\watch-hosts.ps1" -Once
$LASTEXITCODE
```

The one-shot command requires an **elevated Windows PowerShell** session, and the exit code should be **0**. Using `-ExecutionPolicy Bypass` on the child `powershell.exe` process is temporary; it does **not** change the machine-wide execution policy. It performs exactly one synchronization without starting the long-running watcher. Use for diagnostics or manual reconciliation after changes while the scheduled observer is stopped.

To stop the automatic bridge without touching Ubuntu or the CA:

```powershell
Stop-ScheduledTask -TaskName 'DevStack Hosts Event Sync'
Unregister-ScheduledTask -TaskName 'DevStack Hosts Event Sync' -Confirm:$false
```

This intentionally does not remove the previously trusted local CA or unrelated entries from Windows hosts.

## Security and scope

- The Windows scheduled task has elevated file-writing access only because you configured it once as a Windows administrator. Its executable scripts are protected by an Administrators/SYSTEM ACL; this is an administrative trust boundary.
- The WSL watcher requires `root` to read the DevStack project state directory, but sends only fixed event markers, **never passwords, state JSON or CA keys**, through stdout.
- The Windows reader validates every `.test` domain. A malformed managed hosts block causes a safe failure rather than deleting unrelated lines. A backup is created before any change.
- Only the Windows account owning the WSL distro should install and run this task. Do not expose DevStack or its socket/ports on LAN or the Internet.
- The bridge does **not** grant trust to new certificate authorities. Replacing the local CA is an explicit security operation requiring new Windows consent.
