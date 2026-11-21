# Privileged, event-driven Windows hosts synchronization for DevStack.
# Installed into an Administrators/SYSTEM-writable-only directory by install-hosts-sync.ps1.
# Does not install or change certificate authorities.
param([switch]$Once)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'hosts-lib.ps1')

$admin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'DevStack hosts watcher must run elevated.' }

$wsl = Join-Path $env:WINDIR 'System32\wsl.exe'
$hosts = Join-Path $env:WINDIR 'System32\drivers\etc\hosts'
$backup = Join-Path $env:ProgramData 'DevStack\hosts-before-last-sync.bak'

function Invoke-DevStackHostsSync {
    $rawDomains = @(& $wsl -d Ubuntu-26.04 -u root -- python3 /opt/devstack/scripts/list-domains.py)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read WSL DevStack domains.' }
    $domains = @($rawDomains | ForEach-Object { [string]$_ } | Where-Object { $_.Trim() -ne '' } | ForEach-Object { $_.Trim() })
    if ($domains.Count -eq 0) { throw 'Empty domain list.' }
    $original = [System.IO.File]::ReadAllText($hosts)
    $next = Get-DevStackManagedHostsContent -Original $original -Domains $domains
    if ($next -cne $original) {
        # Back up before modifying a Windows system file. Never touch unrelated entries.
        [System.IO.File]::WriteAllText($backup, $original, ([System.Text.UTF8Encoding]::new($false)))
        [System.IO.File]::WriteAllText($hosts, $next, ([System.Text.UTF8Encoding]::new($false)))
        Write-Output ('DevStack hosts updated: ' + ($domains -join ', '))
    }
}

if ($Once) {
    Invoke-DevStackHostsSync
    exit 0
}

# The WSL Python helper blocks in Linux inotify, emitting only 'SYNC' when
# projects.json is atomically replaced. There is NO periodic polling.
# On WSL shutdown or watcher errors, reconnect with a short bounded retry.
while ($true) {
    try {
        & $wsl -d Ubuntu-26.04 -u root -- python3 /opt/devstack/scripts/watch-domains.py 2>&1 |
            ForEach-Object {
                if ([string]$_ -eq 'SYNC') {
                    try {
                        Invoke-DevStackHostsSync
                    } catch {
                        Write-Warning ('DevStack hosts synchronization failed: ' + $_.Exception.Message)
                    }
                }
            }
        Write-Warning ('DevStack domain watcher disconnected (code ' + $LASTEXITCODE + '), reconnecting.')
    } catch {
        Write-Warning ('DevStack domain watcher failed: ' + $_.Exception.Message)
    }
    Start-Sleep -Seconds 15
}
