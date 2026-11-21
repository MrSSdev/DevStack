# One-time Windows setup of event-driven DevStack hosts synchronization.
# Run ONCE in Windows PowerShell as Administrator under the Windows user who owns WSL.
# Certificate trust is deliberately handled by the existing interactive sync-hosts.ps1.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$admin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'Open Windows PowerShell as Administrator.' }

$targetDir = Join-Path $env:ProgramData 'DevStack'
$taskName = 'DevStack Hosts Event Sync'
$account = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$sourceDir = $PSScriptRoot
$windowsWatcher = Join-Path $sourceDir 'watch-hosts.ps1'
$windowsLib = Join-Path $sourceDir 'hosts-lib.ps1'
if (-not (Test-Path -LiteralPath $windowsWatcher -PathType Leaf) -or
    -not (Test-Path -LiteralPath $windowsLib -PathType Leaf)) {
    throw 'Missing Windows sync scripts. Use a complete DevStack Git checkout.'
}
$source = @('watch-hosts.ps1', 'hosts-lib.ps1')
$wsl = Join-Path $env:WINDIR 'System32\wsl.exe'
& $wsl -d Ubuntu-26.04 -u root -- test -f /opt/devstack/scripts/watch-domains.py
if ($LASTEXITCODE -ne 0) {
    throw 'WSL event watcher is missing. Install or upgrade DevStack inside Ubuntu first.'
}
& $wsl -d Ubuntu-26.04 -u root -- test -f /opt/devstack/scripts/list-domains.py
if ($LASTEXITCODE -ne 0) {
    throw 'WSL DevStack domain export script is missing.'
}

New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
# Only trusted Windows local administrators and SYSTEM may modify code run elevated.
& icacls.exe $targetDir /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not secure the DevStack Windows folder.' }
foreach ($file in $source) {
    Copy-Item -LiteralPath (Join-Path $sourceDir $file) -Destination (Join-Path $targetDir $file) -Force
}

$watcherInstalled = Join-Path $targetDir 'watch-hosts.ps1'
$powershell = Join-Path $PSHOME 'powershell.exe'
if (-not (Test-Path -LiteralPath $powershell)) {
    $powershell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
}
# Verify actual Windows hosts write BEFORE reporting installation success.
# This catches malformed legacy blocks, WSL errors, or ACL problems immediately.
Write-Host 'Checking initial DevStack hosts synchronization...' -ForegroundColor Cyan
& $powershell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $watcherInstalled -Once
if ($LASTEXITCODE -ne 0) {
    throw 'Initial Windows hosts synchronization failed. Scheduled task not updated.'
}

$action = New-ScheduledTaskAction -Execute $powershell -Argument (
    '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $watcherInstalled + '"'
)
$principal = New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Highest
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $account
# Keep the listener active on laptops, including battery-powered WSL workstations.
# Windows Task Scheduler otherwise defaults to rejecting starts on battery power.
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable
# Replace the previously queued or running version cleanly on upgrades.
$previous = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($null -ne $previous) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
}
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Event-based .test domain sync, only when DevStack projects change; no timer polling.' -Force | Out-Null

# Start immediately; it will also reconnect after WSL shutdown or resume.
Start-ScheduledTask -TaskName $taskName
Write-Host ''
Write-Host 'DevStack Windows event synchronization installed.' -ForegroundColor Green
Write-Host 'Task:' $taskName
Write-Host 'Runs elevated for Windows account:' $account
Write-Host 'A one-time initial sync occurs when the listener starts.'
Write-Host 'CA trust is never changed by this task.'
Write-Host 'To inspect:' "Get-ScheduledTask -TaskName '$taskName'"
