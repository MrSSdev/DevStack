# Pure Windows hosts-file transformation. No privileges or WSL calls here.
Set-StrictMode -Version Latest
$DevStackBegin = '# BEGIN DevStack managed domains'
$DevStackEnd = '# END DevStack managed domains'
$DevStackLegacy = '# DevStack local domains'

function Get-DevStackManagedHostsContent {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][string]$Original,
        [Parameter(Mandatory=$true)][string[]]$Domains
    )

    $valid = @()
    foreach ($domain in $Domains) {
        if ($domain -cnotmatch '^[a-z][a-z0-9-]{0,39}\.test$') {
            throw "Invalid DevStack domain: $domain"
        }
        $valid += $domain
    }
    $valid = @($valid | Sort-Object -Unique)
    if ($valid -notcontains 'devstack.test') { throw 'Missing devstack.test.' }

    $remaining = New-Object 'System.Collections.Generic.List[string]'
    $inManaged = $false
    $inLegacy = $false
    $managedBlocks = 0
    $lines = $Original -split '\r?\n'
    foreach ($line in $lines) {
        if ($inManaged) {
            if ($line.Trim() -eq $DevStackEnd) {
                $inManaged = $false
            } else {
                if ($line.Trim() -eq $DevStackBegin) { throw 'Nested DevStack hosts block.' }
                # Strictly guard the managed region against unrelated entries.
                if ($line.Trim() -and $line -cnotmatch '^127\.0\.0\.1 [a-z][a-z0-9-]{0,39}\.test$') {
                    throw 'Unexpected content in the managed DevStack hosts block.'
                }
            }
            continue
        }
        if ($line.Trim() -eq $DevStackBegin) {
            $inManaged = $true
            $managedBlocks++
            if ($managedBlocks -gt 1) { throw 'Multiple DevStack hosts blocks found.' }
            continue
        }
        if ($line.Trim() -eq $DevStackEnd) { throw 'Unmatched DevStack hosts end marker.' }
        if ($line.Trim() -eq $DevStackLegacy) {
            $inLegacy = $true
            continue
        }
        if ($inLegacy) {
            if ($line -cmatch '^127\.0\.0\.1 [a-z][a-z0-9-]{0,39}\.test$') {
                continue
            }
            if (-not $line.Trim()) {
                # Stop at the blank line after the legacy entries.
                $inLegacy = $false
                continue
            }
            $inLegacy = $false
        }
        [void]$remaining.Add($line)
    }
    if ($inManaged) { throw 'Unterminated DevStack hosts block.' }

    # Preserve all other entries. Windows hosts requires only ASCII for local .test.
    $base = ($remaining -join "`r`n").TrimEnd("`r", "`n")
    $managed = @($DevStackBegin) + @($valid | ForEach-Object { "127.0.0.1 $_" }) + @($DevStackEnd)
    if ($base.Length -eq 0) { return ($managed -join "`r`n") + "`r`n" }
    return $base + "`r`n`r`n" + ($managed -join "`r`n") + "`r`n"
}
