# Run only on a disposable Windows CI machine, not a workstation.
param([Parameter(Mandatory=$true)][string]$PackageRoot)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true') { throw 'Disposable GitHub runner required.' }

function Assert-Rejected([scriptblock]$Action, [string]$Label) {
    $rejected = $false
    try { & $Action | Out-Null } catch { $rejected = $true }
    if (-not $rejected) { throw "Unexpected success: $Label" }
}
function Assert-True([bool]$Condition, [string]$Label) {
    if (-not $Condition) { throw $Label }
}

# Exercise the actual permission-check function without sending notifications
# to an unattended desktop. The real first toast is a workstation test.
$source = Join-Path $PSScriptRoot '..\scripts\windows-local-notification.ps1'
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile((Resolve-Path $source), [ref]$tokens, [ref]$errors)
Assert-True ($errors.Count -eq 0) 'Invalid WinRT script'
$definition = $ast.Find({ param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Assert-DevStackNotificationSetting'
}, $true)
Assert-True ($null -ne $definition) 'Missing notification setting check'
. ([scriptblock]::Create($definition.Extent.Text))
Assert-DevStackNotificationSetting { 'Enabled' }
Assert-DevStackNotificationSetting { throw [Runtime.InteropServices.COMException]::new('Not found', -2147023728) }
foreach ($setting in @('DisabledForApplication', 'DisabledForUser', 'DisabledByGroupPolicy', 'DisabledByManifest', $null)) {
    Assert-Rejected { Assert-DevStackNotificationSetting { $setting } } "Blocked setting: $setting"
}
Assert-Rejected { Assert-DevStackNotificationSetting { throw [Runtime.InteropServices.COMException]::new('Access denied', -2147024891) } } 'Unrelated HRESULT'
Write-Output 'First-notification HRESULT and disabled-setting handling: OK.'

