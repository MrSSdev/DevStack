# Run as Administrator in Windows PowerShell 5.1+ or PowerShell 7.
# This modifies Windows hosts and trusts the public DevStack local CA.
$ErrorActionPreference = 'Stop'
$admin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'Run PowerShell as Administrator.' }

$all = @(& wsl.exe -d Ubuntu-26.04 -u root -- python3 /opt/devstack/scripts/list-domains.py)
if ($LASTEXITCODE -ne 0) { throw 'Could not read domains from WSL.' }
$domains = @($all | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^[a-z][a-z0-9-]{0,39}\.test$' } | Select-Object -Unique)
if (-not $domains.Contains('devstack.test')) { throw 'DevStack domain is missing.' }

$hosts = Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'
$original = Get-Content -LiteralPath $hosts -Raw
$lines = @()
foreach ($domain in $domains) {
    $escaped = [regex]::Escape($domain)
    $already = $original -match "(?m)^\s*127\.0\.0\.1\s+[^\r\n#]*\b$escaped\b"
    if (-not $already) { $lines += "127.0.0.1 $domain" }
}
if ($lines.Count -gt 0) {
    Copy-Item -LiteralPath $hosts -Destination "$hosts.devstack-backup" -Force
    Add-Content -LiteralPath $hosts -Value ("`r`n# DevStack local domains`r`n" + ($lines -join "`r`n") + "`r`n") -Encoding Ascii
}

$localCert = Join-Path $env:TEMP 'devstack-rootCA.pem'
# Do not pass a Windows path to wslpath through wsl.exe: PowerShell/WSL can
# strip backslashes during native argument conversion. The exported PEM is
# a public certificate, so safely transfer its ASCII text over stdout.
$certLines = @(& wsl.exe -d Ubuntu-26.04 -u root -- cat /var/lib/devstack/ca/rootCA.pem)
if ($LASTEXITCODE -ne 0) { throw 'Could not read the local CA certificate from WSL.' }
if ($certLines.Count -lt 3 -or
    $certLines[0].Trim() -ne '-----BEGIN CERTIFICATE-----' -or
    $certLines[-1].Trim() -ne '-----END CERTIFICATE-----') {
    throw 'WSL returned an invalid PEM certificate. Nothing was imported into Windows.'
}
[System.IO.File]::WriteAllLines($localCert, [string[]]$certLines, [System.Text.Encoding]::ASCII)
Write-Host 'SHA-256 hash of the public CA PEM file:' -ForegroundColor Cyan
& certutil.exe -hashfile $localCert SHA256
if ($LASTEXITCODE -ne 0) { throw 'Could not verify the exported CA file.' }
$confirm = Read-Host 'Trust this NEW LOCAL root CA for Windows? Type YES'
if ($confirm -ne 'YES') { Write-Warning 'Skipped CA trust. Browser will warn until installed.'; exit 0 }
& certutil.exe -addstore -f Root $localCert
if ($LASTEXITCODE -ne 0) { throw 'Failed to trust local CA.' }
Write-Host ('Ready: ' + ($domains -join ', ')) -ForegroundColor Green
