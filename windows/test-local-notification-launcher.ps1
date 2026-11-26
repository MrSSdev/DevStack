param([string]$OutputExecutable)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$runtime = [Runtime.InteropServices.RuntimeEnvironment]::GetRuntimeDirectory()
$compiler = Join-Path $runtime 'csc.exe'
$directory = Join-Path ([IO.Path]::GetTempPath()) ("devstack-notification-{0}" -f [Guid]::NewGuid().ToString('N'))
New-Item -Path $directory -ItemType Directory | Out-Null
try {
    $launcher = Join-Path $directory 'DevStack.Notifications.exe'
    & $compiler /nologo /optimize+ /target:winexe "/out:$launcher" (Join-Path $PSScriptRoot 'local-notification-launcher.cs')
    if ($LASTEXITCODE -ne 0) { throw 'Could not compile the local notification launcher.' }

    $bytes = [IO.File]::ReadAllBytes($launcher)
    $peHeader = [BitConverter]::ToInt32($bytes, 0x3c)
    $subsystem = [BitConverter]::ToUInt16($bytes, ($peHeader + 24 + 68))
    if ($subsystem -ne 2) { throw 'The local notification launcher is not a Windows GUI executable.' }

    # Inspect PowerShell itself on Windows. Looking only at a window's hidden
    # style would miss a console that was allocated before it became hidden.
    @'
$ErrorActionPreference = 'Stop'
Add-Type -Namespace DevStackTest -Name Native -MemberDefinition '[DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow();'
if ([DevStackTest.Native]::GetConsoleWindow() -ne [IntPtr]::Zero) { exit 21 }
$event = ConvertFrom-Json -InputObject ([Console]::ReadLine())
if ($event.id -cne 'mail-AbCdEf0123456789AaBbCc') { exit 22 }
exit 0
'@ | Set-Content -LiteralPath (Join-Path $directory 'show-local-notification.ps1') -Encoding Ascii

    $sample = '{"id":"mail-AbCdEf0123456789AaBbCc","category":"mail","kind":"info"}'
    $encoded = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($sample))
    $process = Start-Process -FilePath $launcher -ArgumentList $encoded -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Expected payload and no console in PowerShell (exit $($process.ExitCode))."
    }
    if ($OutputExecutable) {
        Copy-Item -LiteralPath $launcher -Destination $OutputExecutable
    }
    Write-Output 'GUI launcher and PowerShell child without a console: OK.'
} finally {
    Remove-Item -LiteralPath $directory -Recurse -Force
}
