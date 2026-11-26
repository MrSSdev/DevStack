# Existing installation migration from the matching, verified Windows bundle.
# Start as the signed-in ordinary user. UAC is limited to directory, Hosts,
# and MSIX operations. No WSL launch, private token rotation or cleanup.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{40}$')][string]$ExpectedCommit)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSEdition -ne 'Desktop') { throw 'Use ordinary Windows PowerShell 5.1.' }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Start in ordinary Windows PowerShell; the required administrator operations request UAC.'
}
$bundle = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\')
if ($bundle.StartsWith('\\')) { throw 'Extract the complete verified bundle to the local Windows disk first.' }
$layout = Join-Path $bundle 'layout'
$commit = [IO.File]::ReadAllText((Join-Path $bundle 'COMMIT.txt')).Trim()
if ($commit -cne $ExpectedCommit) { throw 'The bundle commit does not match the accepted revision.' }
$listed = @{}
foreach ($line in Get-Content -LiteralPath (Join-Path $bundle 'SHA256SUMS.txt')) {
    if ($line -cnotmatch '^([a-f0-9]{64})  ([A-Za-z0-9_.@\/-]+)$') { throw 'Invalid bundle hash entry.' }
    $expected = $Matches[1]; $relative = $Matches[2]
    if ($relative.Split('/') -contains '..' -or $relative.Split('/') -contains '.' -or $listed.ContainsKey($relative)) { throw 'Unsafe bundle manifest path.' }
    $path = [IO.Path]::GetFullPath((Join-Path $bundle $relative))
    if (-not $path.StartsWith($bundle + '\',[StringComparison]::OrdinalIgnoreCase) -or
        (Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Unsafe bundle file.' }
    if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ine $expected) { throw "Bundle hash mismatch: $relative" }
    $listed[$relative] = $true
}
foreach ($name in @('Install-Widgets.ps1','Complete-WidgetUpdate.ps1','Migrate-WidgetLayout.ps1','Widget-Layout.ps1','windows-layout.ps1',
    'DevStack-Widgets-x64.msix','DevStack-Widgets-Dev.cer','layout/migrate-notification-layout.ps1','layout/windows-layout.ps1',
    'layout/notification-layout-copy.ps1','layout/windows-layout-migration.ps1','layout/layout-directory-identity.cs',
    'layout/initialize-windows-layout.ps1','layout/install-hosts-sync.ps1','layout/hosts-layout.ps1','layout/watch-hosts.ps1','layout/hosts-lib.ps1')) {
    if (-not $listed.ContainsKey($name)) { throw "Missing required verified bundle entry: $name" }
}
. (Join-Path $layout 'windows-layout.ps1')
$ps = Join-Path ([Environment]::SystemDirectory) 'WindowsPowerShell\v1.0\powershell.exe'
$sid = $identity.User.Value
function Assert-UbuntuStopped {
    $wsl = Join-Path ([Environment]::SystemDirectory) 'wsl.exe'
    $running = @(& $wsl --list --running --quiet)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot verify WSL state.' }
    if (@($running | ForEach-Object { (([string]$_) -replace '[\x00\uFEFF]','').Trim() }) -ccontains 'Ubuntu-26.04') {
        throw 'Stop Ubuntu deliberately and keep it stopped through the Windows migration.'
    }
}
function Quote-Path([string]$Path) { return "'" + $Path.Replace("'","''") + "'" }
function Invoke-AdminStep([string]$Command,[string]$Label) {
    $code = '$ErrorActionPreference = "Stop"; try { ' + $Command + '; exit 0 } catch { Write-Error -ErrorAction Continue $_; exit 1 }'
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($code))
    $process = Start-Process -FilePath $ps -Verb RunAs -ArgumentList @('-NoLogo','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-EncodedCommand',$encoded) -Wait -PassThru
    try { if ($process.ExitCode -ne 0) { throw "$Label failed (exit $($process.ExitCode)). Preserve existing backups." } }
    finally { $process.Dispose() }
}
Assert-UbuntuStopped
$package = @(Get-AppxPackage -Name 'MrSSdev.DevStack.Widgets' -ErrorAction Stop)
if ($package.Count -ne 1 -or [string]$package[0].Status -ne 'Ok') { throw 'This migration expects the existing healthy DevStack widget package.' }
$targetVersion = [version]'0.5.2.0'
if ([version]$package[0].Version -gt $targetVersion) { throw 'Refusing to downgrade a newer widget package.' }

& (Join-Path $layout 'migrate-notification-layout.ps1')
Assert-UbuntuStopped
$prepare = Quote-Path (Join-Path $layout 'initialize-windows-layout.ps1')
$hosts = Quote-Path (Join-Path $layout 'install-hosts-sync.ps1')
Invoke-AdminStep "& $prepare -Mode Prepare -ExpectedUserSid '$sid' -Component Widgets; & $hosts -ExpectedUserSid '$sid'" 'Protected Hosts and Widgets preparation'
Assert-UbuntuStopped
if ([version]$package[0].Version -lt $targetVersion) {
    & (Join-Path $bundle 'Migrate-WidgetLayout.ps1')
    Assert-UbuntuStopped
    $installer = Quote-Path (Join-Path $bundle 'Install-Widgets.ps1')
    Invoke-AdminStep "& $installer -ExpectedUserSid '$sid' -DeferFirstOpenReset" 'Widget package deployment'
}
Assert-DevStackLayoutComponent -Component LocalNotifications -UserSid $identity.User
# Hosts is intentionally inaccessible to the ordinary token; its elevated
# installer already verified directory, files and task contract.
Assert-DevStackLayoutComponent -Component Widgets -UserSid $identity.User
$installed = @(Get-AppxPackage -Name 'MrSSdev.DevStack.Widgets' -ErrorAction Stop)
if ($installed.Count -ne 1 -or [version]$installed[0].Version -ne $targetVersion -or [string]$installed[0].Status -ne 'Ok') { throw 'New widget package not verified.' }
& (Join-Path $bundle 'Complete-WidgetUpdate.ps1')
Write-Output 'Windows layout migration steps completed. Retain all old trees until physical acceptance. No Linux integration was reinstalled.'
