# Disposable Windows CI only: exercises the real C:\DevStack NTFS boundary.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true') { throw 'Disposable GitHub runner required.' }
. (Join-Path $PSScriptRoot 'windows-layout.ps1')
$entry = Join-Path $PSScriptRoot 'initialize-windows-layout.ps1'
$root = 'C:\DevStack'
$user = [Security.Principal.WindowsIdentity]::GetCurrent().User
function Assert-True([bool]$Condition, [string]$Label) {
    if (-not $Condition) { throw $Label }
}
function Assert-Rejected([scriptblock]$Action, [string]$Label) {
    $rejected = $false
    try { & $Action | Out-Null } catch { $rejected = $true }
    if (-not $rejected) { throw "Unexpected success: $Label" }
}
function Prepare([string[]]$Names) {
    & $entry -Mode Prepare -ExpectedUserSid $user.Value -Component $Names
}
function Assert-Policy([string]$Path, [string]$Kind) {
    Assert-True (Test-DevStackLayoutAcl $Path (New-DevStackLayoutAcl -Kind $Kind -UserSid $user)) "Wrong ACL: $Path"
}
Assert-True ($null -eq (Get-DevStackLayoutItem $root)) 'Existing non-fixture root'
Assert-True ((& $entry).State -eq 'Absent') 'Absent root inspection'
Assert-Rejected { & $entry -Mode Prepare -ExpectedUserSid 'S-1-5-18' -Component Hosts } 'Different Windows account'
Assert-Rejected { & $entry -Mode Prepare -Component Hosts } 'Missing intended user SID'
Assert-True ($null -eq (Get-DevStackLayoutItem $root)) 'Invalid invocation created root'
try {
    Prepare @('LocalNotifications')
    Assert-Policy $root 'Root'
    $notifications = Join-Path $root 'LocalNotifications'
    Assert-Policy $notifications 'LocalNotifications'
    Assert-True ((& $entry).State -eq 'Secure') 'Protected root inspection'
    Assert-True (@(Get-ChildItem -LiteralPath $root -Force).Count -eq 1) 'Unused component directories created'
    $before = Get-DevStackLayoutAclSignature (Get-Acl -LiteralPath $root)
    $privateBefore = Get-DevStackLayoutAclSignature (Get-Acl -LiteralPath $notifications)
    Prepare @('LocalNotifications','Hosts','Widgets')
    Assert-Policy (Join-Path $root 'Hosts') 'Hosts'
    Assert-Policy (Join-Path $root 'Widgets') 'Widgets'
    Prepare @('Hosts','Widgets','LocalNotifications')
    Assert-True ((Get-DevStackLayoutAclSignature (Get-Acl -LiteralPath $root)) -ceq $before) 'Repeated preparation changed root'
    Assert-True ((Get-DevStackLayoutAclSignature (Get-Acl -LiteralPath $notifications)) -ceq $privateBefore) 'Repeated preparation changed notifications'
    Assert-True (@(Get-ChildItem -LiteralPath $root -Force).Count -eq 3) 'Wrong component count'
    # Verify a file created by an elevated caller in Hosts does not inherit
    # write rights from the private components or the root's read-only ACE.
    $hostFixture = Join-Path $root 'Hosts\fixture.txt'
    Set-Content -LiteralPath $hostFixture -Value 'host fixture' -Encoding Ascii
    $fileAcl = Get-Acl -LiteralPath $hostFixture
    $sids = @($fileAcl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]) |
        ForEach-Object { $_.IdentityReference.Value } | Sort-Object -Unique)
    Assert-True ($sids.Count -eq 2 -and 'S-1-5-18' -in $sids -and 'S-1-5-32-544' -in $sids) 'Hosts file inherited an unexpected principal'
    # A foreign ACE must cause refusal, not be silently stripped.
    $saved = Get-Acl -LiteralPath $root
    $wrong = Get-Acl -LiteralPath $root
    $wrong.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
        [Security.Principal.SecurityIdentifier]::new('S-1-1-0'), 'WriteData', 'Allow'))
    try {
        Set-Acl -LiteralPath $root -AclObject $wrong
        $bad = Get-DevStackLayoutAclSignature (Get-Acl -LiteralPath $root)
        Assert-True ((& $entry).State -eq 'UnsafeOrUnknown') 'Unexpected root ACL was accepted'
        Assert-Rejected { Prepare @('Hosts') } 'Unexpected root ACL'
        Assert-True ((Get-DevStackLayoutAclSignature (Get-Acl -LiteralPath $root)) -ceq $bad) 'Unexpected root was rewritten'
    } finally {
        Set-Acl -LiteralPath $root -AclObject $saved
    }
    # Even beneath a protected root, do not adopt a preexisting Hosts
    # directory with the wrong policy.
    $hosts = Join-Path $root 'Hosts'
    $saved = Get-Acl -LiteralPath $hosts
    $wrong = Get-Acl -LiteralPath $hosts
    $wrong.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($user, 'FullControl', 'Allow'))
    try {
        Set-Acl -LiteralPath $hosts -AclObject $wrong
        Assert-Rejected { Prepare @('Hosts') } 'User-writable Hosts directory'
        Assert-True (Test-Path -LiteralPath $hostFixture) 'Refusal removed existing data'
    } finally {
        Set-Acl -LiteralPath $hosts -AclObject $saved
    }
    Write-Output 'Fresh provision, exact ACLs, selected components, idempotence and fail-closed checks: OK.'
} finally {
    # Only paths created by this disposable test, never an arbitrary tree.
    $fixture = Join-Path $root 'Hosts\fixture.txt'
    if (Test-Path -LiteralPath $fixture) { Remove-Item -LiteralPath $fixture }
    foreach ($name in @('Hosts','Widgets','LocalNotifications')) {
        $path = Join-Path $root $name
        if (Test-Path -LiteralPath $path) { [IO.Directory]::Delete($path, $false) }
    }
    if (Test-Path -LiteralPath $root) { [IO.Directory]::Delete($root, $false) }
}

