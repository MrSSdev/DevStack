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

function New-DevStackToastXml($Event) {
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
    $launch = 'https://devstack.test/#' + $route + '?notice=' + $Event.id

    # Use protocol activation so Windows can open the URL after this process exits.
    return (
        '<toast activationType="protocol" launch="{0}"><visual><binding template="ToastGeneric"><text>{1}</text><text>{2}</text></binding></visual></toast>' -f
        [Security.SecurityElement]::Escape($launch), [Security.SecurityElement]::Escape($title), [Security.SecurityElement]::Escape($message)
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
