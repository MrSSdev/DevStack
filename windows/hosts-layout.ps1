# Privileged Hosts releases. Use only from a verified installation bundle.
function New-DevStackHostsFileAcl {
    $acl = [Security.AccessControl.FileSecurity]::new()
    $acl.SetAccessRuleProtection($true,$false)
    $admin = [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
    $system = [Security.Principal.SecurityIdentifier]::new('S-1-5-18')
    $acl.SetOwner($admin)
    foreach ($sid in @($admin,$system)) {
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid,'FullControl','Allow'))
    }
    return $acl
}
function Assert-DevStackHostsFile([string]$Path) {
    $item = Get-DevStackLayoutItem $Path
    if ($null -eq $item -or $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Missing or redirected protected file: $Path"
    }
    if ((Get-DevStackLayoutAclSignature (Get-Acl -LiteralPath $Path)) -cne
        (Get-DevStackLayoutAclSignature (New-DevStackHostsFileAcl))) {
        throw "Unexpected protected file ACL: $Path"
    }
}
function Write-DevStackHostsNewFile([string]$Path,[byte[]]$Bytes) {
    $stream = [IO.FileStream]::new($Path,[IO.FileMode]::CreateNew,
        [Security.AccessControl.FileSystemRights]::Write,[IO.FileShare]::None,
        4096,[IO.FileOptions]::WriteThrough,(New-DevStackHostsFileAcl))
    try { $stream.Write($Bytes,0,$Bytes.Length); $stream.Flush($true) }
    finally { $stream.Dispose() }
    Assert-DevStackHostsFile $Path
}
function Get-DevStackHostsReleaseName([hashtable]$Hashes) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [Text.Encoding]::ASCII.GetBytes($Hashes['watch-hosts.ps1'] + ':' + $Hashes['hosts-lib.ps1'])
        return 'release-' + ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-','').ToLowerInvariant()
    } finally { $sha.Dispose() }
}
function Assert-DevStackHostsRelease([string]$Path,[Security.Principal.SecurityIdentifier]$UserSid,[hashtable]$Hashes) {
    Assert-DevStackLayoutDirectory (Get-DevStackLayoutItem $Path) $Path
    if (-not (Test-DevStackLayoutAcl $Path (New-DevStackLayoutAcl -Kind Hosts -UserSid $UserSid))) {
        throw "Unexpected release directory ACL: $Path"
    }
    if (@(Get-ChildItem -LiteralPath $Path -Force).Count -ne 2) { throw "Unexpected release contents: $Path" }
    foreach ($name in @('watch-hosts.ps1','hosts-lib.ps1')) {
        $file = Join-Path $Path $name
        Assert-DevStackHostsFile $file
        if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -cne $Hashes[$name]) {
            throw "Protected release hash mismatch: $file"
        }
    }
}
function Publish-DevStackHostsRelease([string]$Source,[Security.Principal.SecurityIdentifier]$UserSid) {
    Assert-DevStackLayoutComponent -Component Hosts -UserSid $UserSid
    $hostsDir = 'C:\DevStack\Hosts'
    $bytes = @{}; $hashes = @{}
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        foreach ($name in @('watch-hosts.ps1','hosts-lib.ps1')) {
            $path = Join-Path $Source $name
            $item = Get-DevStackLayoutItem $path
            if ($null -eq $item -or $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
                $item.Length -lt 1 -or $item.Length -gt 1048576) { throw "Missing or unsafe trusted source: $path" }
            $bytes[$name] = [IO.File]::ReadAllBytes($path)
            $hashes[$name] = ([BitConverter]::ToString($sha.ComputeHash([byte[]]$bytes[$name]))).Replace('-','')
        }
    } finally { $sha.Dispose() }
    $release = Join-Path $hostsDir (Get-DevStackHostsReleaseName $hashes)
    if ($null -ne (Get-DevStackLayoutItem $release)) {
        Assert-DevStackHostsRelease $release $UserSid $hashes
        return $release
    }
    $stage = Join-Path $hostsDir ('stage-' + [Guid]::NewGuid().ToString('N'))
    [IO.Directory]::CreateDirectory($stage,(New-DevStackLayoutAcl -Kind Hosts -UserSid $UserSid)) | Out-Null
    if (-not (Test-DevStackLayoutAcl $stage (New-DevStackLayoutAcl -Kind Hosts -UserSid $UserSid))) { throw 'Unexpected staging ACL.' }
    try {
        foreach ($name in @('watch-hosts.ps1','hosts-lib.ps1')) {
            Write-DevStackHostsNewFile (Join-Path $stage $name) $bytes[$name]
        }
        Assert-DevStackHostsRelease $stage $UserSid $hashes
        # Never replace a running generation. Publish the pair together.
        [IO.Directory]::Move($stage,$release)
        Assert-DevStackHostsRelease $release $UserSid $hashes
    } catch { throw "Hosts release failed. Preserve $stage and $release. $($_.Exception.Message)" }
    return $release
}
function Get-DevStackHostsActionArgument([string]$Watcher) {
    return '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $Watcher + '" -Once'
}
function Assert-DevStackHostsTask([object]$Task,[Security.Principal.SecurityIdentifier]$UserSid,[string]$PowerShell) {
    if ($null -eq $Task -or $Task.TaskPath -ne '\' -or @($Task.Actions).Count -ne 1 -or
        @($Task.Triggers | Where-Object { $null -ne $_ }).Count -ne 0 -or
        [string]$Task.Principal.LogonType -ne 'S4U' -or [string]$Task.Principal.RunLevel -ne 'Highest' -or
        -not $Task.Settings.Enabled) { throw 'Unexpected Hosts task contract; refusing to overwrite.' }
    $account = [string]$Task.Principal.UserId
    if ($account -match '^S-1-') { $taskSid = [Security.Principal.SecurityIdentifier]::new($account) }
    else { $taskSid = ([Security.Principal.NTAccount]::new($account)).Translate([Security.Principal.SecurityIdentifier]) }
    if ($taskSid.Value -ne $UserSid.Value -or $Task.Actions[0].Execute -ine $PowerShell) {
        throw 'Hosts task belongs to another account or executable.'
    }
    $argument = [string]$Task.Actions[0].Arguments
    $legacy = Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'DevStack\watch-hosts.ps1'
    $prefix = [regex]::Escape((Get-DevStackHostsActionArgument 'C:\DevStack\Hosts\release-PLACEHOLDER\watch-hosts.ps1'))
    $pattern = '\A' + $prefix.Replace('PLACEHOLDER','[a-f0-9]{64}') + '\z'
    if ($argument -ine (Get-DevStackHostsActionArgument $legacy) -and $argument -notmatch $pattern) {
        throw 'Unknown Hosts task action; refusing to overwrite.'
    }
}
function Get-DevStackHostsTask {
    $tasks = @(Get-ScheduledTask -ErrorAction Stop | Where-Object { $_.TaskName -eq 'DevStack Hosts Event Sync' })
    if ($tasks.Count -gt 1) { throw 'Ambiguous Hosts task name.' }
    if ($tasks.Count -eq 1) { return $tasks[0] }
    return $null
}
function Set-DevStackHostsTaskRelease([string]$Release,[Security.Principal.SecurityIdentifier]$UserSid,[string]$PowerShell,[object]$Previous) {
    Assert-DevStackLayoutComponent -Component Hosts -UserSid $UserSid
    if ($Release -cnotmatch '\AC:\\DevStack\\Hosts\\release-[a-f0-9]{64}\z') { throw 'Invalid protected release path.' }
    Assert-DevStackLayoutDirectory (Get-DevStackLayoutItem $Release) $Release
    if (-not (Test-DevStackLayoutAcl $Release (New-DevStackLayoutAcl -Kind Hosts -UserSid $UserSid))) { throw 'Unexpected release ACL.' }
    $watcher = Join-Path $Release 'watch-hosts.ps1'
    Assert-DevStackHostsFile $watcher
    Assert-DevStackHostsFile (Join-Path $Release 'hosts-lib.ps1')
    $taskName = 'DevStack Hosts Event Sync'
    $argument = Get-DevStackHostsActionArgument $watcher
    $action = New-ScheduledTaskAction -Execute $PowerShell -Argument $argument
    if ($null -ne $Previous) {
        Assert-DevStackHostsTask $Previous $UserSid $PowerShell
        if ($Previous.Actions[0].Arguments -ceq $argument) { return }
        $prior = $Previous.Actions[0]
        $xml = Export-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction Stop
        $backup = Join-Path 'C:\DevStack\Hosts' ('task-before-' + [Guid]::NewGuid().ToString('N') + '.xml')
        Write-DevStackHostsNewFile $backup ([Text.Encoding]::Unicode.GetBytes([string]$xml))
        try {
            # Change only the action. Keep task principal/settings/security.
            Set-ScheduledTask -TaskName $taskName -TaskPath '\' -Action $action -ErrorAction Stop | Out-Null
            $current = Get-DevStackHostsTask
            Assert-DevStackHostsTask $current $UserSid $PowerShell
            if ($current.Actions[0].Arguments -cne $argument) { throw 'Task action verification failed.' }
        } catch {
            $failure = $_
            try {
                Set-ScheduledTask -TaskName $taskName -TaskPath '\' -Action $prior -ErrorAction Stop | Out-Null
                $restored = Get-DevStackHostsTask
                Assert-DevStackHostsTask $restored $UserSid $PowerShell
                if ($restored.Actions[0].Arguments -cne $prior.Arguments) { throw 'Old task action not restored.' }
            } catch { throw "Task update and rollback failed. Preserve $backup and releases. $failure / $_" }
            throw $failure
        }
        Write-Host "Previous task definition retained: $backup"
    } else {
        $principal = New-ScheduledTaskPrincipal -UserId $UserSid.Value -LogonType S4U -RunLevel Highest
        $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -MultipleInstances Queue -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        Register-ScheduledTask -TaskName $taskName -TaskPath '\' -Action $action -Principal $principal -Settings $settings -Description 'One-shot elevated Windows hosts reconciliation, triggered by DevStack Linux only; no Windows logon listener.' -ErrorAction Stop | Out-Null
        try {
            $current = Get-DevStackHostsTask
            Assert-DevStackHostsTask $current $UserSid $PowerShell
            if ($current.Actions[0].Arguments -cne $argument) { throw 'New task action verification failed.' }
        } catch {
            $current = Get-DevStackHostsTask
            if ($null -ne $current -and $current.TaskPath -eq '\' -and @($current.Actions).Count -eq 1 -and $current.Actions[0].Arguments -ceq $argument) {
                Unregister-ScheduledTask -TaskName $taskName -TaskPath '\' -Confirm:$false -ErrorAction Stop
            }
            throw
        }
    }
}
