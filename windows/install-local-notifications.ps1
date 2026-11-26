param(
    [ValidateSet('Install', 'Demo', 'Uninstall', 'CleanupLegacy', 'PrepareDirectory')]
    [string]$Mode = 'Install',
    [switch]$UseBuiltLauncher
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or
    [Diagnostics.Process]::GetCurrentProcess().SessionId -eq 0) {
    throw 'Run in the signed-in Windows user session.'
}
$parent = 'C:\DevStack'
$directory = Join-Path $parent 'LocalNotifications'
$launcher = Join-Path $directory 'DevStack.Notifications.exe'
$legacyParent = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'DevStack'
$legacyDirectory = Join-Path $legacyParent 'LocalNotifications'
$source = Join-Path $PSScriptRoot '..\scripts\windows-local-notification.ps1'
$componentFiles = @('DevStack.Notifications.exe', 'show-local-notification.ps1')
$userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$trustedSids = @($userSid.Value, 'S-1-5-18', 'S-1-5-32-544')
$writeRights = [Security.AccessControl.FileSystemRights]::WriteData -bor
    [Security.AccessControl.FileSystemRights]::AppendData -bor
    [Security.AccessControl.FileSystemRights]::WriteExtendedAttributes -bor
    [Security.AccessControl.FileSystemRights]::WriteAttributes -bor
    [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor
    [Security.AccessControl.FileSystemRights]::Delete -bor
    [Security.AccessControl.FileSystemRights]::ChangePermissions -bor
    [Security.AccessControl.FileSystemRights]::TakeOwnership

# This directory is deliberately user-owned. Never place code executed by
# an elevated scheduled task here. The hosts integration stays in ProgramData.
function Assert-SafeItem([string]$Path, [bool]$IsDirectory) {
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer -ne $IsDirectory -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Unsafe DevStack path (wrong type or reparse point): $Path"
    }
    $acl = Get-Acl -LiteralPath $Path
    if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $trustedSids) {
        throw "Unexpected DevStack owner: $Path"
    }
    foreach ($rule in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
        if ($rule.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and
            $rule.IdentityReference.Value -notin $trustedSids -and
            ($rule.FileSystemRights -band $writeRights)) {
            throw "An unrelated account can modify the DevStack path: $Path"
        }
    }
}

function New-PrivateDirectory([string]$Path) {
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $acl.SetOwner($userSid)
    foreach ($sid in $trustedSids) {
        $identity = [Security.Principal.SecurityIdentifier]::new($sid)
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            $identity, [Security.AccessControl.FileSystemRights]::FullControl,
            [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit',
            [Security.AccessControl.PropagationFlags]::None,
            [Security.AccessControl.AccessControlType]::Allow)
        $acl.AddAccessRule($rule)
    }
    # Create with the final ACL, not a broadly writable intermediate folder.
    [IO.Directory]::CreateDirectory($Path, $acl) | Out-Null
    Assert-SafeItem $Path $true
}

function Ensure-Root {
    if (-not (Test-Path -LiteralPath $parent)) {
        try {
            New-PrivateDirectory $parent
        } catch {
            throw "Cannot provision $parent for this Windows account. If access is denied, run this installer with -Mode PrepareDirectory in elevated Windows PowerShell under the same account. $($_.Exception.Message)"
        }
    }
    Assert-SafeItem $parent $true
}

function Assert-Component([string]$Path) {
    Assert-SafeItem $Path $true
    foreach ($item in @(Get-ChildItem -LiteralPath $Path -Force)) {
        if ($item.Name -notin $componentFiles -or $item.PSIsContainer) {
            throw "Unexpected file in managed notification component: $($item.FullName)"
        }
        Assert-SafeItem $item.FullName $false
    }
}

