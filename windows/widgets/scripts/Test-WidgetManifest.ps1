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
