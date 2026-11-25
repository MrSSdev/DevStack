# First installation: Windows 11 and Ubuntu WSL2

This guide is for a **new DevStack installation**. Existing installations use [UPGRADING.md](UPGRADING.md), not the bootstrap or `install.sh`. Keep backups separate from the source checkout. All sample account names and paths below are configuration examples.

## 1. Prepare Windows and Ubuntu

In **Windows PowerShell as Administrator**:

```powershell
wsl --install -d Ubuntu-26.04
```

Restart Windows if requested. Open Ubuntu and create an ordinary developer account with a password and sudo access. The supplied widget Start action currently uses Linux user `ubuntu`; use that account if you intend to install the prebuilt widgets, or review and rebuild their configuration for a different account.

In **Ubuntu**, check PID 1:

```bash
ps -p 1 -o comm=
```

It must be `systemd` for DevStack's service units and widget integrations. Do not continue service setup until WSL/systemd is configured correctly. Use Linux `~/projects` for projects, not `/mnt/c`.

## 2. Install DevStack in Ubuntu

Run as the ordinary Ubuntu account, **not a root shell**:

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

The bootstrap installs Ubuntu build prerequisites, validates Node.js, builds the frontend without sudo, checks the environment and tests, and invokes the first-install script with sudo. It refuses an already installed DevStack. Node.js is needed for builds, not as a running server. An unsupported Node.js version causes a stop rather than an unreviewed third-party runtime installation.

Review the authentication prompt. Password mode is recommended; passwordless local mode deliberately gives any process able to reach the dashboard extensive administration access. See [SECURITY.md](../SECURITY.md).

The installer configures the local Nginx/PHP-FPM stack, MariaDB, Redis, mkcert and the DevStack helper. Check:

```bash
systemctl is-active nginx php8.5-fpm mariadb redis-server devstackd
sudo nginx -t
sudo php-fpm8.5 -t
curl -ksS --noproxy '*' --resolve devstack.test:443:127.0.0.1 https://devstack.test/api/session
```

Here `curl -k` is a diagnostic before trust configuration, not proof that TLS trust is correct. Normal Windows browsing must validate the certificate.

## 3. Authorize local CA trust and Windows hosts integration

Use **Windows PowerShell as Administrator under the Windows account that owns Ubuntu**:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
$setup = '\\wsl.localhost\Ubuntu-26.04\home\<ubuntu-user>\projects\devstack-source\windows\setup-windows.ps1'
Get-Content -LiteralPath $setup
& $setup
```

Replace `<ubuntu-user>`. Review the displayed public CA fingerprint before typing the required confirmation. The CA private key stays in Ubuntu. This CA is separate from the Windows widget code-signing certificate. See [WINDOWS-CA.md](WINDOWS-CA.md).

The installer copies synchronization scripts to ACL-protected `C:\ProgramData\DevStack` and registers **DevStack Hosts Event Sync**. The task uses an elevated, noninteractive S4U session, `-Once`, no logon trigger, and no resident Windows WMI listener. Only the managed block in Windows hosts is replaced; unrelated entries are preserved and the previous file is backed up.

```powershell
Get-ScheduledTask -TaskName 'DevStack Hosts Event Sync'
Get-ScheduledTaskInfo -TaskName 'DevStack Hosts Event Sync'
Get-Content "$env:WINDIR\System32\drivers\etc\hosts"
```

When idle, expect the task to be `Ready`. The managed block includes:

```text
# BEGIN DevStack managed domains
127.0.0.1 devstack.test
# END DevStack managed domains
```

The Linux helper requests the fixed task after project changes and at service startup. Windows validates the domain list and checks that Ubuntu is already running before any Linux read. Merely logging into Windows must not start Ubuntu. The task runs as the WSL-owning Windows account; a different account does not own the same distro registration.

## 4. Open the dashboard and optional widgets

With Ubuntu deliberately running, open **https://devstack.test**. Create a disposable project, verify its domain/HTTPS, and test guarded removal. Database deletion is a separate irreversible operation; source code is kept.

Opening the URL alone does not wake a stopped distribution. Start Ubuntu deliberately from a terminal or the Ubuntu widget. Closing the browser does not stop it.

For the two separate Windows cards, follow [DevStack Widgets](../windows/widgets/README.md). Installing the Windows MSIX, setting up optional WSL metrics, and setting up DevStack service metrics/control are separate operations. No Visual Studio installation is needed for a prebuilt CI package.

## 5. Existing workstation: install this integration without reinstalling

This section migrates an older Windows hosts listener. It does not reinstall DevStack or reauthorize an already trusted CA.

First update the installed Linux application using [UPGRADING.md](UPGRADING.md). Pulling source alone does not update `/opt/devstack` or the installed Windows task. With Ubuntu deliberately running, inspect the current one-shot script:

```powershell
$testScript = '\\wsl.localhost\Ubuntu-26.04\home\<ubuntu-user>\projects\devstack-source\windows\watch-hosts.ps1'
& "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe" `
    -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass `
    -File $testScript -Once
$LASTEXITCODE
```

Run this in an elevated PowerShell session after substituting the Linux user. A zero exit code can mean successful reconciliation or a safe skip when Ubuntu is stopped. Check actual hosts results rather than relying only on the exit code.

Then install only the Windows hosts task:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
$installer = '\\wsl.localhost\Ubuntu-26.04\home\<ubuntu-user>\projects\devstack-source\windows\install-hosts-sync.ps1'
Get-Content -LiteralPath $installer
& $installer
```

The script backs up the previous task definition under `C:\ProgramData\DevStack\task-before-on-demand.xml`, stops the older listener and registers the same task name without a logon trigger. It does not change CA trust. Test create/delete synchronization, no visible console, unrelated hosts preservation and stopped-WSL behavior. Restore an older task only deliberately after reviewing the backup; it may restore unwanted logon behavior.

## 6. Diagnostics and removal

The [troubleshooting guide](TROUBLESHOOTING.md) separates dashboard, hosts and widget issues. For a one-shot installed hosts check, use an elevated PowerShell child process:

```powershell
& "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe" `
    -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass `
    -File "$env:ProgramData\DevStack\watch-hosts.ps1" -Once
$LASTEXITCODE
```

To deliberately remove the automatic hosts task, without touching Ubuntu or certificates:

```powershell
Stop-ScheduledTask -TaskName 'DevStack Hosts Event Sync'
Unregister-ScheduledTask -TaskName 'DevStack Hosts Event Sync' -Confirm:$false
```

This leaves hosts entries and certificate trust in place; it does not uninstall DevStack. Removing a widget card is unrelated to removing this scheduled task. Do not delete project data, databases or CA material as a generic repair step.
