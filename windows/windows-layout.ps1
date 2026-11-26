# Shared, side-effect-free Windows layout policy. Windows PowerShell 5.1 / NTFS.
# Do not put elevated code under an existing user-owned root or adopt its children.
# The caller must explicitly provision the root before any privileged installation.

function Get-DevStackLayoutItem([string]$Path) {
    try {
        Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    } catch [System.Management.Automation.ItemNotFoundException] {
        return $null
    }
}

function Assert-DevStackLayoutDirectory([object]$Item, [string]$Path) {
    if ($null -eq $Item -or -not $Item.PSIsContainer -or
        ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Expected a real directory, not a file or reparse point: $Path"
    }
}

function Assert-DevStackVolumeBoundary {
    # C:\ permits standard users to create their own directories on a default
    # Windows installation. That is not permission to delete or replace a
    # separately protected directory belonging to Administrators.
    $volume = [IO.DriveInfo]::new('C:\')
    if ($volume.DriveType -ne [IO.DriveType]::Fixed -or $volume.DriveFormat -ne 'NTFS') {
        throw 'DevStack Windows components require a fixed NTFS C: volume.'
    }
    Assert-DevStackLayoutDirectory (Get-DevStackLayoutItem 'C:\') 'C:\'
    $acl = Get-Acl -LiteralPath 'C:\' -ErrorAction Stop
    $sidType = [Security.Principal.SecurityIdentifier]
    $trusted = @('S-1-5-18', 'S-1-5-32-544',
        'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
    if (-not $acl.AreAccessRulesCanonical -or $acl.GetOwner($sidType).Value -notin $trusted) {
        throw 'Unexpected owner or noncanonical ACL on C:\. Do not change the drive ACL automatically.'
    }
    $dangerous = [int][Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor
        [int][Security.AccessControl.FileSystemRights]::ChangePermissions -bor
        [int][Security.AccessControl.FileSystemRights]::TakeOwnership -bor
        [int][Security.AccessControl.FileSystemRights]::Delete -bor 0x10000000
    foreach ($rule in $acl.GetAccessRules($true, $true, $sidType)) {
        if ($rule.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and
            -not ($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -and
            $rule.IdentityReference.Value -notin $trusted -and
            ([int]$rule.FileSystemRights -band $dangerous)) {
            throw "C:\ grants a nontrusted principal destructive or ACL rights: $($rule.IdentityReference.Value). Refusing to provision."
        }
    }
}

function New-DevStackLayoutAcl {
    param(
        [Parameter(Mandatory=$true)]
        [ValidateSet('Root','Hosts','LocalNotifications','Widgets','LegacyRoot')]
        [string]$Kind,
        [Parameter(Mandatory=$true)][Security.Principal.SecurityIdentifier]$UserSid
    )
    $admin = [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
    $system = [Security.Principal.SecurityIdentifier]::new('S-1-5-18')
    if ($UserSid.Value -in @($admin.Value, $system.Value)) { throw 'A signed-in user SID is required.' }
    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetAccessRuleProtection($true, $false)
    $inherit = [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
    $full = [Security.AccessControl.FileSystemRights]::FullControl
    if ($Kind -eq 'Root') {
        $acl.SetOwner($admin)
        $inherit = [Security.AccessControl.InheritanceFlags]::None
        $principals = @($admin, $system)
    } elseif ($Kind -eq 'Hosts') {
        $acl.SetOwner($admin)
        $principals = @($admin, $system)
    } elseif ($Kind -eq 'Widgets') {
        $acl.SetOwner($UserSid)
        $principals = @($UserSid, $system)
    } else {
        # Exact previously installed notification/root policy. Only the
        # notification component is allowed to retain this user-owned policy.
        $acl.SetOwner($UserSid)
        $principals = @($UserSid, $system, $admin)
    }
    foreach ($principal in $principals) {
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
            $principal, $full, $inherit, [Security.AccessControl.PropagationFlags]::None,
            [Security.AccessControl.AccessControlType]::Allow))
    }
    if ($Kind -eq 'Root') {
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
            $UserSid, [Security.AccessControl.FileSystemRights]::ReadAndExecute,
            [Security.AccessControl.InheritanceFlags]::None,
            [Security.AccessControl.PropagationFlags]::None,
            [Security.AccessControl.AccessControlType]::Allow))
    }
    return $acl
}

function Get-DevStackLayoutAclSignature([Security.AccessControl.FileSystemSecurity]$Acl) {
    $sidType = [Security.Principal.SecurityIdentifier]
    $rules = @($Acl.GetAccessRules($true, $true, $sidType) | ForEach-Object {
        '{0}|{1}|{2}|{3}|{4}|{5}' -f $_.IdentityReference.Value,
            [int]$_.FileSystemRights, $_.AccessControlType,
            $_.InheritanceFlags, $_.PropagationFlags, $_.IsInherited
    } | Sort-Object)
    # Compare security semantics, not representation-only SDDL flags.
    return (@($Acl.GetOwner($sidType).Value, [string]$Acl.AreAccessRulesProtected,
        [string]$Acl.AreAccessRulesCanonical) + $rules) -join ';'
}

function Test-DevStackLayoutAcl([string]$Path, [Security.AccessControl.DirectorySecurity]$Expected) {
    $actual = Get-Acl -LiteralPath $Path -ErrorAction Stop
    return (Get-DevStackLayoutAclSignature $actual) -ceq (Get-DevStackLayoutAclSignature $Expected)
}

function Get-DevStackLayoutState([Security.Principal.SecurityIdentifier]$UserSid) {
    Assert-DevStackVolumeBoundary
    $root = 'C:\DevStack'
    $item = Get-DevStackLayoutItem $root
    if ($null -eq $item) { return 'Absent' }
    Assert-DevStackLayoutDirectory $item $root
    if (Test-DevStackLayoutAcl $root (New-DevStackLayoutAcl -Kind Root -UserSid $UserSid)) {
        return 'Secure'
    }
    if (Test-DevStackLayoutAcl $root (New-DevStackLayoutAcl -Kind LegacyRoot -UserSid $UserSid)) {
        return 'LegacyUserOwned'
    }
    return 'UnsafeOrUnknown'
}

function Assert-DevStackLayoutRoot([Security.Principal.SecurityIdentifier]$UserSid) {
    $state = Get-DevStackLayoutState $UserSid
    if ($state -ne 'Secure') {
        throw "C:\DevStack is not a provisioned administrator-owned root ($state). Do not install elevated code here."
    }
}

function Assert-DevStackLayoutComponent {
    param(
        [Parameter(Mandatory=$true)][ValidateSet('Hosts','LocalNotifications','Widgets')][string]$Component,
        [Parameter(Mandatory=$true)][Security.Principal.SecurityIdentifier]$UserSid
    )
    Assert-DevStackLayoutRoot $UserSid
    $path = Join-Path 'C:\DevStack' $Component
    Assert-DevStackLayoutDirectory (Get-DevStackLayoutItem $path) $path
    if (-not (Test-DevStackLayoutAcl $path (New-DevStackLayoutAcl -Kind $Component -UserSid $UserSid))) {
        throw "Unexpected owner or access rules on $path. Refusing to adopt or rewrite it."
    }
    # This checks the directory only. A privileged installer must also
    # validate its own destination files, and must never execute content from
    # the user-writable components with an elevated token.
}

function Initialize-DevStackLayout {
    param(
        [Parameter(Mandatory=$true)][Security.Principal.SecurityIdentifier]$UserSid,
        [Parameter(Mandatory=$true)][ValidateSet('Hosts','LocalNotifications','Widgets')][string[]]$Component
    )
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator) -or
        $identity.User.Value -ne $UserSid.Value) {
        throw 'Use elevated Windows PowerShell under the same Windows account that owns the DevStack installation.'
    }
    $state = Get-DevStackLayoutState $UserSid
    if ($state -notin @('Absent','Secure')) {
        # A previously user-owned directory may have outstanding write/delete
        # handles. Tightening its ACL in place is not a safe trust-boundary
        # transition. A separate, tested migration must publish a fresh root.
        throw "Existing DevStack root is $state. No changes made. Use the dedicated migration once available."
    }
    $root = 'C:\DevStack'
    $requested = @($Component | Select-Object -Unique)
    if ($requested.Count -eq 0) { throw 'Select at least one component.' }
    if ($state -eq 'Secure') {
        # Validate all existing requested destinations before creating anything.
        foreach ($name in $requested) {
            if ($null -ne (Get-DevStackLayoutItem (Join-Path $root $name))) {
                Assert-DevStackLayoutComponent -Component $name -UserSid $UserSid
            }
        }
    } else {
        # Create with its final security descriptor, never with an intermediate
        # user-writable DACL. CreateDirectory may find a competing existing
        # path, so always independently verify the result before using it.
        [IO.Directory]::CreateDirectory($root, (New-DevStackLayoutAcl -Kind Root -UserSid $UserSid)) | Out-Null
        Assert-DevStackLayoutRoot $UserSid
    }
    foreach ($name in $requested) {
        Assert-DevStackLayoutRoot $UserSid
        $path = Join-Path $root $name
        if ($null -eq (Get-DevStackLayoutItem $path)) {
            [IO.Directory]::CreateDirectory($path, (New-DevStackLayoutAcl -Kind $name -UserSid $UserSid)) | Out-Null
        }
        Assert-DevStackLayoutComponent -Component $name -UserSid $UserSid
    }
}
