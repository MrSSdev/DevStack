<?php
declare(strict_types=1);

/** Local-only session boundary for the DevStack web process. */
function devstack_boot(): void
{
    if (!hash_equals('devstack.test', strtolower((string)($_SERVER['HTTP_HOST'] ?? '')))) {
        http_response_code(421);
        exit('Unknown host');
    }
    if (($_SERVER['HTTPS'] ?? '') !== 'on') {
        http_response_code(403);
        exit('HTTPS required');
    }
    header('Cache-Control: no-store, private');
    header('Pragma: no-cache');
    header('X-Content-Type-Options: nosniff');
    header('Referrer-Policy: no-referrer');
    header('X-Frame-Options: DENY');
    header('Permissions-Policy: camera=(), microphone=(), geolocation=()');
    header("Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; font-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'");
    ini_set('session.use_strict_mode', '1');
    ini_set('session.use_only_cookies', '1');
    ini_set('session.gc_maxlifetime', '28800');
    session_name('DEVSTACKSESSID');
    session_set_cookie_params([
        'lifetime' => 0, 'path' => '/', 'secure' => true,
        'httponly' => true, 'samesite' => 'Strict',
    ]);
    session_start();
    if (!isset($_SESSION['csrf']) || !is_string($_SESSION['csrf'])) {
        $_SESSION['csrf'] = bin2hex(random_bytes(32));
    }
}

/**
 * Explicit opt-in for passwordless localhost operation.
 * Missing/unreadable/invalid mode files require authentication (fail closed).
 */
function devstack_password_required(?string $modeFile = null): bool
{
    $mode = @file_get_contents($modeFile ?? '/etc/devstack/auth.mode');
    return !is_string($mode) || trim($mode) !== 'disabled';
}

function devstack_is_authenticated(?bool $passwordRequired = null): bool
{
    if (($passwordRequired ?? devstack_password_required()) === false) return true;
    return ($_SESSION['authenticated'] ?? false) === true &&
        (time() - (int)($_SESSION['last_seen'] ?? 0) < 28800);
}

function devstack_password_valid(string $candidate): bool
{
    $hash = @file_get_contents('/etc/devstack/auth.hash');
    return is_string($hash) && password_verify($candidate, trim($hash));
}

function devstack_csrf_valid(mixed $token): bool
{
    return is_string($token) && isset($_SESSION['csrf']) && is_string($_SESSION['csrf']) &&
        hash_equals($_SESSION['csrf'], $token);
}

/**
 * Encode RPC data as a JSON object even when the PHP array is empty.
 * Python devstackd validates that the "data" member is an object, not [].
 */
function devstack_rpc_wire(string $op, array $data): string
{
    return json_encode(['op' => $op, 'data' => (object) $data], JSON_THROW_ON_ERROR);
}

function devstack_rpc(string $op, array $data): array
{
    $stream = @stream_socket_client('unix:///run/devstack/control.sock', $errno, $errstr, 3.0);
    if ($stream === false) {
        throw new RuntimeException('Pomocnik systemowy DevStack jest niedostępny.');
    }
    stream_set_timeout($stream, 30);
    $wire = devstack_rpc_wire($op, $data);
    if (strlen($wire) > 65536) {
        fclose($stream);
        throw new InvalidArgumentException('Żądanie jest zbyt długie.');
    }
    try {
        if (fwrite($stream, $wire . "\n") !== strlen($wire) + 1) {
            throw new RuntimeException('Błąd połączenia z pomocnikiem.');
        }
        $response = fgets($stream, 131072);
        if (!is_string($response) || !str_ends_with($response, "\n")) {
            throw new RuntimeException('Niepełna odpowiedź pomocnika.');
        }
        $decoded = json_decode($response, true, 16, JSON_THROW_ON_ERROR);
    } finally {
        fclose($stream);
    }
    if (!is_array($decoded)) {
        throw new RuntimeException('Nieprawidłowa odpowiedź pomocnika.');
    }
    if (($decoded['ok'] ?? false) !== true) {
        throw new RuntimeException((string)($decoded['error'] ?? 'Operacja nie powiodła się.'));
    }
    return is_array($decoded['result'] ?? null) ? $decoded['result'] : [];
}

function devstack_json(int $status, array $value): never
{
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode($value, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
    exit;
}

function devstack_body(): array
{
    if ((int)($_SERVER['CONTENT_LENGTH'] ?? 0) > 16384) {
        devstack_json(413, ['ok' => false, 'error' => 'Żądanie jest zbyt duże.']);
    }
    $raw = file_get_contents('php://input', false, null, 0, 16385);
    if (!is_string($raw) || strlen($raw) > 16384) {
        devstack_json(413, ['ok' => false, 'error' => 'Żądanie jest zbyt duże.']);
    }
    try {
        $object = json_decode($raw, false, 16, JSON_THROW_ON_ERROR);
    } catch (JsonException) {
        devstack_json(400, ['ok' => false, 'error' => 'Nieprawidłowy JSON.']);
    }
    if (!is_object($object)) {
        devstack_json(400, ['ok' => false, 'error' => 'Oczekiwano obiektu JSON.']);
    }
    return (array)$object;
}

/** Rate limit independent of browser cookies; local panel, one workstation. */
function devstack_login_limit(bool $failure = false, bool $reset = false): bool
{
    $file = '/var/lib/devstack/web-sessions/login-attempts.json';
    $fp = @fopen($file, 'c+');
    if ($fp === false) {
        // Fail closed if throttling data is inaccessible.
        return false;
    }
    try {
        if (!flock($fp, LOCK_EX)) return false;
        $content = stream_get_contents($fp);
        $times = json_decode($content ?: '[]', true);
        if (!is_array($times)) $times = [];
        $times = array_values(array_filter($times, static fn($n) => is_int($n) && $n > time() - 900));
        $allowed = count($times) < 8;
        if ($reset) $times = [];
        elseif ($failure) $times[] = time();
        rewind($fp);
        ftruncate($fp, 0);
        fwrite($fp, json_encode($times, JSON_THROW_ON_ERROR));
        fflush($fp);
        return $allowed;
    } finally {
        flock($fp, LOCK_UN);
        fclose($fp);
    }
}
