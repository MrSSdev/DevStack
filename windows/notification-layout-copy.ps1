# Shared helpers. Copy only the notification files, as the signed-in
# ordinary Windows user. Nothing in these directories is elevated code.
function Get-DevStackNotificationPayload([string]$Path) {
    Assert-DevStackLayoutDirectory (Get-DevStackLayoutItem $Path) $Path
    $names = @('DevStack.Notifications.exe','show-local-notification.ps1')
    $items = @(Get-ChildItem -LiteralPath $Path -Force -ErrorAction Stop)
    foreach ($item in $items) {
        if ($item.Name -cnotin $names -or $item.PSIsContainer -or
            ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
            $item.Length -lt 1 -or $item.Length -gt 4194304) {
            throw "Unexpected notification component entry: $($item.Name)"
        }
    }
    return $items
}
function Assert-DevStackNotificationPayload([string]$Path) {
    $items = @(Get-DevStackNotificationPayload $Path)
    if ($items.Count -ne 2) { throw "Incomplete notification payload: $Path" }
}
function New-DevStackMigrationContainerAcl([Security.Principal.SecurityIdentifier]$UserSid) {
    $acl = New-DevStackLayoutAcl -Kind Hosts -UserSid $UserSid
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
        $UserSid, [Security.AccessControl.FileSystemRights]::ReadAndExecute,
        [Security.AccessControl.InheritanceFlags]::None,
        [Security.AccessControl.PropagationFlags]::None,
        [Security.AccessControl.AccessControlType]::Allow))
    return $acl
}
function Assert-DevStackNotificationPrivateFiles([string]$Path,[Security.Principal.SecurityIdentifier]$UserSid) {
    $expected = @($UserSid.Value,'S-1-5-18','S-1-5-32-544')
    foreach ($item in @(Get-DevStackNotificationPayload $Path)) {
        $acl = Get-Acl -LiteralPath $item.FullName -ErrorAction Stop
        $sidType = [Security.Principal.SecurityIdentifier]
        if ($acl.GetOwner($sidType).Value -notin $expected -or -not $acl.AreAccessRulesCanonical) {
            throw "Unexpected notification file owner or ACL: $($item.Name)"
        }
        foreach ($rule in $acl.GetAccessRules($true,$true,$sidType)) {
            if ($rule.IdentityReference.Value -notin $expected) {
                throw "Unexpected notification file principal: $($item.Name)"
            }
        }
    }
}
function Copy-DevStackNotificationLayout([string]$TransactionId,[Security.Principal.SecurityIdentifier]$UserSid) {
    if ($TransactionId -cnotmatch '^[a-f0-9]{32}$') { throw 'Invalid migration transaction ID.' }
    if ((Get-DevStackLayoutState $UserSid) -ne 'LegacyUserOwned') { throw 'Expected legacy root.' }
    $root = 'C:\DevStack'
    $entries = @(Get-ChildItem -LiteralPath $root -Force)
    if ($entries.Count -ne 1 -or $entries[0].Name -cne 'LocalNotifications') {
        throw 'Unexpected old root contents. Preserve them and inspect before migration.'
    }
    $source = Join-Path $root 'LocalNotifications'
    $transaction = Join-Path 'C:\' ('DevStack-migration-' + $TransactionId)
    $next = Join-Path $transaction 'next'
    $destination = Join-Path $next 'LocalNotifications'
    foreach ($pair in @(
        @{Path=$source;Acl=(New-DevStackLayoutAcl -Kind LocalNotifications -UserSid $UserSid)},
        @{Path=$transaction;Acl=(New-DevStackMigrationContainerAcl $UserSid)},
        @{Path=$next;Acl=(New-DevStackLayoutAcl -Kind Root -UserSid $UserSid)},
        @{Path=$destination;Acl=(New-DevStackLayoutAcl -Kind LocalNotifications -UserSid $UserSid)}
    )) {
        Assert-DevStackLayoutDirectory (Get-DevStackLayoutItem $pair.Path) $pair.Path
        if (-not (Test-DevStackLayoutAcl $pair.Path $pair.Acl)) { throw "Unexpected layout ACL: $($pair.Path)" }
    }
    Assert-DevStackNotificationPayload $source
    Assert-DevStackNotificationPrivateFiles $source $UserSid
    Assert-DevStackNotificationPrivateFiles $destination $UserSid
    foreach ($name in @('DevStack.Notifications.exe','show-local-notification.ps1')) {
        $from = Join-Path $source $name
        $to = Join-Path $destination $name
        $expected = (Get-FileHash -LiteralPath $from -Algorithm SHA256).Hash
        if ($null -eq (Get-DevStackLayoutItem $to)) {
            # CreateNew refuses a competing name. Do not copy ACLs, hard links
            # or alternate streams from the old directory. Inherit the new
            # private component's ACL; no payload is executed or registered.
            $input = [IO.File]::Open($from,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
            try {
                if ($input.Length -lt 1 -or $input.Length -gt 4194304) { throw 'Unexpected notification file length.' }
                $output = [IO.File]::Open($to,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
                try { $input.CopyTo($output); $output.Flush($true) } finally { $output.Dispose() }
            } finally { $input.Dispose() }
        }
        Assert-DevStackNotificationPrivateFiles $destination $UserSid
        if ((Get-FileHash -LiteralPath $to -Algorithm SHA256).Hash -cne $expected -or
            (Get-FileHash -LiteralPath $from -Algorithm SHA256).Hash -cne $expected) {
            throw "Notification copy mismatch: $name. Preserve transaction and old installation."
        }
    }
    Assert-DevStackNotificationPayload $destination
}
