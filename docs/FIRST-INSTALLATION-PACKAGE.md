# DevStack — first installation (Windows 11 x64 + Ubuntu 26.04 on WSL2)

Use the successful **DevStack - First Installation Package** run on `main` and download **DevStack-First-Installation-x64**. A `-test` artifact is for CI validation only; never install it. This package is for a new installation, not an update or migration. It contains a Git source bundle, the Windows setup scripts, the signed development widget MSIX and matching public certificate, optional WinRT launcher, and integrity metadata. Internet access is still required for Ubuntu, apt, npm and other dependencies. No device-specific tokens, passwords, private certificates or database files are distributed.

Extract the whole artifact locally, for example to `C:\Users\<windows-user>\Downloads\DevStack-First-Installation-x64`. Use an artifact from a trusted successful run; the hash list checks completeness and corruption, not authenticity independently of the trusted download. Read `COMMIT.txt` and compare it with the run's full commit. Review scripts before executing them.

## 1. Verify the files

In ordinary Windows PowerShell, from the extracted directory:

```powershell
Get-Content -LiteralPath .\COMMIT.txt
foreach ($line in Get-Content -LiteralPath .\SHA256SUMS.txt) {
    $hash, $relative = $line -split '  ', 2
    if ($hash -cnotmatch '^[a-f0-9]{64}$' -or $relative -cnotmatch '^[A-Za-z0-9_.@/-]+$' -or ($relative.Split('/') | Where-Object { $_ -in @('', '.', '..') })) { throw 'Invalid manifest entry' }
    if ((Get-FileHash -LiteralPath $relative -Algorithm SHA256 -ErrorAction Stop).Hash -ine $hash) { throw "Hash mismatch: $relative" }
}
```

## 2. Install Ubuntu and DevStack

In elevated Windows PowerShell, if not installed:

```powershell
wsl --install -d Ubuntu-26.04
```

Open Ubuntu, create the ordinary developer account (use `ubuntu` if you want the supplied widgets), and ensure PID 1 is systemd. Run the following in Ubuntu. Replace `<windows-user>` and adjust the package path to the actual extraction directory. The destination must be a new checkout.

```bash
sudo apt update
sudo apt install -y git ca-certificates
mkdir -p ~/projects
package='/mnt/c/Users/<windows-user>/Downloads/DevStack-First-Installation-x64'
git clone --branch main "$package/DevStack-Source.bundle" ~/projects/devstack-source
cd ~/projects/devstack-source
test "$(git rev-parse HEAD)" = "$(tr -d '\r\n' < "$package/COMMIT.txt")" &&
git remote set-url origin https://github.com/MrSSdev/DevStack.git &&
bash scripts/bootstrap-ubuntu.sh
```

Do not continue if the clone or commit verification fails. The bootstrap builds the matching frontend and prompts for authentication, using the normal installation scripts. Do not pull a newer revision until the matching initial setup is complete. The Git remote is restored to GitHub for later ordinary updates. For requirements and service checks see `docs/INSTALL-WINDOWS-WSL.md` in the checkout.

## 3. Configure Windows integration

Record the account SID in ordinary Windows PowerShell:

```powershell
[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
```

With Ubuntu running, open **Windows PowerShell 5.1 as Administrator under the same Windows account**. Set `$dir` to the extracted folder and `$sid` to the recorded SID, not an example SID. Review the local CA fingerprint and explicitly approve the CA trust only if correct.

```powershell
$dir = "$env:USERPROFILE\Downloads\DevStack-First-Installation-x64"
$sid = 'S-1-5-21-...' # Replace with the recorded SID.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$dir\windows\setup-windows.ps1" -ExpectedUserSid $sid
if ($LASTEXITCODE -ne 0) { throw 'Windows setup failed.' }
```

Open https://devstack.test and sign in. Edge can install the PWA. Mailpit is optional (`sudo bash scripts/install-mailpit.sh` from the Ubuntu checkout).

## 4. Optional Windows widgets

From the same elevated Windows PowerShell, with the same `$dir` and `$sid`:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$dir\Widgets\Install-Widgets.ps1" -ExpectedUserSid $sid -DeferFirstOpenReset
if ($LASTEXITCODE -ne 0) { throw 'Widget deployment failed.' }
```

The installer checks the matching MSIX signer, imports the public development certificate and prepares private widget configuration. With Ubuntu running, close the elevated shell and open **ordinary Windows PowerShell**:

```powershell
$dir = "$env:USERPROFILE\Downloads\DevStack-First-Installation-x64"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$dir\Widgets\Install-WslTelemetry.ps1"
if ($LASTEXITCODE -ne 0) { throw 'Telemetry setup failed.' }
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$dir\Widgets\Install-DevStackWidget.ps1" -SkipFrontend
if ($LASTEXITCODE -ne 0) { throw 'Service setup failed.' }
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$dir\Widgets\Complete-WidgetUpdate.ps1"
```

Follow the guided first-open reset, add both widgets using Win+W, and reopen the panel. The matching frontend is already built in Ubuntu; no static frontend replacement is needed.

## 5. Optional Windows local notifications

Only if you want local system notifications, prepare the private component in elevated Windows PowerShell 5.1 using the same directory and recorded SID:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$dir\windows\install-local-notifications.ps1" -Mode PrepareDirectory -ExpectedUserSid $sid
if ($LASTEXITCODE -ne 0) { throw 'Notification directory preparation failed.' }
```

Then in ordinary Windows PowerShell, with `$dir` set as before:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$dir\windows\install-local-notifications.ps1" -UseBuiltLauncher
if ($LASTEXITCODE -ne 0) { throw 'Notification installation failed.' }
```

In Ubuntu, from the matching installed checkout:

```bash
cd ~/projects/devstack-source
sudo bash scripts/enable-windows-local-notifications.sh
```

This explicitly opts in; it does not automatically enable notifications for a new installation. See `docs/LOCAL-WINDOWS-NOTIFICATIONS.md` for checks. All initial Windows configuration uses the existing protected `C:\DevStack` layout, without migration or old directory cleanup.

## 6. Later updates

Use the normal Ubuntu update command `bash scripts/update.sh` and the matching widget update instructions as needed. Do not rerun the fresh bootstrap or reinstall private integrations for routine updates. CI validates bundle contents, a real Git clone, signed MSIX and launcher; it cannot replace acceptance on physical Windows/WSL hardware.
