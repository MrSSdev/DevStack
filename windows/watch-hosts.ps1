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
$backup = 'C:\DevStack\Hosts\hosts-before-last-sync.bak'

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

function Test-DevStackUbuntuRunning {
    # --list --running --quiet is read-only: unlike -d, it never starts Ubuntu.
    $running = @(& $wsl --list --running --quiet 2>$null)
    if ($LASTEXITCODE -ne 0) {
        Write-Warning 'Cannot read running WSL distributions. Remaining idle.'
        return $false
    }
    foreach ($entry in $running) {
        # Some wsl.exe versions emit UTF-16 output with embedded NULs
        # when Windows PowerShell captures stdout.
        $name = ([string]$entry) -replace '[\x00\uFEFF]', ''
        if ($name.Trim() -ceq 'Ubuntu-26.04') {
            return $true
        }
    }
    return $false
}

if ($Once) {
    # On-demand task must not wake Ubuntu after a manual shutdown.
    if (-not (Test-DevStackUbuntuRunning)) {
        Write-Output 'DevStack hosts: Ubuntu is not running; no sync needed.'
        exit 0
    }
    Invoke-DevStackHostsSync
    exit 0
}

# B1: The Windows task is allowed to start at user logon, but must NEVER
# launch the distro at logon or after the user manually terminates it.
# An idle listener waits for Win32_ProcessStartTrace rather than polling WSL.
# This Windows PowerShell process uses memory, but makes no periodic WSL calls.
$sourceId = 'DevStack-WslStart-' + $PID
$query = "SELECT * FROM Win32_ProcessStartTrace WHERE ProcessName = 'wsl.exe'"
$firstCycle = $true

while ($true) {
    # Subscribe BEFORE checking current state, so a real start cannot be
    # missed between the one-time check and WMI registration.
    Register-WmiEvent -Query $query -SourceIdentifier $sourceId | Out-Null
    try {
        $shouldAttach = $firstCycle -and (Test-DevStackUbuntuRunning)
        $firstCycle = $false

        while (-not $shouldAttach) {
            $evt = Wait-Event -SourceIdentifier $sourceId
            if ($null -eq $evt) { continue }

            $parentPid = [int]$evt.SourceEventArgs.NewEvent.ParentProcessID
            Remove-Event -EventIdentifier $evt.EventIdentifier -ErrorAction SilentlyContinue

            # Status checks and the inotify stream also launch wsl.exe.
            # Ignore this listener's own subprocesses: no feedback loop.
            if ($parentPid -eq $PID) { continue }

            # A wsl.exe process can start WITHOUT starting Ubuntu
            # (e.g. 'wsl --list'). Only attach after the specific distro
            # is confirmed running. Bound retries to the startup window.
            for ($attempt = 0; $attempt -lt 20; $attempt++) {
                if (Test-DevStackUbuntuRunning) {
                    $shouldAttach = $true
                    break
                }
                Start-Sleep -Milliseconds 750
            }
        }
    }
    finally {
        # Do not queue external process events while actively watching Linux.
        # After disconnect, re-subscribe and wait for ANOTHER external start,
        # rather than re-launching Ubuntu in an automatic retry loop.
        Unregister-Event -SourceIdentifier $sourceId -ErrorAction SilentlyContinue
        Get-Event -SourceIdentifier $sourceId -ErrorAction SilentlyContinue |
            ForEach-Object {
                Remove-Event -EventIdentifier $_.EventIdentifier -ErrorAction SilentlyContinue
            }
    }

    # One last read-only guard immediately before the potentially starting
    # 'wsl -d' command. The remaining check/use race cannot be eliminated
    # with the public wsl.exe CLI and needs a live shutdown test on ASUS.
    if (-not (Test-DevStackUbuntuRunning)) { continue }

    try {
        Write-Output 'DevStack hosts: connected to already-running Ubuntu-26.04.'
        & $wsl -d Ubuntu-26.04 -u root -- python3 /opt/devstack/scripts/watch-domains.py 2>&1 |
            ForEach-Object {
                if ([string]$_ -eq 'SYNC') {
                    try {
                        Invoke-DevStackHostsSync
                    }
                    catch {
                        Write-Warning ('DevStack hosts synchronization failed: ' + $_.Exception.Message)
                    }
                }
            }
        if ($LASTEXITCODE -ne 0) {
            Write-Warning ('WSL domain watcher exited (code ' + $LASTEXITCODE + '). Waiting for a user-initiated WSL start.')
        }
    }
    catch {
        Write-Warning ('WSL domain watcher disconnected: ' + $_.Exception.Message)
    }
    # NO unconditional re-connect: if Ubuntu was manually terminated,
    # returning to Wait-Event must never wake it again.
}
