# One-time Windows setup: explicit CA trust, then event-driven hosts updates.
# Run from an elevated Windows PowerShell session under the WSL owner's account.
[CmdletBinding()]
param([switch]$SkipCaTrust, [Parameter(Mandatory=$true)][string]$ExpectedUserSid)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$admin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin -or [Security.Principal.WindowsIdentity]::GetCurrent().User.Value -cne $ExpectedUserSid) { throw 'Use elevated Windows PowerShell under the intended account.' }

if (-not $SkipCaTrust) {
    Write-Host 'One-time local CA trust check. Review the printed fingerprint before answering YES.' -ForegroundColor Yellow
    & (Join-Path $PSScriptRoot 'sync-hosts.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Initial Windows hosts/CA setup failed.' }
}

& (Join-Path $PSScriptRoot 'install-hosts-sync.ps1') -ExpectedUserSid $ExpectedUserSid
Write-Host 'Windows setup complete. New DevStack domains will be synchronized on project changes.' -ForegroundColor Green
