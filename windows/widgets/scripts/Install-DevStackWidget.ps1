# One-time, deliberate setup. Does not modify the existing WSL telemetry collector.
[CmdletBinding()]
param([switch]$SkipFrontend = $true)
$ErrorActionPreference = 'Stop'
$Distro = 'Ubuntu-26.04'
. (Join-Path $PSScriptRoot 'Widget-Layout.ps1')
$ConfigDir = Get-DevStackWidgetDirectory
$ConfigPath = Join-Path $ConfigDir 'services.json'
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
if ($null -ne (Get-DevStackLayoutItem $ConfigPath)) { Assert-DevStackWidgetPrivateItem $ConfigPath $false $sid }
$running = (& wsl.exe --list --running --quiet | Out-String).Replace([string][char]0, '')
if ($LASTEXITCODE -ne 0 -or ($running -split '\r?\n' | ForEach-Object { $_.Trim() }) -notcontains $Distro) {
    throw 'Ubuntu must already be running. Start it deliberately with the WSL widget, then retry.'
}
function New-Token {
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
}
$readToken = New-Token
$controlToken = New-Token
if (Test-Path -LiteralPath $ConfigPath) {
    $previous = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
    if ($previous.schema -ne 1 -or $previous.distro -ne $Distro -or
        $previous.readToken -cnotmatch '^[a-f0-9]{64}$' -or $previous.controlToken -cnotmatch '^[a-f0-9]{64}$') {
        throw 'Existing services.json is invalid; no Linux settings have been changed.'
    }
    $readToken = $previous.readToken
    $controlToken = $previous.controlToken
}
$bundle = Join-Path $PSScriptRoot 'services'
$files = @('bridge.py','setup.py','devstack-widget-services.socket','devstack-widget-services@.service','devstack-widget-control.socket','devstack-widget-control@.service')
$encoded = @{}
foreach ($name in $files) {
    $path = Join-Path $bundle $name
    if (!(Test-Path -LiteralPath $path)) { throw "Missing bundled file: $name" }
    $encoded[$name] = [Convert]::ToBase64String([IO.File]::ReadAllBytes($path))
}
$frontend = Join-Path $PSScriptRoot 'DevStack-Frontend.zip'
if (!$SkipFrontend) {
    if (!(Test-Path -LiteralPath $frontend)) { throw 'Missing frontend bundle for exact project-page links.' }
    $encoded['frontend.zip'] = [Convert]::ToBase64String([IO.File]::ReadAllBytes($frontend))
}
$payloadJson = @{ files=$encoded; readToken=$readToken; controlToken=$controlToken; frontend=(!$SkipFrontend) } | ConvertTo-Json -Compress
$payload64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($payloadJson))
$setup64 = $encoded['setup.py']
# Secrets travel through exact UTF-8 stdin, never process arguments or printed diagnostics.
$script = @"
set -eu
command -v python3 >/dev/null
test -d /run/systemd/system
work=`$(mktemp -d)
trap 'rm -rf "`$work"' EXIT
printf '%s' '$setup64' | base64 -d > "`$work/setup.py"
printf '%s' '$payload64' | base64 -d | python3 -I "`$work/setup.py"
"@
# Do not pipe a PowerShell string into sh. On Windows the native pipeline
# appends CRLF AFTER normalization, corrupting the final shell token.
# Send exact UTF-8 bytes to stdin; BaseStream also works in PowerShell 5.1.
$start = [System.Diagnostics.ProcessStartInfo]::new()
$start.FileName = (Get-Command wsl.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$start.Arguments = '-d Ubuntu-26.04 -u root -- /bin/sh -s'
$start.UseShellExecute = $false
$start.CreateNoWindow = $true
$start.RedirectStandardInput = $true
$start.RedirectStandardOutput = $true
$start.RedirectStandardError = $true
$utf8 = [System.Text.UTF8Encoding]::new($false)
$start.StandardOutputEncoding = $utf8
$start.StandardErrorEncoding = $utf8
$linuxScript = (($script -replace "`r`n", "`n") -replace "`r", "`n").TrimEnd([char]10) + "`n"
$payload = $utf8.GetBytes($linuxScript)
$process = [System.Diagnostics.Process]::new()
$process.StartInfo = $start
try {
    if (!$process.Start()) { throw 'Could not start the WSL setup process.' }
    # Drain both pipes concurrently so diagnostics cannot fill either buffer.
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    $inputStream = $process.StandardInput.BaseStream
    try {
        $inputStream.Write($payload, 0, $payload.Length)
        $inputStream.Flush()
    } finally {
        $inputStream.Close()
    }
    if (!$process.WaitForExit(120000)) {
        try { $process.Kill() } catch { }
        throw 'DevStack integration setup timed out. Some Linux files may already be installed; Windows settings were not changed; keep this bundle and rerun the setup.'
    }
    $output = $stdout.GetAwaiter().GetResult()
    $errors = $stderr.GetAwaiter().GetResult()
    if (![string]::IsNullOrWhiteSpace($output)) { Write-Host $output.TrimEnd() }
    if (![string]::IsNullOrWhiteSpace($errors)) { Write-Host $errors.TrimEnd() }
    if ($process.ExitCode -ne 0) {
        throw ("DevStack integration setup failed (exit {0}). Some Linux files may already be installed; Windows settings were not changed; keep this bundle and rerun the setup. See diagnostics above." -f $process.ExitCode)
    }
} finally {
    $process.Dispose()
}
$config = @{ schema=1; distro=$Distro; readPort=43872; controlPort=43873; readToken=$readToken; controlToken=$controlToken } | ConvertTo-Json -Compress
[IO.File]::WriteAllText($ConfigPath, $config, [System.Text.UTF8Encoding]::new($false))
Set-Acl -LiteralPath $ConfigPath -AclObject (New-DevStackWidgetFileAcl $sid)
Assert-DevStackWidgetPrivateItem $ConfigPath $false $sid

Write-Host 'DevStack integration installed. WSL telemetry and Ubuntu lifecycle settings were not changed.'
Write-Host 'Restart remains restricted to registered DevStack services, after widget confirmation.'
Write-Host 'Only static dashboard files were updated for project deep links (unless -SkipFrontend was supplied).'
