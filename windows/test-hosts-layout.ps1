# Disposable Windows CI. Never run WSL, modify hosts or launch this task.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true') { throw 'Disposable CI runner required.' }
. (Join-Path $PSScriptRoot 'windows-layout.ps1')
. (Join-Path $PSScriptRoot 'hosts-layout.ps1')
$user = [Security.Principal.WindowsIdentity]::GetCurrent().User
$root = 'C:\DevStack'
$source = Join-Path $env:RUNNER_TEMP ('devstack-hosts-' + [Guid]::NewGuid().ToString('N'))
function Assert-True([bool]$Condition,[string]$Label) { if (-not $Condition) { throw $Label } }
Assert-True ($null -eq (Get-DevStackLayoutItem $root)) 'Existing non-fixture root'
Assert-True ($null -eq (Get-DevStackHostsTask)) 'Existing non-fixture task'
New-Item -ItemType Directory -Path $source | Out-Null
try {
    Set-Content -LiteralPath (Join-Path $source 'watch-hosts.ps1') -Value "throw 'Fixture must never run'" -Encoding Ascii
    Set-Content -LiteralPath (Join-Path $source 'hosts-lib.ps1') -Value '# fixture' -Encoding Ascii
    Initialize-DevStackLayout -UserSid $user -Component Hosts
    $one = Publish-DevStackHostsRelease $source $user
    Assert-True ($one -match '\\release-[a-f0-9]{64}$') 'Unexpected release path'
    Assert-True ((Publish-DevStackHostsRelease $source $user) -ceq $one) 'Identical input created another release'
    Assert-DevStackHostsFile (Join-Path $one 'watch-hosts.ps1')
    Assert-DevStackHostsFile (Join-Path $one 'hosts-lib.ps1')
    $hash = (Get-FileHash -LiteralPath (Join-Path $one 'watch-hosts.ps1')).Hash
    Set-Content -LiteralPath (Join-Path $source 'watch-hosts.ps1') -Value "throw 'Second fixture must never run'" -Encoding Ascii
    $two = Publish-DevStackHostsRelease $source $user
    Assert-True ($two -cne $one) 'Changed release reused active directory'
    Assert-True ((Get-FileHash -LiteralPath (Join-Path $one 'watch-hosts.ps1')).Hash -ceq $hash) 'Previous release changed'
    $ps = Join-Path ([Environment]::SystemDirectory) 'WindowsPowerShell\v1.0\powershell.exe'
    Set-DevStackHostsTaskRelease $one $user $ps $null
    $task = Get-DevStackHostsTask
    Assert-DevStackHostsTask $task $user $ps
    Assert-True ($task.Actions[0].Arguments -ceq (Get-DevStackHostsActionArgument (Join-Path $one 'watch-hosts.ps1'))) 'Wrong first task action'
    $settingsBefore = [string]$task.Settings.MultipleInstances
    Set-DevStackHostsTaskRelease $two $user $ps $task
    $task = Get-DevStackHostsTask
    Assert-True ($task.Actions[0].Arguments -ceq (Get-DevStackHostsActionArgument (Join-Path $two 'watch-hosts.ps1'))) 'Wrong replacement action'
    Assert-True ([string]$task.Settings.MultipleInstances -ceq $settingsBefore) 'Task settings changed'
    Assert-DevStackHostsTask $task $user $ps
    $backups = @(Get-ChildItem -LiteralPath (Join-Path $root 'Hosts') -Filter 'task-before-*.xml')
    Assert-True ($backups.Count -eq 1) 'Previous task XML not retained'
    Assert-DevStackHostsFile $backups[0].FullName
    Set-DevStackHostsTaskRelease $two $user $ps $task
    Assert-True (@(Get-ChildItem -LiteralPath (Join-Path $root 'Hosts') -Filter 'task-before-*.xml').Count -eq 1) 'Repeat switch created a backup'
    Set-DevStackHostsTaskRelease $one $user $ps (Get-DevStackHostsTask)
    Assert-True ((Get-DevStackHostsTask).Actions[0].Arguments -ceq (Get-DevStackHostsActionArgument (Join-Path $one 'watch-hosts.ps1'))) 'Rollback action not restored'
    Write-Host 'Protected releases and S4U action-only task switch/rollback: OK.'
} finally {
    $task = Get-DevStackHostsTask
    if ($null -ne $task) { Unregister-ScheduledTask -TaskName 'DevStack Hosts Event Sync' -TaskPath '\' -Confirm:$false }
    if (Test-Path -LiteralPath (Join-Path $root 'Hosts')) { Remove-Item -LiteralPath (Join-Path $root 'Hosts') -Recurse -Force }
    if (Test-Path -LiteralPath $root) { [IO.Directory]::Delete($root,$false) }
    Remove-Item -LiteralPath $source -Recurse -Force
}
