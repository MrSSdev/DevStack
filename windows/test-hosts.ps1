# Pure, unprivileged tests for the Windows hosts migration. Safe in CI.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'hosts-lib.ps1')

function Assert([bool]$valid, [string]$message) {
    if (-not $valid) { throw "Test failed: $message" }
}

$original = "127.0.0.1 localhost`r`n10.12.0.1 internal.example`r`n# hand-written comment`r`n"
$one = Get-DevStackManagedHostsContent -Original $original -Domains @('devstack.test','alpha.test')
Assert ($one.Contains('127.0.0.1 localhost')) 'unrelated loopback retained'
Assert ($one.Contains('10.12.0.1 internal.example')) 'unrelated mapping retained'
Assert ($one.Contains('# hand-written comment')) 'unrelated comment retained'
Assert ($one.Contains('127.0.0.1 alpha.test')) 'project added'
Assert ($one.Contains('# BEGIN DevStack managed domains')) 'managed block created'
Assert (($one -split '127.0.0.1 alpha.test').Count -eq 2) 'no duplicate managed domain'

$again = Get-DevStackManagedHostsContent -Original $one -Domains @('alpha.test','devstack.test')
Assert ($one -ceq $again) 'sync is idempotent'

$changed = Get-DevStackManagedHostsContent -Original $one -Domains @('devstack.test','beta.test')
Assert (-not $changed.Contains('127.0.0.1 alpha.test')) 'removed domain cleaned'
Assert ($changed.Contains('127.0.0.1 beta.test')) 'new domain added'
Assert ($changed.Contains('10.12.0.1 internal.example')) 'unmanaged content intact after change'

$legacy = $original + "`r`n# DevStack local domains`r`n127.0.0.1 devstack.test`r`n127.0.0.1 old.test`r`n`r`n# another unrelated note`r`n"
$migrated = Get-DevStackManagedHostsContent -Original $legacy -Domains @('devstack.test','fresh.test')
Assert (-not $migrated.Contains('# DevStack local domains')) 'legacy marker removed'
Assert (-not $migrated.Contains('127.0.0.1 old.test')) 'legacy stale domain removed'
Assert ($migrated.Contains('# another unrelated note')) 'legacy trailing custom text retained'

try {
    Get-DevStackManagedHostsContent -Original $original -Domains @('devstack.test','a.example.com') | Out-Null
    throw 'Invalid domain accepted'
} catch {
    Assert ($_.Exception.Message -match 'Invalid DevStack domain') 'invalid domain rejected'
}

try {
    $corrupt = "# BEGIN DevStack managed domains`r`n127.0.0.1 devstack.test`r`n# unrelated`r`n# END DevStack managed domains"
    Get-DevStackManagedHostsContent -Original $corrupt -Domains @('devstack.test') | Out-Null
    throw 'Unexpected managed content accepted'
} catch {
    Assert ($_.Exception.Message -match 'Unexpected content') 'corruption fails closed'
}
Write-Host 'Windows DevStack hosts sync tests: OK'
