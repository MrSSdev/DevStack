# Root-only migration checkpoint. Never execute or read legacy component
# files with administrator rights. The full integration orchestrator is a
# later gate; do not use this script on a working workstation yet.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidateSet('Stage','Inspect','Publish','Rollback')][string]$Mode,
    [Parameter(Mandatory=$true)][string]$ExpectedUserSid,
    [ValidatePattern('^[a-f0-9]{32}$')][string]$TransactionId
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or $PSVersionTable.PSEdition -ne 'Desktop') {
    throw 'Use Windows PowerShell 5.1.'
}
. (Join-Path $PSScriptRoot 'windows-layout.ps1')
. (Join-Path $PSScriptRoot 'notification-layout-copy.ps1')
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$userSid = $identity.User
if ($ExpectedUserSid -cne $userSid.Value -or
    -not ([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Use elevated Windows PowerShell under the intended signed-in account.'
}
Assert-DevStackVolumeBoundary
if (-not ('DevStack.WindowsLayout.DirectoryIdentity' -as [type])) {
    Add-Type -Path (Join-Path $PSScriptRoot 'layout-directory-identity.cs')
}
$root = 'C:\DevStack'
$admin = [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
$system = [Security.Principal.SecurityIdentifier]::new('S-1-5-18')

function New-TransactionAcl {
    return (New-DevStackMigrationContainerAcl $userSid)
}
function New-JournalAcl {
    $acl = [Security.AccessControl.FileSecurity]::new()
    $acl.SetAccessRuleProtection($true, $false)
    $acl.SetOwner($admin)
    foreach ($sid in @($admin,$system)) {
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid,'FullControl','Allow'))
    }
    return $acl
}
function Assert-DirectoryPolicy([string]$Path, [string]$Kind) {
    Assert-DevStackLayoutDirectory (Get-DevStackLayoutItem $Path) $Path
    if (-not (Test-DevStackLayoutAcl $Path (New-DevStackLayoutAcl -Kind $Kind -UserSid $userSid))) {
        throw "Unexpected directory policy: $Path"
    }
}
function Assert-TransactionParent {
    Assert-DevStackLayoutDirectory (Get-DevStackLayoutItem $transaction) $transaction
    if (-not (Test-DevStackLayoutAcl $transaction (New-TransactionAcl))) {
        throw "Unexpected transaction directory ACL: $transaction"
    }
}
function Assert-RootOnlyPhase([string]$Path) {
    Assert-DirectoryPolicy $Path 'Root'
    $children = @(Get-ChildItem -LiteralPath $Path -Force)
    if ($children.Count -ne 1 -or $children[0].Name -cne 'LocalNotifications') {
        throw "Root-only migration requires exactly the private notification component: $Path"
    }
    # Check the leaf directory, not user-controlled files inside it.
    Assert-DirectoryPolicy (Join-Path $Path 'LocalNotifications') 'LocalNotifications'
}
function Assert-LegacyHostsTask {
    # Do not roll back into a user-owned root if our elevated task has
    # already been switched. Other elevated tasks are outside this fixture.
    $tasks = @(Get-ScheduledTask -ErrorAction Stop | Where-Object { $_.TaskName -eq 'DevStack Hosts Event Sync' })
    if ($tasks.Count -eq 0) { return }
    $expected = Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'DevStack\watch-hosts.ps1'
    $argument = '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $expected + '" -Once'
    if ($tasks.Count -ne 1 -or $tasks[0].TaskPath -ne '\' -or
        @($tasks[0].Actions).Count -ne 1 -or $tasks[0].Actions[0].Arguments -ine $argument) {
        throw 'The Hosts task is not in the known legacy state. Root-only migration/rollback is refused.'
    }
}
function Get-DirectoryState([string]$Path) {
    if ($null -eq (Get-DevStackLayoutItem $Path)) { return 'Absent' }
    $id = [DevStack.WindowsLayout.DirectoryIdentity]::Get($Path)
    if ($id -ceq $manifest.oldId) {
        Assert-DirectoryPolicy $Path 'LegacyRoot'
        return 'Old'
    }
    if ($id -ceq $manifest.newId) {
        Assert-RootOnlyPhase $Path
        return 'New'
    }
    throw "Unexpected directory identity at $Path. Preserve all paths; no replacement attempted."
}
function Get-MigrationPhase {
    Assert-DevStackVolumeBoundary
    Assert-TransactionParent
    $signature = '{0}/{1}/{2}' -f (Get-DirectoryState $root), (Get-DirectoryState $next), (Get-DirectoryState $previous)
    switch ($signature) {
        'Old/New/Absent' { return 'Staged' }
        'Absent/New/Old' { return 'BetweenRenames' }
        'New/Absent/Old' { return 'Published' }
        default { throw "Unexpected migration state: $signature. Preserve all paths and inspect." }
    }
}
function Move-Checked([string]$From,[string]$To,[string]$Id) {
    Assert-DevStackVolumeBoundary
    Assert-TransactionParent
    if ($null -ne (Get-DevStackLayoutItem $To)) { throw "Destination already exists: $To" }
    # Atomic no-replace rename of this particular directory object. Only
    # fixed direct children of C:\ or the administrator-owned transaction
    # directory are used as source/destination; never descend into legacy data.
    [DevStack.WindowsLayout.DirectoryIdentity]::Move($From,$To,$Id)
    if ([DevStack.WindowsLayout.DirectoryIdentity]::Get($To) -cne $Id) {
        throw "Unexpected directory identity after rename: $To"
    }
}
function Read-Manifest {
    $item = Get-DevStackLayoutItem $journal
    if ($null -eq $item -or $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Missing or redirected migration journal.'
    }
    $acl = Get-Acl -LiteralPath $journal
    if ((Get-DevStackLayoutAclSignature $acl) -cne (Get-DevStackLayoutAclSignature (New-JournalAcl))) {
        throw 'Unexpected migration journal ACL.'
    }
    $data = [IO.File]::ReadAllText($journal) | ConvertFrom-Json
    if ($data.schema -ne 1 -or $data.transaction -cne $TransactionId -or $data.userSid -cne $userSid.Value -or
        $data.oldId -cnotmatch '^[a-f0-9]{8}:[a-f0-9]{16}$' -or
        $data.newId -cnotmatch '^[a-f0-9]{8}:[a-f0-9]{16}$' -or $data.oldId -ceq $data.newId) {
        throw 'Unexpected migration journal contents.'
    }
    return $data
}

if ($Mode -eq 'Stage') {
    if ((Get-DevStackLayoutState $userSid) -ne 'LegacyUserOwned') {
        throw 'Root-only migration requires the known legacy user-owned root.'
    }
    Assert-LegacyHostsTask
    if (-not $TransactionId) { $TransactionId = [Guid]::NewGuid().ToString('N') }
} elseif (-not $TransactionId) {
    throw 'An existing transaction ID is required.'
}
$transaction = Join-Path 'C:\' ('DevStack-migration-' + $TransactionId)
$next = Join-Path $transaction 'next'
$previous = Join-Path $transaction 'previous'
$journal = Join-Path $transaction 'state.json'

if ($Mode -eq 'Stage') {
    $oldId = [DevStack.WindowsLayout.DirectoryIdentity]::Get($root)
    if ($null -ne (Get-DevStackLayoutItem $transaction)) { throw 'Transaction name collision.' }
    try {
        [IO.Directory]::CreateDirectory($transaction,(New-TransactionAcl)) | Out-Null
        Assert-TransactionParent
        if (@(Get-ChildItem -LiteralPath $transaction -Force).Count -ne 0) { throw 'Transaction must be empty.' }
        [IO.Directory]::CreateDirectory($next,(New-DevStackLayoutAcl -Kind Root -UserSid $userSid)) | Out-Null
        Assert-DirectoryPolicy $next 'Root'
        [IO.Directory]::CreateDirectory((Join-Path $next 'LocalNotifications'),
            (New-DevStackLayoutAcl -Kind LocalNotifications -UserSid $userSid)) | Out-Null
        Assert-RootOnlyPhase $next
        $manifest = [pscustomobject]@{
            schema=1; transaction=$TransactionId; userSid=$userSid.Value
            oldId=$oldId; newId=[DevStack.WindowsLayout.DirectoryIdentity]::Get($next)
        }
        $bytes = [Text.Encoding]::UTF8.GetBytes(($manifest | ConvertTo-Json -Compress))
        # Create new, with final administrator-only file ACL and owner.
        $stream = [IO.FileStream]::new($journal,[IO.FileMode]::CreateNew,
            [Security.AccessControl.FileSystemRights]::Write,[IO.FileShare]::None,
            4096,[IO.FileOptions]::WriteThrough,(New-JournalAcl))
        try { $stream.Write($bytes,0,$bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
        $manifest = Read-Manifest
        if ((Get-MigrationPhase) -ne 'Staged') { throw 'Staged state verification failed.' }
    } catch {
        throw "Stage failed. Preserve $transaction for inspection. $($_.Exception.Message)"
    }
} else {
    Assert-TransactionParent
    $manifest = Read-Manifest
}
$phase = Get-MigrationPhase
if ($Mode -eq 'Publish' -or $Mode -eq 'Rollback') {
    Assert-LegacyHostsTask
}
if ($Mode -eq 'Publish' -and $phase -ne 'Published') {
    # Metadata-only check. The ordinary user must already have copied and
    # verified the payload. No privileged read or execution of that payload.
    Assert-DevStackNotificationPayload (Join-Path $next 'LocalNotifications')
    if ($phase -eq 'Staged') {
        Move-Checked $root $previous $manifest.oldId
    }
    try {
        if ((Get-MigrationPhase) -ne 'BetweenRenames') { throw 'Cannot publish from this state.' }
        Move-Checked $next $root $manifest.newId
    } catch {
        $failure = $_
        try {
            if ((Get-MigrationPhase) -eq 'BetweenRenames') {
                Move-Checked $previous $root $manifest.oldId
            }
        } catch {
            throw "Publish failed; automatic restore incomplete. Preserve $transaction and C:\DevStack. $failure / $_"
        }
        throw $failure
    }
} elseif ($Mode -eq 'Rollback' -and $phase -ne 'Staged') {
    if ($phase -eq 'Published') {
        Move-Checked $root $next $manifest.newId
    }
    try {
        if ((Get-MigrationPhase) -ne 'BetweenRenames') { throw 'Cannot restore from this state.' }
        Move-Checked $previous $root $manifest.oldId
    } catch {
        $failure = $_
        try {
            if ((Get-MigrationPhase) -eq 'BetweenRenames') {
                Move-Checked $next $root $manifest.newId
            }
        } catch {
            throw "Rollback failed; restore of the protected root incomplete. Preserve $transaction and C:\DevStack. $failure / $_"
        }
        throw $failure
    }
}
[pscustomobject]@{
    TransactionId=$TransactionId
    Phase=Get-MigrationPhase
    StagingRoot=$next
    PreviousRoot=$previous
}
