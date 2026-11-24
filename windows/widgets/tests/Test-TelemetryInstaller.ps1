# Real Windows PowerShell execution, with a byte-checking fake wsl.exe.
# Never invokes a Linux distro, changes systemd or touches the real user config.
# Run the harness with Windows PowerShell 5.1 so Add-Type emits a Framework EXE.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$temp = Join-Path ([IO.Path]::GetTempPath()) ('DevStack installer test ' + [Guid]::NewGuid().ToString('N'))
$oldPath = $env:PATH
$oldLocal = $env:LOCALAPPDATA
$utf8 = [Text.UTF8Encoding]::new($false)
function Assert([bool]$Condition, [string]$Message) {
    if (!$Condition) { throw "FAIL: $Message" }
    Write-Host "PASS: $Message"
}
function Run-Script([string]$Engine, [string]$File, [string]$Extra = '') {
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $Engine
    $info.Arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $File + '" ' + $Extra
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $p = [Diagnostics.Process]::Start($info)
    try {
        $out = $p.StandardOutput.ReadToEndAsync()
        $err = $p.StandardError.ReadToEndAsync()
        if (!$p.WaitForExit(30000)) {
            try { $p.Kill() } catch { }
            throw 'Test process timed out.'
        }
        return @{ Code=$p.ExitCode; Text=$out.GetAwaiter().GetResult() + $err.GetAwaiter().GetResult() }
    } finally { $p.Dispose() }
}
function Token-Hash([string]$Token) {
    $hash = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::ASCII.GetBytes($Token))) }
    finally { $hash.Dispose() }
}
try {
    New-Item -ItemType Directory -Path $temp | Out-Null
    $fakeDir = Join-Path $temp 'fake-bin'
    $bundle = Join-Path $temp 'bundle with spaces'
    New-Item -ItemType Directory -Path $fakeDir,$bundle | Out-Null
    $source = @'
using System;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using System.Security.Cryptography;
public static class FakeWsl {
    public static int Main(string[] args) {
        Console.OutputEncoding = new UTF8Encoding(false);
        string joined = string.Join(" ", args);
        if (joined == "--list --running --quiet") {
            if (Environment.GetEnvironmentVariable("DS_TEST_STOPPED") != "1")
                Console.WriteLine("Ubuntu-26.04");
            return 0;
        }
        if (joined != "-d Ubuntu-26.04 -u root -- /bin/sh -s") return 90;
        byte[] bytes;
        using (var stream = new MemoryStream()) {
            Console.OpenStandardInput().CopyTo(stream);
            bytes = stream.ToArray();
        }
        // The old installer normalized its string, then the Windows pipeline
        // added CRLF back. Catch even a single trailing CR byte.
        if (Array.IndexOf(bytes, (byte)13) >= 0) return 40;
        if (bytes.Length >= 3 && bytes[0] == 239 && bytes[1] == 187 && bytes[2] == 191) return 41;
        if (bytes.Length == 0 || bytes[bytes.Length - 1] != 10) return 42;
        string script = new UTF8Encoding(false, true).GetString(bytes);
        File.WriteAllText(Environment.GetEnvironmentVariable("DS_TEST_CALLED"), "yes");
        if (Environment.GetEnvironmentVariable("DS_TEST_FAIL") == "1") {
            Console.Error.WriteLine("Simulated Linux socket activation failure.");
            return 23;
        }
        if (script.Contains("disable --now devstack-widget-metrics.socket")) return 0;
        var secret = Regex.Match(script, @"'([0-9a-f]{64})' > /etc/devstack-widgets/auth\.token");
        if (!secret.Success) return 43;
        // Only persist a test-token hash, never log the credential itself.
        using (var hash = SHA256.Create()) {
            File.WriteAllText(Environment.GetEnvironmentVariable("DS_TEST_TOKEN_HASH"),
                BitConverter.ToString(hash.ComputeHash(Encoding.ASCII.GetBytes(secret.Groups[1].Value))));
        }
        Console.WriteLine("Linux metrics socket: active");
        return 0;
    }
}
'@
    Add-Type -TypeDefinition $source -Language CSharp -OutputAssembly (Join-Path $fakeDir 'wsl.exe') -OutputType ConsoleApplication
    $env:PATH = $fakeDir + ';' + $oldPath
    Assert ((Get-Command wsl.exe -CommandType Application).Source -eq (Join-Path $fakeDir 'wsl.exe')) 'Only fake WSL can execute'
    $installer = Join-Path $bundle 'Install-WslTelemetry.ps1'
    Copy-Item (Join-Path $root 'scripts\Install-WslTelemetry.ps1') $installer
    Copy-Item (Join-Path $root 'telemetry') (Join-Path $bundle 'telemetry') -Recurse
    $repro = Join-Path $temp 'old-pipeline.ps1'
    [IO.File]::WriteAllText($repro, "'systemctl is-active --quiet devstack-widget-metrics.socket' | wsl.exe -d Ubuntu-26.04 -u root -- /bin/sh -s`nexit `$LASTEXITCODE`n", $utf8)
    $engines = @((Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'))
    $modern = Get-Command pwsh.exe -ErrorAction SilentlyContinue
    if ($modern) { $engines += $modern.Source }
    foreach ($engine in $engines) {
        $case = Join-Path $temp ([Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $case | Out-Null
        $env:LOCALAPPDATA = $case
        $env:DS_TEST_TOKEN_HASH = Join-Path $case 'token.hash'
        $env:DS_TEST_CALLED = Join-Path $case 'called.marker'
        $env:DS_TEST_FAIL = '0'; $env:DS_TEST_STOPPED = '0'
        $config = Join-Path $case 'DevStack\Widgets\telemetry.json'
        Write-Host "Testing $engine"
        $result = Run-Script $engine $repro
        Assert ($result.Code -eq 40) 'Original pipeline reproduces trailing CRLF corruption'
        $result = Run-Script $engine $installer
        if ($result.Code -ne 0) { Write-Host $result.Text }
        Assert ($result.Code -eq 0) 'Full fixed installer accepts exact LF stdin'
        $saved = Get-Content -LiteralPath $config -Raw | ConvertFrom-Json
        Assert ($saved.schema -eq 1 -and $saved.distro -eq 'Ubuntu-26.04' -and $saved.port -eq 43871) 'Expected Windows settings written'
        Assert ($saved.token -match '^[0-9a-f]{64}$') 'Valid credential format'
        Assert ((Token-Hash $saved.token) -eq [IO.File]::ReadAllText($env:DS_TEST_TOKEN_HASH)) 'Linux stdin and Windows credential agree'
        Assert (!$result.Text.Contains($saved.token)) 'Credential not printed'
        Assert ((Get-Acl -LiteralPath $config).AreAccessRulesProtected) 'Credential ACL inheritance disabled'
        $result = Run-Script $engine $installer
        Assert ($result.Code -eq 0) 'Retry after existing installation succeeds'
        $saved = Get-Content -LiteralPath $config -Raw | ConvertFrom-Json
        Assert ((Token-Hash $saved.token) -eq [IO.File]::ReadAllText($env:DS_TEST_TOKEN_HASH)) 'Retry credentials remain synchronized'
        $before = [IO.File]::ReadAllText($config)
        $env:DS_TEST_FAIL = '1'
        $result = Run-Script $engine $installer
        Assert ($result.Code -ne 0 -and $result.Text.Contains('exit 23') -and $result.Text.Contains('Simulated Linux socket activation failure')) 'Linux failure exit code and diagnostics preserved'
        Assert ([IO.File]::ReadAllText($config) -eq $before) 'Linux failure does not overwrite Windows config'
        $result = Run-Script $engine $installer '-Remove'
        Assert ($result.Code -ne 0 -and (Test-Path -LiteralPath $config)) 'Failed removal preserves Windows config'
        $env:DS_TEST_FAIL = '0'
        $env:DS_TEST_STOPPED = '1'
        Remove-Item $env:DS_TEST_CALLED -ErrorAction SilentlyContinue
        $result = Run-Script $engine $installer
        Assert ($result.Code -ne 0 -and !(Test-Path $env:DS_TEST_CALLED)) 'Stopped distro is never started by installer preflight'
        $env:DS_TEST_STOPPED = '0'
        $result = Run-Script $engine $installer '-Remove'
        Assert ($result.Code -eq 0 -and !(Test-Path -LiteralPath $config)) 'Removal works with exact LF stdin'
    }
    Write-Host 'PASS: real Windows PowerShell engines; fake WSL only. Physical Ubuntu socket health remains a device test.'
} finally {
    $env:PATH = $oldPath
    $env:LOCALAPPDATA = $oldLocal
    foreach ($name in @('DS_TEST_FAIL','DS_TEST_STOPPED','DS_TEST_TOKEN_HASH','DS_TEST_CALLED')) {
        Remove-Item "Env:$name" -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
}
