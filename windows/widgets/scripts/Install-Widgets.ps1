# Run from the extracted artifact as the Windows account that owns WSL.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (!$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Open PowerShell as administrator using your usual Windows account.'
}
$msix = Join-Path $PSScriptRoot 'DevStack-Widgets-x64.msix'
$cer = Join-Path $PSScriptRoot 'DevStack-Widgets-Dev.cer'
foreach ($file in @($msix, $cer)) {
    if (!(Test-Path -LiteralPath $file)) { throw "Missing artifact file: $file" }
}
# Check the package before replacing the working installation.
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::OpenRead($msix)
try {
    $entry = $zip.GetEntry('AppxManifest.xml')
    if (!$entry) { throw 'MSIX manifest missing.' }
    $reader = [IO.StreamReader]::new($entry.Open())
    try { [xml]$manifest = $reader.ReadToEnd() } finally { $reader.Dispose() }
} finally { $zip.Dispose() }
if ($manifest.Package.Identity.Name -ne 'MrSSdev.DevStack.Widgets' -or
    $manifest.Package.Identity.Publisher -ne 'CN=DevStack Widgets Development' -or
    $manifest.Package.Identity.Version -ne '0.3.0.0') { throw 'Expected DevStack Widgets 0.3.0.0.' }
$cert = Get-PfxCertificate -FilePath $cer
if ($cert.Subject -ne 'CN=DevStack Widgets Development') { throw 'Unexpected signer.' }
$before = Get-AuthenticodeSignature -FilePath $msix
if (!$before.SignerCertificate -or $before.SignerCertificate.Thumbprint -ne $cert.Thumbprint) {
    throw 'The MSIX and CER are not the matching signer pair.'
}
$cert | Format-List Subject, Thumbprint, NotAfter
Import-Certificate -FilePath $cer -CertStoreLocation Cert:\LocalMachine\TrustedPeople | Out-Null
$signature = Get-AuthenticodeSignature -FilePath $msix
if ($signature.Status -ne 'Valid') { throw "Package signature failed: $($signature.Status)" }
Get-Process WidgetBoard, 'DevStack.Widgets' -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue
# A higher package version can normally update in place without losing pins.
# Do not uninstall first: on failure the existing package remains installed.
Add-AppxPackage -Path $msix -ErrorAction Stop
Get-AppxPackage -Name 'MrSSdev.DevStack.Widgets' | Select-Object Name, Version, Status
Write-Host 'WSL widget updated. DevStack services layout is unchanged.'
Write-Host 'For real resource readings, start Ubuntu deliberately and run Install-WslTelemetry.ps1 once.'
