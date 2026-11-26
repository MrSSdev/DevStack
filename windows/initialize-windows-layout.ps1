# Bootstrap for the shared Windows component layout.
# This increment does not migrate an existing user-owned root, install code,
# change scheduled tasks, touch the registry, or start WSL.
[CmdletBinding()]
param(
    [ValidateSet('Inspect','Prepare')][string]$Mode = 'Inspect',
    [ValidateSet('Hosts','LocalNotifications','Widgets')][string[]]$Component,
    [string]$ExpectedUserSid
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or $PSVersionTable.PSEdition -ne 'Desktop') {
    throw 'Use Windows PowerShell 5.1.'
}
. (Join-Path $PSScriptRoot 'windows-layout.ps1')
$userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
if ($ExpectedUserSid -and $ExpectedUserSid -cne $userSid.Value) {
    throw 'The elevated account does not match the intended Windows user.'
}
if ($Mode -eq 'Inspect') {
    [pscustomobject]@{
        Root = 'C:\DevStack'
        UserSid = $userSid.Value
        State = Get-DevStackLayoutState $userSid
    }
    return
}
if (-not $ExpectedUserSid -or -not $Component) {
    throw 'Prepare requires -ExpectedUserSid from the intended user and one or more -Component names.'
}
Initialize-DevStackLayout -UserSid $userSid -Component $Component
Write-Output 'Requested Windows component directories are provisioned. No component files were installed.'
