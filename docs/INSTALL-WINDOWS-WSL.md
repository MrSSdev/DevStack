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

The Windows installer copies the trusted synchronization scripts to an ACL-protected `C:\ProgramData\DevStack` folder and registers **DevStack Hosts Event Sync** as a **manual-trigger-only**, elevated Windows task. It runs the already-tested `watch-hosts.ps1 -Once` via hidden PowerShell; there is **no Windows logon trigger or resident WMI listener**. The installed Ubuntu daemon sends a fixed `schtasks.exe /Run` signal after project changes and at service startup. Windows checks that Ubuntu is already running before reading domains; it does not wake a stopped distro, accept shell arguments from projects, or change certificate trust.

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

Open `https://devstack.test` in the Windows browser. Creating or removing a project commits the root-owned `/var/lib/devstack/projects.json` state and triggers the existing privileged Windows hosts reconciliation using a **fixed task name**, with no arbitrary command or domain input in the trigger. The Windows script reads and validates the complete domain list on each run; its managed `hosts` block is backed up before changes. Fast consecutive requests queue rather than cancel an in-progress sync.

The same one-shot request also runs when the existing `devstackd` service starts inside Ubuntu, so missed changes can be reconciled without a Windows watcher. There is no extra Windows process at logon. The `-Once` command first checks `wsl --list --running --quiet` and exits without starting Ubuntu if the distribution is stopped. Creating/deleting projects needs no UAC prompt after the initial Windows task registration. Editing a project's domain/slug is not yet implemented.

**Existing workstations:** the installed Windows task does **not** change when you only pull source or update DevStack inside Ubuntu. An old B1 logon-triggered listener continues until you explicitly install the new task (section 5). Do not assume that GitHub source updates have changed your current Windows configuration.

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

## 5a. Existing workstation: safe on-demand migration checks

**Do not replace the currently installed scheduled task on the basis of CI alone.** With Ubuntu intentionally running and the source updated, first test only the one-shot script from a separate elevated Windows PowerShell console:

~~~powershell
$testScript = '\\wsl.localhost\Ubuntu-26.04\home\ubuntu\projects\devstack-source\windows\watch-hosts.ps1'
& "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $testScript -Once
$LASTEXITCODE
~~~

Expect exit code `0` and unchanged unrelated hosts entries. A running legacy B1 observer may perform the same idempotent sync; this is acceptable during this pre-deployment test. Next, install the new task from section 5 as Administrator. The installer backs up the previous task XML as `C:\ProgramData\DevStack\task-before-on-demand.xml`, stops the logon observer and registers the **same task name with no trigger**, using the previous hidden PowerShell launch style and adding `-Once`. It does not touch trusted CA settings.

After installation, expect `Get-ScheduledTask -TaskName 'DevStack Hosts Event Sync'` to be `Ready` when idle, with zero triggers. Test a disposable non-database project creation/deletion, matching Windows `hosts`, one-shot `LastTaskResult = 0`, no visible PowerShell window and no admin prompts. Close Ubuntu via `wsl --terminate Ubuntu-26.04`, verify it stays stopped and the task does not run by itself. A new login must not start a DevStack Windows PowerShell process; the task remains available for next use.

**Rollback:** the old XML task definition is saved in the protected directory and the legacy listener code remains in `watch-hosts.ps1`. Do not restore the old trigger automatically; only reinstall the saved task definition as Administrator if the new sync fails, then verify B1 behavior before returning to work.


## 6. Troubleshooting and removal

The current on-demand task should remain idle at Windows login and must not start Ubuntu. To diagnose:

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

The one-shot command requires an **elevated Windows PowerShell** session, and the exit code should be **0**. It checks whether Ubuntu is already running; if stopped, it skips without waking it. `-ExecutionPolicy Bypass` applies only to the child PowerShell process. Use for diagnostics; the normal project workflow triggers the protected task automatically.

To stop the automatic bridge without touching Ubuntu or the CA:

```powershell
Stop-ScheduledTask -TaskName 'DevStack Hosts Event Sync'
Unregister-ScheduledTask -TaskName 'DevStack Hosts Event Sync' -Confirm:$false
```

This intentionally does not remove the previously trusted local CA or unrelated entries from Windows hosts.

## Security and scope

- The Windows scheduled task has elevated file-writing access only because you configured it once as a Windows administrator. Its executable scripts are protected by an Administrators/SYSTEM ACL; this is an administrative trust boundary.
- The WSL daemon sends only a fixed, no-argument task trigger to Windows; the privileged Windows reader obtains validated domains directly. It never passes passwords, arbitrary shell text, state JSON or CA keys as task arguments.
- The Windows reader validates every `.test` domain. A malformed managed hosts block causes a safe failure rather than deleting unrelated lines. A backup is created before any change.
- Only the Windows account owning the WSL distro should install and run this task. Do not expose DevStack or its socket/ports on LAN or the Internet.
- The bridge does **not** grant trust to new certificate authorities. Replacing the local CA is an explicit security operation requiring new Windows consent.
