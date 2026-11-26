# Run from a verified complete DevStack checkout/bundle as the signed-in
# ordinary Windows user. Requests UAC only for root directory operations.
# This is the notification/root substep, not a complete Hosts/Widgets migration.
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or $PSVersionTable.PSEdition -ne 'Desktop') {
    throw 'Use ordinary Windows PowerShell 5.1.'
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Start this orchestrator in ordinary Windows PowerShell, not as administrator.'
}
. (Join-Path $PSScriptRoot 'windows-layout.ps1')
. (Join-Path $PSScriptRoot 'notification-layout-copy.ps1')
$root = 'C:\DevStack'
$component = Join-Path $root 'LocalNotifications'
$transaction = [Guid]::NewGuid().ToString('N')
$engine = Join-Path $PSScriptRoot 'windows-layout-migration.ps1'
$ps = Join-Path ([Environment]::SystemDirectory) 'WindowsPowerShell\v1.0\powershell.exe'
function Assert-Quiescent {
    # Read-only check: do not start Ubuntu or stop it behind the user's back.
    $wsl = Join-Path ([Environment]::SystemDirectory) 'wsl.exe'
    $running = @(& $wsl --list --running --quiet)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot verify WSL running state.' }
    foreach ($entry in $running) {
        if ((([string]$entry) -replace '[\x00\uFEFF]','').Trim() -ceq 'Ubuntu-26.04') {
            throw 'Stop Ubuntu-26.04 deliberately before the migration and keep it stopped.'
        }
    }
    if (@(Get-Process -Name 'DevStack.Notifications' -ErrorAction SilentlyContinue).Count -ne 0) {
        throw 'The notification launcher is still running. Retry after it exits.'
    }
}
function Invoke-ElevatedMigration([string]$Mode) {
    if ($Mode -notin @('Stage','Publish','Rollback')) { throw 'Unexpected migration mode.' }
    # The only elevated code is this verified bundle's fixed engine. No
    # elevated read/copy/launch of old or newly staged private payloads.
    $quoted = "'" + $engine.Replace("'","''") + "'"
    $command = '$ErrorActionPreference = "Stop"; try { & ' + $quoted +
        " -Mode $Mode -ExpectedUserSid '$($identity.User.Value)' -TransactionId '$transaction'; exit 0 } catch { Write-Error -ErrorAction Continue $_; exit 1 }"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    $process = Start-Process -FilePath $ps -Verb RunAs -ArgumentList @('-NoLogo','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-EncodedCommand',$encoded) -Wait -PassThru
    try { if ($process.ExitCode -ne 0) { throw "Elevated $Mode failed (exit $($process.ExitCode))." } }
    finally { $process.Dispose() }
}
if ((Get-DevStackLayoutState $identity.User) -eq 'Secure') {
    Assert-DevStackLayoutComponent -Component LocalNotifications -UserSid $identity.User
    Assert-DevStackNotificationPayload $component
    Write-Output 'Notification root already provisioned. No migration performed.'
    return
}
Assert-Quiescent
if ((Get-DevStackLayoutState $identity.User) -ne 'LegacyUserOwned') { throw 'Expected existing legacy DevStack root.' }
Assert-DevStackNotificationPayload $component
Assert-DevStackNotificationPrivateFiles $component $identity.User
$hashes = @{}
foreach ($name in @('DevStack.Notifications.exe','show-local-notification.ps1')) {
    $hashes[$name] = (Get-FileHash -LiteralPath (Join-Path $component $name) -Algorithm SHA256).Hash
}
Write-Host "Migration transaction: $transaction"
try {
    Invoke-ElevatedMigration Stage
    Copy-DevStackNotificationLayout $transaction $identity.User
    Assert-Quiescent
    Invoke-ElevatedMigration Publish
    try {
        Assert-DevStackLayoutComponent -Component LocalNotifications -UserSid $identity.User
        Assert-DevStackNotificationPayload $component
        Assert-DevStackNotificationPrivateFiles $component $identity.User
        foreach ($name in $hashes.Keys) {
            if ((Get-FileHash -LiteralPath (Join-Path $component $name) -Algorithm SHA256).Hash -cne $hashes[$name]) {
                throw "Published notification file differs: $name"
            }
        }
    } catch {
        $failure = $_
        Assert-Quiescent
        Invoke-ElevatedMigration Rollback
        throw $failure
    }
} catch {
    throw "Migration interrupted or failed. Keep Ubuntu stopped and preserve transaction $transaction. $($_.Exception.Message)"
}
Write-Output "Notification root migrated; original retained in C:\DevStack-migration-$transaction\previous."
Write-Output 'No notification identity, WSL marker, Hosts task or widget setting was changed.'
