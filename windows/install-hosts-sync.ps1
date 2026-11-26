# One-time installation or elevated upgrade from a verified bundle.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$ExpectedUserSid)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or $PSVersionTable.PSEdition -ne 'Desktop') { throw 'Use elevated Windows PowerShell 5.1.' }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if ($identity.User.Value -cne $ExpectedUserSid -or
    -not ([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Use elevated Windows PowerShell under the intended Windows account.'
}
. (Join-Path $PSScriptRoot 'windows-layout.ps1')
. (Join-Path $PSScriptRoot 'hosts-layout.ps1')
$ps = Join-Path ([Environment]::SystemDirectory) 'WindowsPowerShell\v1.0\powershell.exe'
$previous = Get-DevStackHostsTask
if ($null -ne $previous) { Assert-DevStackHostsTask $previous $identity.User $ps }
$wsl = Join-Path ([Environment]::SystemDirectory) 'wsl.exe'
$running = @(& $wsl --list --running --quiet)
if ($LASTEXITCODE -ne 0) { throw 'Could not verify WSL running state.' }
$ubuntu = @($running | ForEach-Object { (([string]$_) -replace '[\x00\uFEFF]','').Trim() }) -ccontains 'Ubuntu-26.04'
if ($null -eq $previous -and -not $ubuntu) { throw 'For fresh Hosts integration, deliberately start Ubuntu and install DevStack first.' }
if ($ubuntu) {
    & $wsl -d Ubuntu-26.04 -u root -- test -f /opt/devstack/scripts/list-domains.py
    if ($LASTEXITCODE -ne 0) { throw 'WSL DevStack domain export script is missing.' }
}
Initialize-DevStackLayout -UserSid $identity.User -Component Hosts
$release = Publish-DevStackHostsRelease $PSScriptRoot $identity.User
if ($null -eq $previous) {
    & $ps -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File (Join-Path $release 'watch-hosts.ps1') -Once
    if ($LASTEXITCODE -ne 0) { throw 'Initial hosts synchronization failed; no task installed.' }
}
Set-DevStackHostsTaskRelease $release $identity.User $ps $previous
Write-Host 'DevStack Hosts integration installed in C:\DevStack\Hosts.'
Write-Host 'On-demand S4U task only; no logon trigger, no permanent Windows process.'
Write-Host 'Existing CA trust, legacy backups and previous releases are preserved.'
if (-not $ubuntu) { Write-Host 'Ubuntu remains stopped. Reconciliation will run after its next deliberate start.' }
