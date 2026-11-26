# Run as the ordinary Windows user, not in the administrator terminal.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{32}$')][string]$TransactionId)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or $PSVersionTable.PSEdition -ne 'Desktop') {
    throw 'Use ordinary Windows PowerShell 5.1.'
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run the notification copy without elevation.'
}
. (Join-Path $PSScriptRoot 'windows-layout.ps1')
. (Join-Path $PSScriptRoot 'notification-layout-copy.ps1')
Copy-DevStackNotificationLayout $TransactionId $identity.User
Write-Output 'Both notification files staged and verified without elevation.'
