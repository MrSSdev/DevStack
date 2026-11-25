<?php
declare(strict_types=1);
require_once __DIR__ . '/../backend/security.php';

function check(bool $ok, string $message): void {
    if (!$ok) { fwrite(STDERR, "FAIL: {$message}\n"); exit(1); }
}
$_SESSION = ['csrf' => str_repeat('a', 64)];
check(devstack_csrf_valid(str_repeat('a', 64)), 'Valid token');
check(!devstack_csrf_valid(str_repeat('b', 64)), 'Incorrect token');
check(!devstack_csrf_valid(null), 'Missing token');
check(!devstack_csrf_valid(['not' => 'a token']), 'Non-string token');
$_SESSION = ['authenticated' => true, 'last_seen' => time()];
check(devstack_is_authenticated(), 'Fresh session');
$_SESSION['last_seen'] = time() - 28801;
check(!devstack_is_authenticated(), 'Session expiration');
$modeFile = tempnam(sys_get_temp_dir(), 'devstack-auth-');
check(is_string($modeFile), 'Temporary auth-mode test file');
try {
    file_put_contents($modeFile, "required\n");
    check(devstack_password_required($modeFile), 'Password mode enabled');
    file_put_contents($modeFile, "disabled\n");
    check(!devstack_password_required($modeFile), 'Passwordless mode enabled explicitly');
    file_put_contents($modeFile, "invalid\n");
    check(devstack_password_required($modeFile), 'Invalid mode fails closed');
    unlink($modeFile);
    check(devstack_password_required($modeFile), 'Missing mode fails closed');
    check(devstack_is_authenticated(false), 'Passwordless session bypasses login');
    check(!devstack_is_authenticated(true), 'Password mode rejects expired session');
} finally {
    if (is_file($modeFile)) unlink($modeFile);
}
$authMode = tempnam(sys_get_temp_dir(), 'devstack-engine-auth-');
$engineMode = tempnam(sys_get_temp_dir(), 'devstack-engine-consent-');
check(is_string($authMode) && is_string($engineMode), 'Temporary installer policy files');
try {
    file_put_contents($authMode, "disabled\n");
    unlink($engineMode);
    check(!devstack_engine_access_allowed($authMode, $engineMode), 'Missing consent denies passwordless installers');
    foreach (['invalid', 'deny-passwordless', ''] as $value) {
        file_put_contents($engineMode, $value);
        check(!devstack_engine_access_allowed($authMode, $engineMode), 'Invalid/denied consent blocks installers');
    }
    file_put_contents($engineMode, "allow-passwordless\n");
    check(devstack_engine_access_allowed($authMode, $engineMode), 'Explicit installer consent allows passwordless mode');
    check(!devstack_password_required($authMode), 'Installer consent does not enable authentication');
    unlink($engineMode);
    check(!devstack_engine_access_allowed($authMode, $engineMode), 'Revocation blocks passwordless installers');
    file_put_contents($authMode, "required\n");
    check(devstack_engine_access_allowed($authMode, $engineMode), 'Normal authenticated installer access does not need consent');
    check(!devstack_is_authenticated(true), 'Installer policy does not grant an authenticated session');
} finally {
    if (is_file($authMode)) unlink($authMode);
    if (is_file($engineMode)) unlink($engineMode);
}
$statusRpc = json_decode(devstack_rpc_wire('status', []));
check(is_object($statusRpc), 'RPC request is a JSON object');
check(is_object($statusRpc->data), 'Empty PHP array encoded as JSON object');
check($statusRpc->op === 'status', 'RPC operation is preserved');
$createRpc = json_decode(devstack_rpc_wire('create', ['slug' => 'demo']));
check(is_object($createRpc->data) && $createRpc->data->slug === 'demo', 'Non-empty RPC data is preserved');
// First-paint HTML must use the server theme before React renders.
$htmlTemplate = '<html class="__DEVSTACK_THEME_CLASS__"><head><meta name="color-scheme" content="__DEVSTACK_SCHEME__"><meta name="theme-color" content="__DEVSTACK_THEME_COLOR__"></head></html>';
$darkHtml = devstack_theme_html($htmlTemplate, 'dark');
check(str_contains($darkHtml, 'class="dark"'), 'Dark class rendered server-side');
check(str_contains($darkHtml, 'name="color-scheme" content="dark"'), 'Dark native color scheme');
check(str_contains($darkHtml, 'content="#1c2028"'), 'Dark browser theme color');
check(!str_contains($darkHtml, '__DEVSTACK_'), 'No unresolved placeholders in dark HTML');
$lightHtml = devstack_theme_html($htmlTemplate, 'light');
check(str_contains($lightHtml, 'class="light"'), 'Light class rendered server-side');
check(str_contains($lightHtml, 'name="color-scheme" content="light"'), 'Light native color scheme');
check(str_contains($lightHtml, 'content="#f8fafc"'), 'Light browser theme color');
check(str_contains(devstack_theme_html($htmlTemplate, 'unrecognized'), 'class="light"'), 'Unknown theme fails to light');
try {
    devstack_theme_html('<html></html>', 'dark');
    check(false, 'Missing placeholder must fail');
} catch (RuntimeException $e) {
    check(str_contains($e->getMessage(), 'placeholder'), 'Missing placeholder detected');
}
$mailId = str_repeat('A', 22);
check(devstack_mailpit_target('list', []) === ['GET', '/messages?start=0&limit=40', null], 'Mailpit fixed list target');
check(devstack_mailpit_target('list', ['query' => 'from:hello@example.test&limit=500', 'start' => '40'])
    === ['GET', '/search?query=from%3Ahello%40example.test%26limit%3D500&start=40&limit=40', null], 'Mail search is encoded');
check(devstack_mailpit_target('message', ['id' => $mailId]) === ['GET', '/message/' . $mailId, null], 'Fixed message target');
check(devstack_mailpit_target('attachment', ['id' => $mailId, 'part' => '1.2'])
    === ['GET', '/message/' . $mailId . '/part/1.2', null], 'Attachment target');
$delete = devstack_mailpit_target('delete', ['id' => $mailId]);
check($delete[0] === 'DELETE' && json_decode($delete[2], true)['IDs'] === [$mailId], 'Delete exactly one message');
foreach ([
    ['delete', []], ['delete', ['id' => 'latest']], ['delete', ['id' => [$mailId]]],
    ['message', ['id' => '../messages']], ['message', ['id' => 'http://evil.test']],
    ['attachment', ['id' => $mailId, 'part' => '../raw']],
    ['list', ['start' => '1&limit=500']], ['list', ['query' => ['bad']]],
    ['list', ['start' => '501']], ['send', ['id' => $mailId]],
] as [$operation, $data]) {
    try {
        devstack_mailpit_target($operation, $data);
        check(false, 'Unsafe Mailpit target rejected');
    } catch (InvalidArgumentException $e) {
        check(true, 'Unsafe Mailpit target rejected');
    }
}
echo "PHP session/CSRF and Mailpit target tests: OK\n";
