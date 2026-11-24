# Run deliberately, as the Windows account that owns Ubuntu-26.04.
# Installs one loopback-only, authenticated, socket-activated read-only collector.
[CmdletBinding()]
param([switch]$Remove)
$ErrorActionPreference = 'Stop'
$Distro = 'Ubuntu-26.04'
$ConfigDir = Join-Path $env:LOCALAPPDATA 'DevStack\Widgets'
$ConfigPath = Join-Path $ConfigDir 'telemetry.json'
$running = (& wsl.exe --list --running --quiet | Out-String).Replace([string][char]0, '')
if ($LASTEXITCODE -ne 0 -or ($running -split '\r?\n' | ForEach-Object { $_.Trim() }) -notcontains $Distro) {
    throw 'Ubuntu-26.04 must already be running. Start it deliberately, then run this setup again.'
}
if ($Remove) {
    $script = @'
set -eu
systemctl disable --now devstack-widget-metrics.socket || true
rm -f /etc/systemd/system/devstack-widget-metrics.socket /etc/systemd/system/devstack-widget-metrics@.service
rm -f /usr/local/lib/devstack-widgets/collector.py /etc/devstack-widgets/auth.token
systemctl daemon-reload
'@
} else {
    $bundle = Join-Path $PSScriptRoot 'telemetry'
    foreach ($name in @('collector.py','devstack-widget-metrics.socket','devstack-widget-metrics@.service')) {
        if (!(Test-Path -LiteralPath (Join-Path $bundle $name))) { throw "Missing bundled file: $name" }
    }
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $token = ([System.BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
    $collector = [Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $bundle 'collector.py')))
    $socket = [Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $bundle 'devstack-widget-metrics.socket')))
    $service = [Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $bundle 'devstack-widget-metrics@.service')))
    # The secret travels via stdin, not command-line arguments or shell history.
    $script = @"
set -eu
test -d /run/systemd/system || { echo 'systemd must be active' >&2; exit 1; }
command -v python3 >/dev/null
install -d -m 0755 /usr/local/lib/devstack-widgets
install -d -m 0700 /etc/devstack-widgets
printf '%s' '$collector' | base64 -d > /usr/local/lib/devstack-widgets/collector.py
printf '%s' '$socket' | base64 -d > /etc/systemd/system/devstack-widget-metrics.socket
printf '%s' '$service' | base64 -d > /etc/systemd/system/devstack-widget-metrics@.service
umask 077
printf '%s\n' '$token' > /etc/devstack-widgets/auth.token
chmod 0644 /usr/local/lib/devstack-widgets/collector.py /etc/systemd/system/devstack-widget-metrics.socket /etc/systemd/system/devstack-widget-metrics@.service
chmod 0600 /etc/devstack-widgets/auth.token
systemctl daemon-reload
systemctl enable --now devstack-widget-metrics.socket
systemctl is-active --quiet devstack-widget-metrics.socket
"@
}
$previousEncoding = $OutputEncoding
try {
    $OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    ($script -replace "`r`n", "`n") | & wsl.exe -d $Distro -u root -- /bin/sh -s
    if ($LASTEXITCODE -ne 0) { throw 'Linux collector setup failed; Windows settings were not changed.' }
} finally { $OutputEncoding = $previousEncoding }
if ($Remove) {
    Remove-Item -LiteralPath $ConfigPath -Force -ErrorAction SilentlyContinue
    Write-Host 'Read-only metrics removed. WSL controls remain available.'
    return
}
New-Item -ItemType Directory -Path $ConfigDir -Force | Out-Null
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$acl = [System.Security.AccessControl.DirectorySecurity]::new()
$acl.SetAccessRuleProtection($true, $false)
$inherit = [System.Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
$propagate = [System.Security.AccessControl.PropagationFlags]::None
foreach ($principal in @($sid, [System.Security.Principal.SecurityIdentifier]::new('S-1-5-18'))) {
    $rule = [System.Security.AccessControl.FileSystemAccessRule]::new($principal, 'FullControl', $inherit, $propagate, 'Allow')
    $acl.AddAccessRule($rule)
}
Set-Acl -LiteralPath $ConfigDir -AclObject $acl
$config = @{ schema=1; distro=$Distro; port=43871; token=$token } | ConvertTo-Json -Compress
[IO.File]::WriteAllText($ConfigPath, $config, [System.Text.UTF8Encoding]::new($false))
$fileAcl = [System.Security.AccessControl.FileSecurity]::new()
$fileAcl.SetAccessRuleProtection($true, $false)
foreach ($principal in @($sid, [System.Security.Principal.SecurityIdentifier]::new('S-1-5-18'))) {
    $fileAcl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new($principal, 'FullControl', 'Allow'))
}
Set-Acl -LiteralPath $ConfigPath -AclObject $fileAcl
Write-Host 'Read-only Ubuntu metrics installed. Reopen Win+W to read CPU, RAM, disk and uptime.'
Write-Host 'No resident collector loop: systemd starts one short process per authenticated request.'