$installer = Join-Path $PSScriptRoot 'install-local-notifications.ps1'
$packageInstaller = Join-Path $PackageRoot 'windows\install-local-notifications.ps1'
$root = 'C:\DevStack'
$component = Join-Path $root 'LocalNotifications'
$launcher = Join-Path $component 'DevStack.Notifications.exe'
$installedScript = Join-Path $component 'show-local-notification.ps1'
$legacyRoot = Join-Path $env:LOCALAPPDATA 'DevStack'
$legacy = Join-Path $legacyRoot 'LocalNotifications'
$registration = 'HKCU:\Software\Classes\AppUserModelId\DevStack.LocalNotifications'
$settings = 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Notifications\Settings\DevStack.LocalNotifications'
foreach ($path in @($root, $legacyRoot, $registration, $settings)) {
    Assert-True (-not (Test-Path -LiteralPath $path)) "Existing non-fixture data: $path"
}
$fixtureConfig = Join-Path $root 'Config'
$fixtureWidgets = Join-Path $legacyRoot 'Widgets'
$unexpected = Join-Path $component 'unexpected.txt'
try {
    & $installer
    Assert-True (Test-Path -LiteralPath $launcher -PathType Leaf) 'Missing compiled launcher'
    Assert-True ((Get-FileHash -LiteralPath $source).Hash -eq (Get-FileHash -LiteralPath $installedScript).Hash) 'Incorrect WinRT script'
    Assert-True ((Get-Acl -LiteralPath $root).AreAccessRulesProtected) 'Root ACL inherits unrelated accounts'
    Assert-True ((Get-ItemProperty -LiteralPath $registration).DisplayName -eq 'DevStack') 'Missing registration'
    $originalHash = (Get-FileHash -LiteralPath $launcher).Hash
    # Unknown component content must never be silently removed.
    Set-Content -LiteralPath $unexpected -Value 'preserve me'
    Assert-Rejected { & $installer } 'Unowned component file'
    Assert-True (Test-Path -LiteralPath $unexpected) 'Unowned file was deleted'
    Assert-True ((Get-FileHash -LiteralPath $launcher).Hash -eq $originalHash) 'Failed update replaced existing launcher'
    Remove-Item -LiteralPath $unexpected
    # Reject a missing binary in a staged package, preserving the old version.
    $built = Join-Path $PackageRoot 'windows\DevStack.Notifications.exe'
    $savedBuilt = "$built.fixture"
    Move-Item -LiteralPath $built -Destination $savedBuilt
    try {
        Assert-Rejected { & $packageInstaller -UseBuiltLauncher } 'Missing packaged binary'
        Assert-True ((Get-FileHash -LiteralPath $launcher).Hash -eq $originalHash) 'Failed staging replaced launcher'
    } finally {
        Move-Item -LiteralPath $savedBuilt -Destination $built
    }
    # Simulate the old profile layout, including unrelated widget data.
    [IO.Directory]::CreateDirectory($legacyRoot, (Get-Acl -LiteralPath $root)) | Out-Null
    New-Item -ItemType Directory -Path $legacy, $fixtureWidgets, $fixtureConfig | Out-Null
    Copy-Item -LiteralPath $launcher, $installedScript -Destination $legacy
    Set-Content -LiteralPath (Join-Path $fixtureWidgets 'keep.txt') -Value 'widget data'
    Set-Content -LiteralPath (Join-Path $fixtureConfig 'keep.txt') -Value 'other component'
    & $packageInstaller -UseBuiltLauncher
    Assert-True (Test-Path -LiteralPath $legacy) 'Legacy was deleted before transport switch'
    Assert-True ((Get-FileHash -LiteralPath $launcher).Hash -eq (Get-FileHash -LiteralPath $built).Hash) 'Packaged launcher not installed'
    & $installer -Mode CleanupLegacy
    Assert-True (-not (Test-Path -LiteralPath $legacy)) 'Legacy component not cleaned up'
    Assert-True (Test-Path -LiteralPath (Join-Path $fixtureWidgets 'keep.txt')) 'Widget data lost'
    Assert-True (Test-Path -LiteralPath $registration) 'Cleanup removed registration'
    # Win32 wildcard matching for "name.*" may include "name" with no
    # extension. Check exact allowed children, including unexpected files,
    # and report actual leftovers rather than relying on provider filtering.
    $leftovers = @(Get-ChildItem -LiteralPath $root -Force | Where-Object {
        $_.Name -notin @('LocalNotifications', 'Config')
    })
    if ($leftovers.Count -ne 0) {
        throw ('Abandoned staging or backup entries: ' + (($leftovers | Select-Object -ExpandProperty Name) -join ', '))
    }
    & $installer -Mode Uninstall
    & $installer -Mode Uninstall
    Assert-True (-not (Test-Path -LiteralPath $component)) 'Component not removed'
    Assert-True (-not (Test-Path -LiteralPath $registration)) 'Registration not removed'
    Assert-True (-not (Test-Path -LiteralPath $settings)) 'Notification settings not removed'
    Assert-True (Test-Path -LiteralPath (Join-Path $fixtureConfig 'keep.txt')) 'Unrelated component lost'
    Assert-True (Test-Path -LiteralPath (Join-Path $fixtureWidgets 'keep.txt')) 'Widget data lost during uninstall'
    Write-Output 'Windows install, update, safe failed staging, legacy cleanup and uninstall: OK.'
} finally {
    if (Test-Path -LiteralPath $unexpected) { Remove-Item -LiteralPath $unexpected }
    & $installer -Mode Uninstall
    foreach ($fixture in @($fixtureConfig, $fixtureWidgets)) {
        if (Test-Path -LiteralPath (Join-Path $fixture 'keep.txt')) {
            Remove-Item -LiteralPath (Join-Path $fixture 'keep.txt')
        }
        if ((Test-Path -LiteralPath $fixture) -and @(Get-ChildItem -LiteralPath $fixture -Force).Count -eq 0) {
            Remove-Item -LiteralPath $fixture
        }
    }
    foreach ($folder in @($root, $legacyRoot)) {
        if ((Test-Path -LiteralPath $folder) -and @(Get-ChildItem -LiteralPath $folder -Force).Count -eq 0) {
            Remove-Item -LiteralPath $folder
        }
    }
}
