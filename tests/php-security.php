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
echo "PHP session/CSRF tests: OK\n";
