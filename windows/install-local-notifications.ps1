param(
    [ValidateSet('Install', 'Demo', 'Uninstall')]
    [string]$Mode = 'Install',
    [switch]$UseBuiltLauncher
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or
    [Diagnostics.Process]::GetCurrentProcess().SessionId -eq 0) {
    throw 'Run in the signed-in Windows user session.'
}
$parent = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'DevStack'
$directory = Join-Path $parent 'LocalNotifications'
$launcher = Join-Path $directory 'DevStack.Notifications.exe'
$installedScript = Join-Path $directory 'show-local-notification.ps1'
$source = Join-Path $PSScriptRoot '..\scripts\windows-local-notification.ps1'

if ($Mode -eq 'Uninstall') {
    & $source -Mode Unregister
    foreach ($file in @($launcher, $installedScript)) {
        if (Test-Path -LiteralPath $file -PathType Leaf) {
            Remove-Item -LiteralPath $file
        }
    }
    foreach ($folder in @($directory, $parent)) {
        if ((Test-Path -LiteralPath $folder -PathType Container) -and
            @((Get-ChildItem -LiteralPath $folder -Force)).Count -eq 0) {
            Remove-Item -LiteralPath $folder
        }
    }
    return
}

if ($Mode -eq 'Demo') {
    $event = '{"id":"batch-00000000000000000000000000000000","category":"mail","kind":"info"}'
    $encoded = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($event))
    $process = Start-Process -FilePath $launcher -ArgumentList $encoded -PassThru -Wait
    if ($process.ExitCode -ne 0) {
        throw "The Windows notification test failed (exit $($process.ExitCode))."
    }
    Write-Output 'Windows accepted the DevStack test notification.'
    return
}

New-Item -Path $directory -ItemType Directory -Force | Out-Null
$temporaryLauncher = Join-Path $directory ("build-{0}.exe" -f [Guid]::NewGuid().ToString('N'))
try {
    if ($UseBuiltLauncher) {
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'DevStack.Notifications.exe') -Destination $temporaryLauncher
    } else {
        $compiler = Join-Path ([Runtime.InteropServices.RuntimeEnvironment]::GetRuntimeDirectory()) 'csc.exe'
        if (-not (Test-Path -LiteralPath $compiler -PathType Leaf)) {
            throw 'Use Windows PowerShell 5.1 to build the launcher, or install the GitHub Actions package.'
        }
        & $compiler /nologo /optimize+ /target:winexe "/out:$temporaryLauncher" (Join-Path $PSScriptRoot 'local-notification-launcher.cs')
        if ($LASTEXITCODE -ne 0) {
            throw "The notification launcher failed to build (exit $LASTEXITCODE)."
        }
    }
    Copy-Item -LiteralPath $source -Destination $installedScript -Force
    Move-Item -LiteralPath $temporaryLauncher -Destination $launcher -Force
    & $installedScript -Mode Register
    Write-Output $launcher
} finally {
    if (Test-Path -LiteralPath $temporaryLauncher -PathType Leaf) {
        Remove-Item -LiteralPath $temporaryLauncher
    }
}
