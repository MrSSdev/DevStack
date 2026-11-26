# Disposable CI: real ACLs, no Linux commands or actual credentials.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true') { throw 'Disposable runner required.' }
. (Join-Path $PSScriptRoot 'widgets\scripts\Widget-Layout.ps1')
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$root = 'C:\DevStack'
$legacyParent = Join-Path $env:RUNNER_TEMP ('widget-layout-' + [Guid]::NewGuid().ToString('N'))
$legacy = Join-Path $legacyParent 'Widgets'
function Assert-True([bool]$Condition,[string]$Label) { if (-not $Condition) { throw $Label } }
function Assert-Rejected([scriptblock]$Action,[string]$Label) {
    $rejected = $false
    try { & $Action | Out-Null } catch { $rejected = $true }
    if (-not $rejected) { throw "Unexpected success: $Label" }
}
Assert-True ($null -eq (Get-DevStackLayoutItem $root)) 'Existing non-fixture root'
try {
    Initialize-DevStackLayout -UserSid $sid -Component Widgets
    New-Item -ItemType Directory -Path $legacyParent | Out-Null
    [IO.Directory]::CreateDirectory($legacy,(New-DevStackLayoutAcl -Kind Widgets -UserSid $sid)) | Out-Null
    $names = @('services.json','telemetry.json','wsl-view-cache.json','wsl-bridge.log','wsl-bridge.log.previous')
    $hashes = @{}
    foreach ($name in $names) {
        $path = Join-Path $legacy $name
        Set-Content -LiteralPath $path -Value ('synthetic fixture ' + $name) -Encoding Ascii
        if ($name -in @('services.json','telemetry.json')) { Set-Acl -LiteralPath $path -AclObject (New-DevStackWidgetFileAcl $sid) }
        $hashes[$name] = (Get-FileHash -LiteralPath $path).Hash
    }
    $foreign = Join-Path $legacy 'unknown.txt'
    Set-Content -LiteralPath $foreign -Value 'preserve'
    try { Assert-Rejected { Copy-DevStackWidgetState $legacy } 'Unknown data' }
    finally { Remove-Item -LiteralPath $foreign }
    Assert-True (@(Get-ChildItem -LiteralPath 'C:\DevStack\Widgets' -Force).Count -eq 0) 'Unknown data caused partial copy'
    Copy-DevStackWidgetState $legacy
    Copy-DevStackWidgetState $legacy
    foreach ($name in $names) {
        $path = Join-Path 'C:\DevStack\Widgets' $name
        Assert-DevStackWidgetPrivateItem $path $false $sid
        Assert-True ((Get-FileHash -LiteralPath $path).Hash -ceq $hashes[$name]) "Copy mismatch: $name"
        Assert-True ((Get-FileHash -LiteralPath (Join-Path $legacy $name)).Hash -ceq $hashes[$name]) "Old data changed: $name"
        if ($name -in @('services.json','telemetry.json')) {
            Assert-True ((Get-Acl -LiteralPath $path).AreAccessRulesProtected) 'Credential file ACL not protected'
        }
    }
    $config = 'C:\DevStack\Widgets\services.json'
    Set-Content -LiteralPath $config -Value 'conflict' -Encoding Ascii
    Assert-Rejected { Copy-DevStackWidgetState $legacy } 'Existing different config'
    Assert-True ((Get-Content -LiteralPath $config -Raw).Trim() -eq 'conflict') 'Conflict overwritten'
    Assert-Rejected { & (Join-Path $PSScriptRoot 'widgets\scripts\Migrate-WidgetLayout.ps1') } 'Elevated private-data migration'
    Write-Host 'Widget data migration, ACL, exact copy, retention, conflict refusal and idempotence: OK.'
} finally {
    if (Test-Path -LiteralPath 'C:\DevStack\Widgets') { Remove-Item -LiteralPath 'C:\DevStack\Widgets' -Recurse -Force }
    if (Test-Path -LiteralPath $root) { [IO.Directory]::Delete($root,$false) }
    if (Test-Path -LiteralPath $legacyParent) { Remove-Item -LiteralPath $legacyParent -Recurse -Force }
}
