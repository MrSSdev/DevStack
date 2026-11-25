# One-time foreground recovery after the actual first open of the widget board.
# No timer service, scheduled task, WSL command or automatic panel activation.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$packages = @(Get-AppxPackage -Name 'MrSSdev.DevStack.Widgets' -ErrorAction Stop)
if ($packages.Count -ne 1 -or [string]$packages[0].Status -ne 'Ok') {
    throw 'A single healthy DevStack Widgets package must be installed first.'
}

Write-Host ''
Write-Host 'Complete widget initialization:'
Write-Host '1. Open Win+W once. If necessary, add the Ubuntu and DevStack widgets.'
Write-Host '2. Wait until the widgets show content or their loading indicator.'
Write-Host '3. Close the widget panel (Esc or Win+W) and return to PowerShell.'
$answer = Read-Host 'Press ENTER after these steps, or type SKIP to defer the final reset'
if ($answer -ne '') {
    Write-Warning 'First-open reset deferred. Run Complete-WidgetUpdate.ps1 when ready.'
    return
}

# Reset only the current interactive Windows session, after the user has
# actually initialized the board. Process existence alone is not proof that
# Win+W was opened; a fixed sleep after deployment is not sufficient either.
$session = (Get-Process -Id $PID -ErrorAction Stop).SessionId
$targets = @(Get-Process -Name 'WidgetBoard', 'DevStack.Widgets' -ErrorAction SilentlyContinue |
    Where-Object { $_.SessionId -eq $session })
if (!$targets.Count) {
    Write-Warning 'No widget processes found in this session. Reopen Win+W and verify the cards.'
    return
}
$resetErrors = @()
$targets | Stop-Process -Force -ErrorAction SilentlyContinue -ErrorVariable resetErrors
if ($resetErrors.Count -gt 0) {
    throw 'Final widget reset reported an error. Reopen Win+W and verify; do not reinstall Ubuntu or telemetry.'
}
Write-Host 'First-open widget process reset completed. Reopen Win+W to verify both widgets.'
