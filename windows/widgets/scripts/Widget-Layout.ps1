# Shared private widget configuration path. Included alongside installers
# in the MSIX bundle; use the same root policy in a source checkout.
$layoutLibrary = Join-Path $PSScriptRoot 'windows-layout.ps1'
if (-not (Test-Path -LiteralPath $layoutLibrary -PathType Leaf)) {
    $layoutLibrary = Join-Path $PSScriptRoot '..\..\windows-layout.ps1'
}
. $layoutLibrary

function Get-DevStackWidgetDirectory {
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    Assert-DevStackLayoutComponent -Component Widgets -UserSid $sid
    return 'C:\DevStack\Widgets'
}
function Assert-DevStackWidgetPrivateItem([string]$Path,[bool]$Directory,[Security.Principal.SecurityIdentifier]$Sid) {
    $item = Get-DevStackLayoutItem $Path
    if ($null -eq $item -or $item.PSIsContainer -ne $Directory -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Missing or redirected widget path: $Path"
    }
    $acl = Get-Acl -LiteralPath $Path -ErrorAction Stop
    $type = [Security.Principal.SecurityIdentifier]
    if ($acl.GetOwner($type).Value -notin @($Sid.Value,'S-1-5-18','S-1-5-32-544') -or -not $acl.AreAccessRulesCanonical) {
        throw "Unexpected widget owner or ACL: $Path"
    }
    foreach ($rule in $acl.GetAccessRules($true,$true,$type)) {
        if ($rule.IdentityReference.Value -notin @($Sid.Value,'S-1-5-18')) {
            throw "Unexpected widget file principal: $Path"
        }
    }
}
function New-DevStackWidgetFileAcl([Security.Principal.SecurityIdentifier]$Sid) {
    $acl = [Security.AccessControl.FileSecurity]::new()
    $acl.SetAccessRuleProtection($true,$false)
    $acl.SetOwner($Sid)
    foreach ($principal in @($Sid,[Security.Principal.SecurityIdentifier]::new('S-1-5-18'))) {
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($principal,'FullControl','Allow'))
    }
    return $acl
}
function Copy-DevStackWidgetState([string]$Source) {
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $destination = Get-DevStackWidgetDirectory
    if ($null -eq (Get-DevStackLayoutItem $Source)) { return }
    $parent = Split-Path -Parent $Source
    Assert-DevStackLayoutDirectory (Get-DevStackLayoutItem $parent) $parent
    Assert-DevStackWidgetPrivateItem $Source $true $sid
    $names = @('services.json','telemetry.json','wsl-view-cache.json','wsl-bridge.log','wsl-bridge.log.previous')
    $items = @(Get-ChildItem -LiteralPath $Source -Force)
    $hashes = @{}
    foreach ($item in $items) {
        if ($item.Name -cnotin $names -or $item.PSIsContainer -or $item.Length -gt 4194304) {
            throw "Unknown widget entry: $($item.Name). Preserve old files and inspect."
        }
        Assert-DevStackWidgetPrivateItem $item.FullName $false $sid
        $hashes[$item.Name] = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash
        $to = Join-Path $destination $item.Name
        if ($null -ne (Get-DevStackLayoutItem $to)) {
            Assert-DevStackWidgetPrivateItem $to $false $sid
            if ((Get-FileHash -LiteralPath $to -Algorithm SHA256).Hash -cne $hashes[$item.Name]) {
                throw "Widget file conflict: $($item.Name). Neither copy was overwritten."
            }
        }
    }
    foreach ($item in $items) {
        $to = Join-Path $destination $item.Name
        if ($null -eq (Get-DevStackLayoutItem $to)) {
            $sourceStream = [IO.File]::Open($item.FullName,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
            try {
                if ($sourceStream.Length -gt 4194304) { throw 'Widget file grew during copy.' }
                $destStream = [IO.File]::Open($to,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
                try { $sourceStream.CopyTo($destStream); $destStream.Flush($true) } finally { $destStream.Dispose() }
            } finally { $sourceStream.Dispose() }
            if ($item.Name -in @('services.json','telemetry.json')) {
                Set-Acl -LiteralPath $to -AclObject (New-DevStackWidgetFileAcl $sid)
            }
        }
        Assert-DevStackWidgetPrivateItem $to $false $sid
        if ((Get-FileHash -LiteralPath $to -Algorithm SHA256).Hash -cne $hashes[$item.Name] -or
            (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash -cne $hashes[$item.Name]) {
            throw "Widget copy changed: $($item.Name). Preserve both copies."
        }
    }
}
