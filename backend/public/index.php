<?php
declare(strict_types=1);
require_once __DIR__ . '/../security.php';

devstack_boot();
$path = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH);
$path = is_string($path) ? rtrim($path, '/') ?: '/' : '/';
$method = strtoupper((string)($_SERVER['REQUEST_METHOD'] ?? 'GET'));

if (!str_starts_with($path, '/api/')) {
    if ($method !== 'GET' || !is_file(__DIR__ . '/index.html')) {
        http_response_code(503);
        exit('Frontend assets are not built. See installation guide.');
    }
    // Render the saved theme into <html> before any stylesheet or React loads.
    // The helper owns the preference file; PHP never gets filesystem access to root-only state.
    $html = file_get_contents(__DIR__ . '/index.html');
    if (!is_string($html)) {
        http_response_code(503);
        exit('Frontend assets are not readable.');
    }
    $theme = 'light';
    try {
        $preferences = devstack_rpc('get_preferences', []);
        if (($preferences['theme'] ?? null) === 'dark') {
            $theme = 'dark';
        }
    } catch (Throwable $e) {
        // Keep the page usable if the helper is temporarily unavailable.
        error_log('DevStack theme bootstrap: ' . $e->getMessage());
    }
    header('Content-Type: text/html; charset=utf-8');
    echo devstack_theme_html($html, $theme);
    exit;
}

if ($method === 'GET' && $path === '/api/session') {
    devstack_json(200, ['ok' => true, 'authenticated' => devstack_is_authenticated(), 'password_required' => devstack_password_required(), 'csrf' => $_SESSION['csrf']]);
}
if ($method === 'POST') {
    $origin = $_SERVER['HTTP_ORIGIN'] ?? null;
    if ($origin !== null && $origin !== 'https://devstack.test') {
        devstack_json(403, ['ok' => false, 'error' => 'Nieprawidłowe pochodzenie żądania.']);
    }
    if (!devstack_csrf_valid($_SERVER['HTTP_X_CSRF_TOKEN'] ?? null)) {
        devstack_json(403, ['ok' => false, 'error' => 'Nieprawidłowy token CSRF. Odśwież stronę.']);
    }
    $body = devstack_body();
    if ($path === '/api/login') {
        if (!devstack_password_required()) {
            devstack_json(200, ['ok' => true, 'authenticated' => true, 'password_required' => false, 'csrf' => $_SESSION['csrf']]);
        }
        if (!devstack_login_limit()) {
            devstack_json(429, ['ok' => false, 'error' => 'Zbyt wiele prób. Spróbuj za 15 minut.']);
        }
        $password = $body['password'] ?? null;
        if (!is_string($password) || strlen($password) > 300 || !devstack_password_valid($password)) {
            devstack_login_limit(failure: true);
            devstack_json(401, ['ok' => false, 'error' => 'Nieprawidłowe hasło.']);
        }
        devstack_login_limit(reset: true);
        session_regenerate_id(true);
        $_SESSION['authenticated'] = true;
        $_SESSION['last_seen'] = time();
        $_SESSION['csrf'] = bin2hex(random_bytes(32));
        devstack_json(200, ['ok' => true, 'authenticated' => true, 'csrf' => $_SESSION['csrf']]);
    }
    if ($path === '/api/logout') {
        if (!devstack_password_required()) {
            devstack_json(403, ['ok' => false, 'error' => 'W trybie bez hasła wylogowanie nie ma zastosowania.']);
        }
        $_SESSION = [];
        session_regenerate_id(true);
        $_SESSION['csrf'] = bin2hex(random_bytes(32));
        devstack_json(200, ['ok' => true, 'authenticated' => false, 'csrf' => $_SESSION['csrf']]);
    }
}
if (!devstack_is_authenticated()) {
    devstack_json(401, ['ok' => false, 'error' => 'Zaloguj się do DevStack.']);
}
$_SESSION['last_seen'] = time();

