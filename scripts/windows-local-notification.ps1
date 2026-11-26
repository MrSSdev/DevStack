param(
    [ValidateSet('Show', 'Register', 'Unregister', 'Render')]
    [string]$Mode = 'Show'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$applicationId = 'DevStack.LocalNotifications'
$registration = "HKCU:\Software\Classes\AppUserModelId\$applicationId"

# On the first notification Windows may not have created per-app settings
# yet (ERROR_NOT_FOUND, 0x80070490). Let WinRT itself enforce policy on
# that single Show call. Every other error or explicit disabled setting
# remains a failure. Do not change Windows notification preferences.
function Assert-DevStackNotificationSetting([scriptblock]$ReadSetting) {
    try {
        $value = & $ReadSetting
    } catch {
        if ($_.Exception.GetBaseException().HResult.ToString('X8') -eq '80070490') {
            return
        }
        throw
    }
    if ($null -eq $value -or $value.ToString() -ne 'Enabled') {
        throw 'Windows notifications are disabled or unavailable for DevStack.'
    }
}

function Read-DevStackNotice {
    $line = [Console]::ReadLine()
    if ([string]::IsNullOrEmpty($line) -or $line.Length -gt 256) {
        throw 'Expected a small DevStack event on stdin.'
    }
    $event = ConvertFrom-Json -InputObject $line
    if ($null -eq $event -or $event.PSObject.Properties.Name.Count -ne 3 -or
        $event.id -isnot [string] -or $event.id -cnotmatch '\A(?:(?:op|job|batch|service)-[a-f0-9]{32}|mail-[A-Za-z0-9]{22})\z' -or
        $event.category -isnot [string] -or $event.category -cnotin @('mail', 'project', 'installer', 'service', 'settings') -or
        $event.kind -isnot [string] -or $event.kind -cnotin @('success', 'error', 'info')) {
        throw 'Invalid DevStack event.'
    }
    return $event
}

# Prefer the PWA only when there is evidence of an OS protocol handler.
# Chromium registers per-user capabilities; UserChoice may not yet exist
# before the first selection. Read associations but never set defaults,
# execute registry commands, or register a competing protocol handler.
function Test-DevStackPwaProtocol(
    [scriptblock]$ReadValue = {
        param($Path, $Name)
        [Microsoft.Win32.Registry]::GetValue($Path, $Name, $null)
    },
    [scriptblock]$ReadCapabilities = {
        $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Software\RegisteredApplications')
        if ($null -ne $key) {
            try {
                foreach ($name in $key.GetValueNames()) { $key.GetValue($name) }
            } finally { $key.Dispose() }
        }
    },
    [scriptblock]$ReadPackages = {
        $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel\Repository\Packages')
        if ($null -ne $key) {
            try { $key.GetSubKeyNames() } finally { $key.Dispose() }
        }
    }
) {
    try {
        $scheme = 'web+devstack'
        $classes = 'HKEY_CLASSES_ROOT\'
        # Edge on Windows 11 may install a PWA as an AppX package. Its
        # per-user capabilities are in AppModel\Repository\Packages, not
        # RegisteredApplications. The ProgID uses DelegateExecute instead
        # of a default shell command, even before UserChoice is created.
        # Match the fixed DevStack origin, not a machine-specific package,
        # Edge app ID, ProgID, version, publisher hash or COM delegate.
        $repository = 'HKEY_CURRENT_USER\Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel\Repository\Packages\'
        foreach ($package in @(& $ReadPackages)) {
            if ($package -isnot [string] -or $package -notmatch '\Adevstack\.test-[A-Za-z0-9_.-]{1,220}\z') { continue }
            $progId = & $ReadValue ($repository + $package + '\App\Capabilities\URLAssociations') $scheme
            if ($progId -isnot [string] -or $progId -notmatch '\AAppX[A-Za-z0-9]{1,128}\z') { continue }
            $open = $classes + $progId + '\Shell\open'
            $packageId = & $ReadValue $open 'PackageId'
            $contract = & $ReadValue $open 'ContractId'
            $delegate = & $ReadValue ($open + '\command') 'DelegateExecute'
            if ($packageId -is [string] -and $packageId -ieq $package -and
                $contract -is [string] -and $contract -ieq 'Windows.Protocol' -and
                $delegate -is [string] -and
                $delegate -match '\A\{[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\}\z') {
                return $true
            }
        }
        $choice = & $ReadValue 'HKEY_CURRENT_USER\Software\Microsoft\Windows\Shell\Associations\UrlAssociations\web+devstack\UserChoice' 'ProgId'
        $candidates = @($choice, $scheme)
        foreach ($capability in @(& $ReadCapabilities)) {
            if ($capability -is [string] -and $capability.StartsWith('Software\', [StringComparison]::OrdinalIgnoreCase)) {
                $progId = & $ReadValue ('HKEY_CURRENT_USER\' + $capability + '\URLAssociations') $scheme
                if ($progId -is [string]) { $candidates += $progId }
            }
        }
        foreach ($progId in $candidates) {
            if ($progId -isnot [string] -or $progId -cnotmatch '\A[A-Za-z0-9_.+-]{1,256}\z') { continue }
            $command = & $ReadValue ($classes + $progId + '\shell\open\command') ''
            if ($command -is [string] -and -not [string]::IsNullOrWhiteSpace($command)) { return $true }
        }
    } catch {
        # A missing or inaccessible association must never block delivery.
    }
    return $false
}

function New-DevStackToastXml($Event, [bool]$UsePwa = (Test-DevStackPwaProtocol)) {
    $route = switch -CaseSensitive ($Event.category) {
        'mail' {
            if ($Event.id.StartsWith('mail-', [StringComparison]::Ordinal)) {
                '/mail/' + $Event.id.Substring(5)
            } else {
                '/mail'
            }
        }
        'project' { '/projects' }
        'installer' { '/projects' }
        'settings' { '/settings' }
        default { '/dashboard' }
    }
    $title = switch -CaseSensitive ($Event.category) {
        'mail' { 'Nowa poczta w DevStack' }
        'project' { 'Zdarzenie projektu' }
        'installer' { 'Zdarzenie instalatora' }
        default { 'DevStack' }
    }
    $message = if ($Event.kind -ceq 'error') {
        'Operacja wymaga uwagi. Zobacz szczegoly w DevStack.'
    } else {
        'Zobacz szczegoly w DevStack.'
    }
    $https = 'https://devstack.test/#' + $route + '?notice=' + $Event.id
    $launch = $https
    $actions = ''
    if ($UsePwa) {
        $launch = 'web+devstack://open' + $route + '?notice=' + $Event.id
        # The app may be removed or its association changed after delivery.
        # Keep an explicit HTTPS route to the same event on those toasts.
        $actions = '<actions><action content="Otworz w przegladarce" arguments="{0}" activationType="protocol"/></actions>' -f [Security.SecurityElement]::Escape($https)
    }

    # Use protocol activation so Windows can open the URL after this process exits.
    return (
        '<toast activationType="protocol" launch="{0}"><visual><binding template="ToastGeneric"><text>{1}</text><text>{2}</text></binding></visual>{3}</toast>' -f
        [Security.SecurityElement]::Escape($launch), [Security.SecurityElement]::Escape($title), [Security.SecurityElement]::Escape($message), $actions
    )
}

if ($Mode -eq 'Render') {
    New-DevStackToastXml (Read-DevStackNotice)
    return
}

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or
    [Diagnostics.Process]::GetCurrentProcess().SessionId -eq 0) {
    throw 'Run in the signed-in Windows user session.'
}

if ($Mode -eq 'Register') {
    New-Item -Path $registration -Force | Out-Null
    New-ItemProperty -LiteralPath $registration -Name 'DisplayName' -PropertyType String -Value 'DevStack' -Force | Out-Null
    return
}
if ($Mode -eq 'Unregister') {
    if (Test-Path -LiteralPath $registration) {
        try {
            [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]::History.Clear($applicationId)
        } catch {
            Write-Warning 'Windows could not clear DevStack history. Dismiss any leftover DevStack toast in Notification Center.'
        }
        Remove-Item -LiteralPath $registration -Recurse
    }
    $settings = "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Notifications\Settings\$applicationId"
    if (Test-Path -LiteralPath $settings) {
        Remove-Item -LiteralPath $settings -Recurse
    }
    return
}
if (-not (Test-Path -LiteralPath $registration)) {
    throw 'Register DevStack notifications in this Windows account first.'
}

$event = Read-DevStackNotice
$xml = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime]::new()
$xml.LoadXml((New-DevStackToastXml $event))
$toast = [Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType = WindowsRuntime]::new($xml)

# Reuse the same tag for the same journal event without sharing tags across categories.
$hash = [Security.Cryptography.SHA256]::Create()
try {
    $digest = $hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($event.id))
    $toast.Tag = [BitConverter]::ToString($digest, 0, 8).Replace('-', '')
} finally {
    $hash.Dispose()
}
$toast.Group = 'devstack'
$manager = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
$notifier = $manager::CreateToastNotifier($applicationId)
# Reflection preserves the underlying HRESULT on Windows PowerShell 5.1;
# ordinary property access can turn a failed WinRT getter into null.
$type = $notifier.GetType()
$setting = $type.GetProperty('Setting')
$show = $type.GetMethod('Show', [type[]]@($toast.GetType()))
if ($null -eq $setting -or $null -eq $show) {
    throw 'Windows could not expose the notification API.'
}
Assert-DevStackNotificationSetting { $setting.GetValue($notifier, $null) }
$null = $show.Invoke($notifier, [object[]]@($toast))
