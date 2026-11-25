$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
[xml]$doc = Get-Content -LiteralPath (Join-Path $root 'Package.appxmanifest') -Raw
$defs = @($doc.SelectNodes("//*[local-name()='Definition']"))
$ids = @($defs | ForEach-Object { $_.GetAttribute('Id') })
if ($ids.Count -ne 2 -or $ids -notcontains 'DevStack_WSL' -or $ids -notcontains 'DevStack_Services') { throw 'Need the two original definition IDs' }
foreach ($def in $defs) {
    $caps = @($def.SelectNodes("./*[local-name()='Capabilities']/*[local-name()='Capability']"))
    if ($caps.Count -ne 1) { throw 'Each widget must have exactly one capability' }
    $sizes = @($caps[0].SelectNodes("./*[local-name()='Size']"))
    if ($sizes.Count -ne 1 -or $sizes[0].GetAttribute('Name') -ne 'large') { throw 'Capability must contain exactly one Size=large' }
}
$clsid = $doc.SelectSingleNode("//*[local-name()='Class']").GetAttribute('Id')
$activation = $doc.SelectSingleNode("//*[local-name()='CreateInstance']").GetAttribute('ClassId')
if ($clsid -ne $activation -or $clsid -ne '6DE550FB-ECB8-4955-87E2-4943E247E34A') { throw 'CLSID mismatch' }
if ($doc.Package.Identity.Publisher -ne 'CN=DevStack Widgets Development') { throw 'Wrong signing publisher' }
foreach ($icon in @('WSL','DevStack','Nginx','PHP','MariaDB','Redis')) {
    if (-not (Test-Path (Join-Path $root "ProviderAssets/UI_$icon.png"))) { throw "Missing UI glyph $icon" }
}
foreach ($name in @('WSL', 'DevStack')) {
    $card = Get-Content -LiteralPath (Join-Path $root "Templates/$name.json") -Raw | ConvertFrom-Json
    if ($card.type -ne 'AdaptiveCard' -or $card.version -ne '1.5') { throw "Invalid legacy template $name" }
}
Write-Host 'PASS: two definitions, one large size per Capability, original COM identity, six glyphs'

# Verify the real installer is syntactically valid and keeps both reset points.
# Execute only its deployment tail with mocked commands: never deploy/stop
# real processes on the CI runner.
$installer = Join-Path $PSScriptRoot 'Install-Widgets.ps1'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($installer, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -gt 0) { throw 'Widget installer syntax failed' }
$commands = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.CommandAst]
}, $true))
$deploy = @($commands | Where-Object { $_.GetCommandName() -eq 'Add-AppxPackage' })
$resets = @($commands | Where-Object { $_.GetCommandName() -eq 'Stop-Process' })
if ($deploy.Count -ne 1 -or $resets.Count -ne 2 -or
    $resets[0].Extent.StartOffset -ge $deploy[0].Extent.StartOffset -or
    $resets[1].Extent.StartOffset -le $deploy[0].Extent.EndOffset) {
    throw 'Expected a reset before deployment and another after deployment'
}
$tail = [scriptblock]::Create((Get-Content -LiteralPath $installer -Raw).Substring($deploy[0].Extent.StartOffset))
foreach ($scenario in @('ok','deployment-error','wrong-version','bad-status','missing-package')) {
    & {
        $calls = [System.Collections.Generic.List[string]]::new()
        $msix = 'mock.msix'
        $manifest = $doc
        function Add-AppxPackage {
            [CmdletBinding()]
            param([string]$Path)
            $calls.Add('deploy')
            if ($scenario -eq 'deployment-error') { throw 'Mock deployment failure' }
        }
        function Get-AppxPackage {
            [CmdletBinding()]
            param([string]$Name)
            $calls.Add('verify')
            if ($scenario -eq 'missing-package') { return }
            $version = if ($scenario -eq 'wrong-version') { '0.0.0.0' } else { [string]$doc.Package.Identity.Version }
            $status = if ($scenario -eq 'bad-status') { 'Error' } else { 'Ok' }
            [pscustomobject]@{ Name=$Name; Version=$version; Status=$status }
        }
        function Get-Process {
            [CmdletBinding()]
            param([string[]]$Name)
            if (($Name -join ',') -ne 'WidgetBoard,DevStack.Widgets') { throw 'Unexpected reset target' }
            $calls.Add('get-process')
            [pscustomobject]@{ Name='WidgetBoard' }
        }
        function Stop-Process {
            [CmdletBinding()]
            param([Parameter(ValueFromPipeline=$true)]$InputObject, [switch]$Force)
            process {
                if (!$Force) { throw 'Expected forced widget process reset' }
                $calls.Add('reset')
            }
        }
        $failed = $false
        try { & $tail | Out-Null } catch { $failed = $true }
        $expected = switch ($scenario) {
            'ok' { 'deploy,verify,get-process,reset' }
            'deployment-error' { 'deploy' }
            default { 'deploy,verify' }
        }
        if (($calls -join ',') -ne $expected -or $failed -ne ($scenario -ne 'ok')) {
            throw "Installer post-update reset test failed: $scenario ($($calls -join ','))"
        }
    }
}
Write-Host 'PASS: post-update reset after verified installation only (mocked commands)'
