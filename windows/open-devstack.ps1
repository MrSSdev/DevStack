# Open DevStack on demand from Windows 11. No administrator privileges required.
# The script starts Ubuntu only when explicitly invoked by the user.
[CmdletBinding()]
param(
    [switch]$NoBrowser,
    [ValidateRange(10, 300)]
    [int]$TimeoutSeconds = 90
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$distribution = 'Ubuntu-26.04'
$url = 'https://devstack.test/'
$wsl = Join-Path $env:WINDIR 'System32\wsl.exe'

try {
    if (-not (Test-Path -LiteralPath $wsl -PathType Leaf)) {
        throw 'wsl.exe was not found. Check your WSL installation.'
    }

    # One explicit launch works whether Ubuntu is already running or stopped.
    # It does not reboot an already-running distribution or restart services.
    & $wsl -d $distribution -- /usr/bin/true
    if ($LASTEXITCODE -ne 0) {
        throw "Could not start $distribution (wsl.exe exit code $LASTEXITCODE)."
    }

    # WSL may return before systemd services and HTTPS are ready.
    # Use the same trusted HTTPS endpoint as the normal Windows browser.
    # Do not bypass CA validation or modify Windows hosts/certificates here.
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $ready = $false
    do {
        try {
            $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 4 -ErrorAction Stop
            if ($response.StatusCode -eq 200) {
                $ready = $true
                break
            }
        }
        catch {
            # Connection refused or service startup: retry within the deadline.
        }

        if ((Get-Date) -ge $deadline) {
            break
        }
        Start-Sleep -Seconds 1
    } while ($true)

    if (-not $ready) {
        throw "DevStack did not respond with HTTP 200 at $url within $TimeoutSeconds seconds. Check Ubuntu services, Windows hosts and the trusted local CA."
    }

    if (-not $NoBrowser) {
        Start-Process -FilePath $url -ErrorAction Stop
    }

    Write-Output "DevStack ready: $url"
}
catch {
    $message = $_.Exception.Message
    if (-not $NoBrowser) {
        # The Start-menu shortcut hides the PowerShell window. Show failures.
        try {
            Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
            [void][System.Windows.Forms.MessageBox]::Show(
                $message,
                'DevStack',
                [System.Windows.Forms.MessageBoxButtons]::OK,
                [System.Windows.Forms.MessageBoxIcon]::Error
            )
        }
        catch {
            # The original error is still written to stderr below.
        }
    }

    [Console]::Error.WriteLine("DevStack: $message")
    exit 1
}
