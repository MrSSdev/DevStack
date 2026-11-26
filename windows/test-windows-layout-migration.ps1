# Disposable Windows CI. No real notification binaries, WSL, registry changes
# or task installation. Test the actual directory identity and rename logic.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true') { throw 'Disposable GitHub runner required.' }
. (Join-Path $PSScriptRoot 'windows-layout.ps1')
$script = Join-Path $PSScriptRoot 'windows-layout-migration.ps1'
$root = 'C:\DevStack'
$user = [Security.Principal.WindowsIdentity]::GetCurrent().User
function Assert-True([bool]$Condition,[string]$Label) { if (-not $Condition) { throw $Label } }
function Assert-Rejected([scriptblock]$Action,[string]$Label) {
    $rejected = $false
    try { & $Action | Out-Null } catch { $rejected = $true }
    if (-not $rejected) { throw "Unexpected success: $Label" }
}
function Invoke-Migration([string]$Mode,[string]$Id) {
    & $script -Mode $Mode -ExpectedUserSid $user.Value -TransactionId $Id
}
Assert-True ($null -eq (Get-DevStackLayoutItem $root)) 'Existing non-fixture root'
Assert-Rejected { & $script -Mode Stage -ExpectedUserSid 'S-1-5-18' } 'Wrong account'
[IO.Directory]::CreateDirectory($root,(New-DevStackLayoutAcl -Kind LegacyRoot -UserSid $user)) | Out-Null
$oldNotifications = Join-Path $root 'LocalNotifications'
[IO.Directory]::CreateDirectory($oldNotifications,(New-DevStackLayoutAcl -Kind LocalNotifications -UserSid $user)) | Out-Null
$oldFile = Join-Path $oldNotifications 'old-fixture.txt'
Set-Content -LiteralPath $oldFile -Value 'original' -Encoding Ascii
$hash = (Get-FileHash -LiteralPath $oldFile).Hash
$state = & $script -Mode Stage -ExpectedUserSid $user.Value
$id = $state.TransactionId
$transaction = Split-Path $state.StagingRoot
$next = $state.StagingRoot
$previous = $state.PreviousRoot
$nextFile = Join-Path $next 'LocalNotifications\new-fixture.txt'
try {
    Assert-True ($state.Phase -eq 'Staged') 'Wrong staged phase'
    Assert-True ((Get-DevStackLayoutState $user) -eq 'LegacyUserOwned') 'Stage changed old root'
    Assert-True ((Get-FileHash -LiteralPath $oldFile).Hash -eq $hash) 'Stage changed old file'
    $oldId = [DevStack.WindowsLayout.DirectoryIdentity]::Get($root)
    $newId = [DevStack.WindowsLayout.DirectoryIdentity]::Get($next)
    Assert-True ($oldId -cne $newId) 'Staging reused the old directory object'
    # Represents later unelevated staging. No production code is copied.
    Set-Content -LiteralPath $nextFile -Value 'replacement' -Encoding Ascii
    $newHash = (Get-FileHash -LiteralPath $nextFile).Hash
    # Wrong source identity or existing destination must never be replaced.
    Assert-Rejected { [DevStack.WindowsLayout.DirectoryIdentity]::Move($root,$previous,$newId) } 'Wrong source ID'
    Assert-Rejected { [DevStack.WindowsLayout.DirectoryIdentity]::Move($root,$next,$oldId) } 'Occupied destination'
    Assert-True ((Invoke-Migration Inspect $id).Phase -eq 'Staged') 'Rejected rename changed state'

    # Simulate an interruption between the two actual NTFS renames.
    [DevStack.WindowsLayout.DirectoryIdentity]::Move($root,$previous,$oldId)
    Assert-True ((Invoke-Migration Inspect $id).Phase -eq 'BetweenRenames') 'Interrupted state not detected'
    # A competing name must not be overwritten by publish or rollback.
    Set-Content -LiteralPath $root -Value 'collision' -Encoding Ascii
    try {
        Assert-Rejected { Invoke-Migration Publish $id } 'Occupied root publish'
        Assert-Rejected { Invoke-Migration Rollback $id } 'Occupied root rollback'
        Assert-True ((Get-Content -LiteralPath $root -Raw).Trim() -eq 'collision') 'Collision was overwritten'
    } finally { Remove-Item -LiteralPath $root }
    Assert-True ((Invoke-Migration Rollback $id).Phase -eq 'Staged') 'Interrupted-state restore failed'
    Assert-True ((Get-FileHash -LiteralPath $oldFile).Hash -eq $hash) 'Interrupted restore changed old content'

    $result = Invoke-Migration Publish $id
    Assert-True ($result.Phase -eq 'Published') 'Publication failed'
    Assert-True ((Get-DevStackLayoutState $user) -eq 'Secure') 'Published root is not protected'
    Assert-True (([DevStack.WindowsLayout.DirectoryIdentity]::Get($root)) -ceq $newId) 'Published root identity changed'
    Assert-True (([DevStack.WindowsLayout.DirectoryIdentity]::Get($previous)) -ceq $oldId) 'Backup identity changed'
    Assert-True ((Get-FileHash -LiteralPath (Join-Path $previous 'LocalNotifications\old-fixture.txt')).Hash -eq $hash) 'Backup content changed'
    $published = Join-Path $root 'LocalNotifications\new-fixture.txt'
    Assert-True ((Get-FileHash -LiteralPath $published).Hash -eq $newHash) 'Staged data changed'
    Assert-True ((Invoke-Migration Publish $id).Phase -eq 'Published') 'Repeat publication not idempotent'
    # Once Hosts appears, even an empty directory, do not allow a root-only rollback.
    $hosts = Join-Path $root 'Hosts'
    [IO.Directory]::CreateDirectory($hosts,(New-DevStackLayoutAcl -Kind Hosts -UserSid $user)) | Out-Null
    try { Assert-Rejected { Invoke-Migration Rollback $id } 'Hosts present' }
    finally { [IO.Directory]::Delete($hosts,$false) }
    Assert-True ((Invoke-Migration Rollback $id).Phase -eq 'Staged') 'Explicit rollback failed'
    Assert-True (([DevStack.WindowsLayout.DirectoryIdentity]::Get($root)) -ceq $oldId) 'Rollback did not restore old object'
    Assert-True ((Get-FileHash -LiteralPath $oldFile).Hash -eq $hash) 'Rollback changed old content'
    Assert-True ((Get-FileHash -LiteralPath $nextFile).Hash -eq $newHash) 'Rollback lost staged data'
    Assert-True ((Invoke-Migration Rollback $id).Phase -eq 'Staged') 'Repeat rollback not idempotent'
    Write-Output 'Root identity, no-replace publication, interruption, collision, rollback and retention: OK.'
} finally {
    # Only fixture paths on a disposable runner. On an unexpected phase,
    # leave the fixture for diagnostics rather than deleting arbitrary data.
    if ((Invoke-Migration Inspect $id).Phase -eq 'Staged') {
        Remove-Item -LiteralPath $oldFile,$nextFile
        [IO.Directory]::Delete($oldNotifications,$false)
        [IO.Directory]::Delete($root,$false)
        [IO.Directory]::Delete((Join-Path $next 'LocalNotifications'),$false)
        [IO.Directory]::Delete($next,$false)
        Remove-Item -LiteralPath (Join-Path $transaction 'state.json')
        [IO.Directory]::Delete($transaction,$false)
    }
}
