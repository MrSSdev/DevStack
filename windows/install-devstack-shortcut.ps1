# Install a per-user DevStack shortcut in the Windows Start menu.
# Does not register tasks, change Windows hosts, modify CA trust or start Ubuntu.
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$source = Join-Path $PSScriptRoot 'open-devstack.ps1'
$destinationDir = Join-Path $env:LOCALAPPDATA 'DevStack'
$destination = Join-Path $destinationDir 'open-devstack.ps1'
$startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$shortcutPath = Join-Path $startMenu 'DevStack.lnk'
$powershell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'

if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
    throw "Missing launcher source: $source"
}
if (-not (Test-Path -LiteralPath $powershell -PathType Leaf)) {
    throw "Windows PowerShell was not found: $powershell"
}

# Reject an invalid source before copying it into the user profile.
$tokens = $null
$parseErrors = $null
[void][System.Management.Automation.Language.Parser]::ParseFile(
    $source, [ref]$tokens, [ref]$parseErrors
)
if ($parseErrors.Count -ne 0) {
    throw 'open-devstack.ps1 has PowerShell syntax errors. Not installing.'
}

New-Item -ItemType Directory -Path $destinationDir -Force | Out-Null
New-Item -ItemType Directory -Path $startMenu -Force | Out-Null
Copy-Item -LiteralPath $source -Destination $destination -Force

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $powershell
$shortcut.Arguments = '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $destination + '"'
$shortcut.WorkingDirectory = $destinationDir
$shortcut.Description = 'Open the local DevStack panel, starting Ubuntu WSL only on demand'
$shortcut.IconLocation = $powershell + ',0'
$shortcut.Save()

Write-Host 'DevStack Start-menu shortcut installed for this Windows user.' -ForegroundColor Green
Write-Host "Script: $destination"
Write-Host "Shortcut: $shortcutPath"
Write-Host 'This installer did not start Ubuntu or modify the existing hosts watcher.'