function Remove-Component([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    Assert-Component $Path
    foreach ($name in $componentFiles) {
        $file = Join-Path $Path $name
        if (Test-Path -LiteralPath $file) {
            Remove-Item -LiteralPath $file
        }
    }
    Remove-Item -LiteralPath $Path
}

function Remove-EmptyDirectory([string]$Path) {
    if ((Test-Path -LiteralPath $Path -PathType Container) -and
        @(Get-ChildItem -LiteralPath $Path -Force).Count -eq 0) {
        Remove-Item -LiteralPath $Path
    }
}

function Remove-LegacyComponent {
    if (Test-Path -LiteralPath $legacyDirectory) {
        # Do not follow a redirected DevStack directory and do not touch Widgets.
        Assert-SafeItem $legacyParent $true
        Remove-Component $legacyDirectory
        Remove-EmptyDirectory $legacyParent
    }
}

if ($Mode -eq 'PrepareDirectory') {
    Ensure-Root
    Write-Output $parent
    return
}
if ($Mode -eq 'CleanupLegacy') {
    # Only after Ubuntu has switched its marker to the new path. Preserve
    # the shared Windows notification identity and its settings.
    Remove-LegacyComponent
    return
}
if ($Mode -eq 'Uninstall') {
    if (Test-Path -LiteralPath $parent) {
        Assert-SafeItem $parent $true
        if (Test-Path -LiteralPath $directory) { Assert-Component $directory }
    }
    if (Test-Path -LiteralPath $legacyDirectory) {
        Assert-SafeItem $legacyParent $true
        Assert-Component $legacyDirectory
    }
    & $source -Mode Unregister
    Remove-Component $directory
    Remove-LegacyComponent
    Remove-EmptyDirectory $parent
    return
}
if ($Mode -eq 'Demo') {
    Assert-SafeItem $parent $true
    Assert-Component $directory
    $event = '{"id":"batch-00000000000000000000000000000000","category":"mail","kind":"info"}'
    $encoded = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($event))
    $process = Start-Process -FilePath $launcher -ArgumentList $encoded -PassThru -Wait
    if ($process.ExitCode -ne 0) {
        throw "The Windows notification test failed (exit $($process.ExitCode))."
    }
    Write-Output 'Windows accepted the DevStack test notification.'
    return
}

Ensure-Root
if (Test-Path -LiteralPath $directory) { Assert-Component $directory }
$staging = Join-Path $parent ('LocalNotifications.stage-' + [Guid]::NewGuid().ToString('N'))
$backup = Join-Path $parent ('LocalNotifications.previous-' + [Guid]::NewGuid().ToString('N'))
$registration = 'HKCU:\Software\Classes\AppUserModelId\DevStack.LocalNotifications'
$hadRegistration = Test-Path -LiteralPath $registration
$oldMoved = $false
$newMoved = $false
$committed = $false
New-PrivateDirectory $staging
try {
    $temporaryLauncher = Join-Path $staging 'DevStack.Notifications.exe'
    $temporaryScript = Join-Path $staging 'show-local-notification.ps1'
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
    Copy-Item -LiteralPath $source -Destination $temporaryScript
    Assert-Component $staging
    if (-not (Test-Path -LiteralPath $temporaryLauncher -PathType Leaf) -or
        -not (Test-Path -LiteralPath $temporaryScript -PathType Leaf)) {
        throw 'Incomplete staged notification component.'
    }
    if (Test-Path -LiteralPath $directory) {
        Move-Item -LiteralPath $directory -Destination $backup
        $oldMoved = $true
    }
    Move-Item -LiteralPath $staging -Destination $directory
    $newMoved = $true
    & (Join-Path $directory 'show-local-notification.ps1') -Mode Register
    $committed = $true
} catch {
    if ($newMoved) { Remove-Component $directory }
    if ($oldMoved) { Move-Item -LiteralPath $backup -Destination $directory }
    if (-not $hadRegistration -and (Test-Path -LiteralPath $registration)) {
        Remove-Item -LiteralPath $registration -Recurse
    }
    throw
} finally {
    if (Test-Path -LiteralPath $staging) { Remove-Component $staging }
}
if ($committed -and $oldMoved) { Remove-Component $backup }
# Leave a legacy installation in place until the Linux marker is switched.
Write-Output $launcher
