# Installation on Windows 11 and WSL2

For a new installation. To update an existing DevStack installation, use [UPGRADING.md](UPGRADING.md).

## 1. Prepare Ubuntu

Open Windows PowerShell as Administrator:

```powershell
wsl --install -d Ubuntu-26.04
```

Restart Windows if requested. Open Ubuntu and create an ordinary user with sudo access. Use the username `ubuntu` if you intend to use the supplied widget package.

In Ubuntu, check that systemd is running:

```bash
ps -p 1 -o comm=
```

Expected output: `systemd`. Keep projects under `~/projects` on the Linux filesystem.

## 2. Install DevStack

In Ubuntu, as your ordinary developer account:

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

The bootstrap prepares dependencies, builds the frontend, runs checks and installs the local services. The build requires Node.js 20.19+, 22.12+ or 24+ with npm; follow the version check. Choose password authentication for the dashboard.

The default full installation also installs and enables MariaDB and Redis for projects. For a new installation without either server, use `bash scripts/bootstrap-ubuntu.sh --core-only` instead. This leaves any pre-existing optional services unchanged; PHP database/cache extensions remain available for applications. Add or start those servers explicitly when a project needs them.

Check the required services:

```bash
for unit in nginx php8.5-fpm devstackd devstack-notifications.timer; do
    systemctl is-active "$unit"
done
sudo nginx -t
sudo php-fpm8.5 -t
```

MariaDB and Redis may be stopped or absent without making DevStack unhealthy. On a fresh core-only installation, the empty protected project registry is initialized for the widget bridge. Use the matching service-integration bundle (the v1 reader also supports explicitly missing units).

## 3. Windows integration

First record the intended account SID in ordinary Windows PowerShell using `[Security.Principal.WindowsIdentity]::GetCurrent().User.Value`. Then use elevated Windows PowerShell 5.1 under that same account (replace the SID placeholder below):

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
$setup = '\\wsl.localhost\Ubuntu-26.04\home\<ubuntu-user>\projects\devstack-source\windows\setup-windows.ps1'
Get-Content -LiteralPath $setup
& $setup -ExpectedUserSid 'S-1-5-21-...'
```

Replace `<ubuntu-user>` with your Ubuntu username. Review the public CA fingerprint and confirm trust. See [Windows CA](WINDOWS-CA.md).

The setup registers `DevStack Hosts Event Sync`, which synchronizes the managed hosts block on demand.

```powershell
Get-ScheduledTask -TaskName 'DevStack Hosts Event Sync'
Get-ScheduledTaskInfo -TaskName 'DevStack Hosts Event Sync'
```

## 4. Start working

With Ubuntu running, open **https://devstack.test**. Sign in, create a project and open its local URL. Configure the project directory, document root, local domain, HTTPS and database as required.

For optional Windows widgets, see [installation](../windows/widgets/README.md#installation).

## 5. Update Windows hosts integration

After updating the installed Linux application, run the following in elevated Windows PowerShell with Ubuntu running:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
$installer = '\\wsl.localhost\Ubuntu-26.04\home\<ubuntu-user>\projects\devstack-source\windows\install-hosts-sync.ps1'
Get-Content -LiteralPath $installer
& $installer -ExpectedUserSid 'S-1-5-21-...'
```

Replace the username as above. The script publishes immutable protected script releases under `C:\DevStack\Hosts` and changes only the existing task action. Previous task definitions and code are retained for rollback. Existing certificate trust is preserved. An existing user-owned root must first complete the separate notification/root migration; do not copy privileged scripts into it.

See [troubleshooting](TROUBLESHOOTING.md) for service and synchronization checks.
