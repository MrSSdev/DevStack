# Run once as the ordinary signed-in user after administrator provisioning.
# No Linux commands, token regeneration, package deployment or deletion.
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run widget data migration without elevation.'
}
. (Join-Path $PSScriptRoot 'Widget-Layout.ps1')
$null = Get-DevStackWidgetDirectory
$legacy = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'DevStack\Widgets'
$session = [Diagnostics.Process]::GetCurrentProcess().SessionId
# Close Win+W before running this one-shot copy; do not open it again
# until the matching package has been deployed. No other user is stopped.
Get-Process -Name WidgetBoard,'DevStack.Widgets' -ErrorAction SilentlyContinue |
    Where-Object { $_.SessionId -eq $session } | Stop-Process -Force -ErrorAction Stop
Copy-DevStackWidgetState $legacy
Write-Output 'Widget data copied and verified. Existing legacy data is retained for rollback.'