try {
    if ($method === 'GET' && ($path === '/api/ca/info' || $path === '/api/ca')) {
        // The user must already be authenticated; this is the public CA cert,
        // NOT the private signing key. Windows trust is never changed here.
        $ca = devstack_rpc('public_ca_certificate', []);
        if ($path === '/api/ca/info') {
            devstack_json(200, ['ok' => true, 'result' => ['sha256' => $ca['sha256']]]);
        }
        $pem = $ca['pem'] ?? null;
        if (!is_string($pem) || strlen($pem) > 16384 ||
            !str_starts_with($pem, "-----BEGIN CERTIFICATE-----\n")) {
            throw new RuntimeException('Brak poprawnego publicznego certyfikatu CA.');
        }
        header('Content-Type: application/pkix-cert');
        header('Content-Disposition: attachment; filename="devstack-rootCA.cer"');
        header('Content-Length: ' . strlen($pem));
        echo $pem;
        exit;
    }
    if ($method === 'GET' && $path === '/api/state') {
        devstack_json(200, ['ok' => true, 'result' => devstack_rpc('status', [])]);
    }
    if ($method === 'POST' && $path === '/api/preferences') {
        $theme = $body['theme'] ?? null;
        if (!is_string($theme) || !in_array($theme, ['light', 'dark'], true) || count($body) !== 1) {
            devstack_json(422, ['ok' => false, 'error' => 'Nieprawidłowa wartość motywu.']);
        }
        devstack_json(200, ['ok' => true, 'result' => devstack_rpc('set_preferences', ['theme' => $theme])]);
    }
    if ($method === 'GET' && $path === '/api/engines/laravel') {
        if (!devstack_password_required()) {
            devstack_json(403, ['ok' => false, 'error' => 'Instalatory wymagają włączonego logowania do panelu.']);
        }
        devstack_json(200, ['ok' => true, 'result' => devstack_rpc('engine_tool_status', ['engine' => 'laravel'])]);
    }
    if ($method === 'POST' && $path === '/api/engines/laravel') {
        if (!devstack_password_required()) {
            devstack_json(403, ['ok' => false, 'error' => 'Instalatory wymagają włączonego logowania do panelu.']);
        }
        if (count($body) !== 1 || !is_string($body['action'] ?? null) ||
            !in_array($body['action'], ['check', 'install-tool', 'update-tool'], true)) {
            devstack_json(422, ['ok' => false, 'error' => 'Nieprawidłowa operacja instalatora.']);
        }
        devstack_json(200, ['ok' => true, 'result' => devstack_rpc('engine_tool_action', [
            'engine' => 'laravel', 'action' => $body['action'],
        ])]);
    }
    if ($method === 'POST' && $path === '/api/projects') {
        $engine = $body['engine'] ?? 'none';
        if (!is_string($engine) || !in_array($engine, ['none', 'laravel'], true)) {
            devstack_json(422, ['ok' => false, 'error' => 'Nieprawidłowy silnik projektu.']);
        }
        if ($engine !== 'none' && !devstack_password_required()) {
            devstack_json(403, ['ok' => false, 'error' => 'Instalatory wymagają włączonego logowania do panelu.']);
        }
        devstack_json(200, ['ok' => true, 'result' => devstack_rpc('create', [
            'slug' => $body['slug'] ?? null,
            'name' => $body['name'] ?? null,
            'domain' => $body['domain'] ?? null,
            'webroot' => $body['webroot'] ?? null,
            'database' => $body['database'] ?? false,
            'https' => $body['https'] ?? true,
            'engine' => $engine,
            'starter_kit' => $body['starter_kit'] ?? 'none',
        ])]);
    }
    if ($method === 'POST' && $path === '/api/projects/test-db') {
        if (count($body) !== 1 || !is_string($body['slug'] ?? null)) {
            devstack_json(422, ['ok' => false, 'error' => 'Nieprawidłowe dane testu MariaDB.']);
        }
        devstack_json(200, ['ok' => true, 'result' => devstack_rpc('test_project_database', ['slug' => $body['slug']])]);
    }
    if ($method === 'POST' && $path === '/api/projects/delete') {
        if (count($body) !== 3 ||
            !is_string($body['slug'] ?? null) ||
            !is_string($body['confirmation'] ?? null) ||
            !is_bool($body['delete_database'] ?? null)) {
            devstack_json(422, ['ok' => false, 'error' => 'Nieprawidłowe dane usuwania projektu.']);
        }
        devstack_json(200, ['ok' => true, 'result' => devstack_rpc('delete_project', [
            'slug' => $body['slug'],
            'confirmation' => $body['confirmation'],
            'delete_database' => $body['delete_database'],
        ])]);
    }
    if ($method === 'POST' && $path === '/api/reveal') {
        $pw = $body['password'] ?? null;
        if (devstack_password_required() && (!is_string($pw) || strlen($pw) > 300 || !devstack_password_valid($pw))) {
            devstack_json(403, ['ok' => false, 'error' => 'Potwierdź hasło panelu.']);
        }
        devstack_json(200, ['ok' => true, 'result' => devstack_rpc('reveal', ['slug' => $body['slug'] ?? null])]);
    }
    if ($method === 'POST' && $path === '/api/config/full') {
        if (devstack_password_required() && !devstack_login_limit()) {
            devstack_json(429, ['ok' => false, 'error' => 'Zbyt wiele prób uwierzytelnienia.']);
        }
        $pw = $body['password'] ?? null;
        if (devstack_password_required()) {
            if (!is_string($pw) || strlen($pw) > 300 || !devstack_password_valid($pw)) {
                devstack_login_limit(failure: true);
                devstack_json(403, ['ok' => false, 'error' => 'Potwierdź hasło panelu, aby zapisać zaawansowaną konfigurację.']);
            }
            devstack_login_limit(reset: true);
        }
        devstack_json(200, ['ok' => true, 'result' => devstack_rpc('update_full_config', [
            'slug' => $body['slug'] ?? null,
            'full_config' => $body['full_config'] ?? null,
        ])]);
    }
    if ($method === 'POST' && $path === '/api/config') {
        devstack_json(200, ['ok' => true, 'result' => devstack_rpc('update_config', [
            'slug' => $body['slug'] ?? null,
            'overrides' => $body['overrides'] ?? null,
            'extra_locations' => $body['extra_locations'] ?? '',
        ])]);
    }
} catch (InvalidArgumentException $e) {
    devstack_json(422, ['ok' => false, 'error' => $e->getMessage()]);
} catch (Throwable $e) {
    error_log('DevStack error: ' . $e->getMessage());
    devstack_json(422, ['ok' => false, 'error' => $e->getMessage()]);
}
devstack_json(404, ['ok' => false, 'error' => 'Nieznana operacja API.']);