# Existing ASUS-style root must stay untouched until a real migration exists.
[IO.Directory]::CreateDirectory($root, (New-DevStackLayoutAcl -Kind LegacyRoot -UserSid $user)) | Out-Null
$marker = Join-Path $root 'keep.txt'
try {
    Set-Content -LiteralPath $marker -Value 'preserve legacy' -Encoding Ascii
    $before = Get-DevStackLayoutAclSignature (Get-Acl -LiteralPath $root)
    $hash = (Get-FileHash -LiteralPath $marker).Hash
    Assert-True ((& $entry).State -eq 'LegacyUserOwned') 'Legacy root not recognized'
    Assert-Rejected { Prepare @('Hosts','LocalNotifications') } 'Legacy root must not be adopted'
    Assert-True ((Get-DevStackLayoutAclSignature (Get-Acl -LiteralPath $root)) -ceq $before) 'Legacy ACL changed'
    Assert-True ((Get-FileHash -LiteralPath $marker).Hash -eq $hash) 'Legacy contents changed'
    Assert-True (@(Get-ChildItem -LiteralPath $root -Force).Count -eq 1) 'Legacy root gained children'
} finally {
    if (Test-Path -LiteralPath $marker) { Remove-Item -LiteralPath $marker }
    [IO.Directory]::Delete($root, $false)
}

# A junction at the root must be rejected without touching the target.
$target = Join-Path 'C:\' ('DevStack-layout-fixture-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $target | Out-Null
try {
    New-Item -ItemType Junction -Path $root -Target $target | Out-Null
    Assert-Rejected { & $entry } 'Junction root inspection'
    Assert-Rejected { Prepare @('Hosts') } 'Junction root provision'
    Assert-True (@(Get-ChildItem -LiteralPath $target -Force).Count -eq 0) 'Junction target modified'
} finally {
    if ($null -ne (Get-DevStackLayoutItem $root)) { [IO.Directory]::Delete($root, $false) }
    [IO.Directory]::Delete($target, $false)
}
Write-Output 'Legacy root and redirected root refusal: OK.'
