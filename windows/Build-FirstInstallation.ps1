# CI-only assembly of a fresh-install package. No installation or migration.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$WidgetBundle,
    [Parameter(Mandatory=$true)][string]$OutputDirectory
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$widgets = (Resolve-Path $WidgetBundle).Path
$output = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $output) { throw 'First-install output must not already exist.' }
if ($env:GITHUB_SHA -cnotmatch '^[a-f0-9]{40}$') { throw 'Missing build commit.' }
$commit = (& git -C $root rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $commit -cne $env:GITHUB_SHA) { throw 'Source checkout mismatch.' }
$widgetCommit = (Get-Content -LiteralPath (Join-Path $widgets 'COMMIT.txt') -Raw).Trim()
if ($widgetCommit -cne $commit) { throw 'Widget bundle commit mismatch.' }
New-Item -ItemType Directory -Path $output | Out-Null
$required = [System.Collections.Generic.List[string]]::new()
function Copy-BundleFile([string]$Source,[string]$Relative) {
    $target = Join-Path $output $Relative
    $parent = Split-Path -Parent $target
    if (!(Test-Path -LiteralPath $parent -PathType Container)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $sourceItem = Get-Item -LiteralPath $Source -ErrorAction Stop
    if ($sourceItem.PSIsContainer -or ($sourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $sourceItem.Length -eq 0) {
        throw "Invalid package input: $Relative"
    }
    Copy-Item -LiteralPath $Source -Destination $target
    if ((Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash -cne (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash) {
        throw "Copy verification failed: $Relative"
    }
    $required.Add($Relative)
}

# Reuse the signed and verified MSIX, CER and exact runtime installer files.
# Never copy the migration entry points, legacy fixtures or preview artifacts.
$widgetFiles = @(
    'DevStack-Widgets-x64.msix','DevStack-Widgets-Dev.cer',
    'Install-Widgets.ps1','Complete-WidgetUpdate.ps1',
    'Install-WslTelemetry.ps1','Install-DevStackWidget.ps1',
    'Widget-Layout.ps1','windows-layout.ps1',
    'telemetry/collector.py','telemetry/devstack-widget-metrics.socket',
    'telemetry/devstack-widget-metrics@.service',
    'services/bridge.py','services/setup.py','services/devstack-widget-services.socket',
    'services/devstack-widget-services@.service','services/devstack-widget-control.socket',
    'services/devstack-widget-control@.service'
)
foreach ($relative in $widgetFiles) {
    Copy-BundleFile (Join-Path $widgets $relative) ('Widgets/' + $relative)
}

# Local copies of the matching Windows setup scripts; the checkout also contains
# these scripts. The optional WinRT launcher is built using its existing test.
$setupFiles = @(
    'windows/setup-windows.ps1','windows/sync-hosts.ps1',
    'windows/install-hosts-sync.ps1','windows/hosts-layout.ps1',
    'windows/hosts-lib.ps1','windows/watch-hosts.ps1','windows/windows-layout.ps1',
    'windows/initialize-windows-layout.ps1',
    'windows/install-local-notifications.ps1','windows/local-notification-launcher.cs',
    'scripts/windows-local-notification.ps1'
)
foreach ($relative in $setupFiles) {
    Copy-BundleFile (Join-Path $root $relative) $relative
}
$launcher = Join-Path $output 'windows/DevStack.Notifications.exe'
& (Join-Path $root 'windows/test-local-notification-launcher.ps1') -OutputExecutable $launcher
if (!(Test-Path -LiteralPath $launcher -PathType Leaf) -or (Get-Item -LiteralPath $launcher).Length -eq 0) {
    throw 'Missing tested notification launcher.'
}
$required.Add('windows/DevStack.Notifications.exe')
Copy-BundleFile (Join-Path $root 'docs/FIRST-INSTALLATION-PACKAGE.md') 'START-HERE.md'

# A real Git bundle retains the main history and exact build revision. A ZIP
# of the source tree cannot be used by scripts/update.sh as a Git checkout.
# For PR validation only, use the tested synthetic merge as the bundle main.
& git -C $root update-ref refs/heads/main $commit
if ($LASTEXITCODE -ne 0) { throw 'Could not create package main ref.' }
$bundle = Join-Path $output 'DevStack-Source.bundle'
& git -C $root bundle create $bundle refs/heads/main
if ($LASTEXITCODE -ne 0) { throw 'Source bundle creation failed.' }
& git -C $root bundle verify $bundle
if ($LASTEXITCODE -ne 0) { throw 'Source bundle verification failed.' }
$clone = Join-Path $env:RUNNER_TEMP ('devstack-install-check-' + [Guid]::NewGuid().ToString('N'))
try {
    & git clone --quiet --branch main $bundle $clone
    if ($LASTEXITCODE -ne 0) { throw 'Source bundle cannot be cloned.' }
    $clonedCommit = (& git -C $clone rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $clonedCommit -cne $commit) { throw 'Cloned commit mismatch.' }
    if ((& git -C $clone branch --show-current).Trim() -cne 'main') { throw 'Clone does not start on main.' }
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect clone branch.' }
    & git -C $clone remote set-url origin https://github.com/MrSSdev/DevStack.git
    if ($LASTEXITCODE -ne 0) { throw 'Clone cannot set its update origin.' }
    if (!(Test-Path -LiteralPath (Join-Path $clone 'scripts/bootstrap-ubuntu.sh') -PathType Leaf)) {
        throw 'Missing Ubuntu bootstrap in source bundle.'
    }
} finally {
    if (Test-Path -LiteralPath $clone) { Remove-Item -LiteralPath $clone -Recurse -Force }
}
$required.Add('DevStack-Source.bundle')
Set-Content -LiteralPath (Join-Path $output 'COMMIT.txt') -Value $commit -Encoding Ascii
$required.Add('COMMIT.txt')

# Check the exact allowlist, parse every shipped PowerShell script, and hash
# the payload after the final copy. Private signing keys never enter it.
$files = @(Get-ChildItem -LiteralPath $output -Recurse -File -Force | Sort-Object FullName)
$relativeFiles = @($files | ForEach-Object { $_.FullName.Substring($output.Length + 1).Replace('\','/') })
if (@(Compare-Object ($required | Sort-Object) ($relativeFiles | Sort-Object)).Count -ne 0) {
    throw 'Unexpected or missing first-installation file.'
}
foreach ($file in $files | Where-Object { $_.Extension -eq '.ps1' }) {
    $tokens = $null
    $errors = $null
    [Management.Automation.Language.Parser]::ParseFile($file.FullName,[ref]$tokens,[ref]$errors) | Out-Null
    if ($errors.Count) { $errors | Format-List; throw "Invalid packaged script: $($file.Name)" }
}
$hashes = foreach ($file in $files) {
    '{0}  {1}' -f (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant(), $file.FullName.Substring($output.Length + 1).Replace('\','/')
}
Set-Content -LiteralPath (Join-Path $output 'SHA256SUMS.txt') -Value $hashes -Encoding Ascii
Write-Host "Verified first-install package: $($required.Count) payload files, commit $commit."
