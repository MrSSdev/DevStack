$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
[xml] $doc = Get-Content -LiteralPath (Join-Path $root 'Package.appxmanifest') -Raw
$definitions = @($doc.SelectNodes("//*[local-name()='Definition']"))
$ids = @($definitions | ForEach-Object { $_.GetAttribute('Id') })
if ($ids.Count -ne 2 -or $ids -notcontains 'DevStack_WSL' -or $ids -notcontains 'DevStack_Services') {
    throw "Need exactly two distinct widget definitions, found: $($ids -join ',')"
}
$clsid = $doc.SelectSingleNode("//*[local-name()='Class']").GetAttribute('Id')
$activation = $doc.SelectSingleNode("//*[local-name()='CreateInstance']").GetAttribute('ClassId')
if ($clsid -ne $activation -or $clsid -ne '6DE550FB-ECB8-4955-87E2-4943E247E34A') { throw 'CLSID mismatch' }
if ($doc.Package.Identity.Publisher -ne 'CN=DevStack Widgets Development') { throw 'Wrong signing publisher' }
foreach ($name in @('WSL', 'DevStack')) {
    $template = Get-Content -LiteralPath (Join-Path $root "Templates/$name.json") -Raw | ConvertFrom-Json
    if ($template.type -ne 'AdaptiveCard' -or $template.version -ne '1.5') { throw "Invalid Adaptive Card $name" }
}
if (-not (Test-Path (Join-Path $root 'Assets/StoreLogo.png'))) { throw 'Generate artwork first' }
Write-Host 'Two definitions, COM registration, publisher and cards: OK'
