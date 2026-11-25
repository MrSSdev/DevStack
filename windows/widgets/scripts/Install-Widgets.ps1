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
    $manifest.Package.Identity.Version -ne '0.4.2.0') { throw 'Expected DevStack Widgets 0.4.2.0.' }
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
# Update in place. Do not remove the working version before deployment succeeds.
Add-AppxPackage -Path $msix -ErrorAction Stop
$packages = @(Get-AppxPackage -Name 'MrSSdev.DevStack.Widgets' -ErrorAction Stop)
if ($packages.Count -ne 1 -or
    [string]$packages[0].Version -ne [string]$manifest.Package.Identity.Version -or
    [string]$packages[0].Status -ne 'Ok') {
    throw 'Deployment verification failed. Expected the installed package version and Status Ok.'
}

# Always clear the host/provider session after successful deployment.
# A process may have restarted while Add-AppxPackage was running.
# Never terminate Ubuntu or restart the Linux integration.
$resetErrors = @()
Get-Process -Name "WidgetBoard", "DevStack.Widgets" `
    -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue -ErrorVariable resetErrors
if ($resetErrors.Count -gt 0) {
    Write-Warning 'Package updated, but the post-update widget process reset reported an error.'
} else {
    Write-Host 'Post-update widget process reset completed. Reopen Win+W.'
}

$packages | Select-Object Name, Version, Status
Write-Host 'Widgets updated to 0.4.2.0. Approved WSL appearance and controls are preserved.'
Write-Host 'Existing Ubuntu telemetry setup is retained. Do not reinstall WSL telemetry.'
Write-Host 'Existing DevStack integration is retained. Run Install-DevStackWidget.ps1 only if not already configured.'
