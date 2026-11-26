<?php
declare(strict_types=1);
require_once __DIR__ . '/../security.php';
require_once __DIR__ . '/../notifications.php';
require_once __DIR__ . '/../push-helper.php';

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
        try { $notices=new DevStackNotices(); $notices->leave(DevStackNotices::client($_SERVER['HTTP_X_DEVSTACK_CLIENT']??null)); $notices->unsubscribe(); } catch (Throwable) {}
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
        $state=devstack_rpc('status', []);
        try {
            $notices=new DevStackNotices();
            $client=DevStackNotices::client($_SERVER['HTTP_X_DEVSTACK_CLIENT']??null);
            $notices->touch($client);
            $state['engine_notice']=$notices->observeJob(is_array($state['engine_job']??null)?$state['engine_job']:null,$client);
        } catch (Throwable) { error_log('DevStack notification journal unavailable.'); }
        devstack_json(200, ['ok' => true, 'result' => $state]);
    }
    if ($method === 'POST' && $path === '/api/notifications/poll') {
        $client=DevStackNotices::client($body['client']??null);
        if ($client==='') throw new InvalidArgumentException('Nieprawidłowy klient powiadomień.');
        $store=new DevStackNotices();
        devstack_json(200,['ok'=>true,'result'=>$store->poll($client)]);
    }
    if ($method === 'POST' && $path === '/api/notifications/leave') {
        (new DevStackNotices())->leave(DevStackNotices::client($body['client']??null));
        devstack_json(200,['ok'=>true,'result'=>[]]);
    }
    if ($method === 'POST' && $path === '/api/notifications/ack') {
        (new DevStackNotices())->acknowledge(is_string($body['id']??null)?$body['id']:'',DevStackNotices::client($body['client']??null));
        devstack_json(200,['ok'=>true,'result'=>[]]);
    }
    if ($method === 'POST' && $path === '/api/notifications/manage') {
        $action=$body['action']??null;
        if (!is_string($action)) throw new InvalidArgumentException('Nieznana operacja.');
        (new DevStackNotices())->mutate($action,$body['id']??null);
        devstack_json(200,['ok'=>true,'result'=>[]]);
    }
    if ($method === 'POST' && $path === '/api/notifications/categories') {
        (new DevStackNotices())->setCategories($body['categories']??null);
        devstack_json(200,['ok'=>true,'result'=>[]]);
    }
    if ($method === 'POST' && $path === '/api/notifications/push-key') {
        session_write_close();
        $key=devstack_push_helper('public');
        if (($key['ok']??false)!==true||!is_string($key['key']??null)) {
            throw new RuntimeException('Web Push wymaga pakietu python3-pywebpush w Ubuntu.');
        }
        devstack_json(200,['ok'=>true,'result'=>['key'=>$key['key']]]);
    }
    if ($method === 'POST' && $path === '/api/notifications/subscribe') {
        (new DevStackNotices())->subscribe($body['subscription']??null);
        devstack_json(200,['ok'=>true,'result'=>[]]);
    }
    if ($method === 'POST' && $path === '/api/notifications/unsubscribe') {
        (new DevStackNotices())->unsubscribe();
        devstack_json(200,['ok'=>true,'result'=>[]]);
    }
    // These routes run after the existing session and POST CSRF checks.
    // JSON and attachments retain the ordinary non-frameable policy. Only
    // the dedicated preview response is frameable, with an opaque sandbox.
    if ($method === 'GET' && $path === '/api/mail/messages') {
        devstack_json(200, ['ok' => true, 'result' => devstack_mailpit_request('list', $_GET)]);
    }
    if ($method === 'GET' && preg_match('~^/api/mail/message/([A-Za-z0-9]{22})/preview$~D', $path, $mailMatch)) {
        require_once __DIR__ . '/../mail-preview.php';
        $externalImages = ($_GET['images'] ?? null) === '1';
        // Release the PHP session lock before reading optional inline parts.
        session_write_close();
        try {
            $id = $mailMatch[1];
            $message = devstack_mailpit_request('message', ['id' => $id]);
            if (!is_array($message) || ($message['ID'] ?? null) !== $id || !is_string($message['HTML'] ?? null)) {
                throw new RuntimeException('Brak podglądu HTML.');
            }
            $parts = array_merge(is_array($message['Inline'] ?? null) ? $message['Inline'] : [],
                is_array($message['Attachments'] ?? null) ? $message['Attachments'] : []);
            $preview = devstack_render_mail_preview($message['HTML'], $parts,
                static fn(string $part): array|string => devstack_mailpit_request('attachment', ['id' => $id, 'part' => $part]),
                $externalImages);
        } catch (Throwable $e) {
            http_response_code(422);
            $preview = '<!doctype html><html><meta charset="utf-8"><p>Nie można wyświetlić HTML. Otwórz wiadomość w Mailpit.</p></html>';
        }
        header('Content-Type: text/html; charset=utf-8');
        header('X-Frame-Options: SAMEORIGIN');
        header('Content-Security-Policy: ' . devstack_mail_preview_policy($externalImages));
        echo $preview;
        exit;
    }
    if ($method === 'GET' && preg_match('~^/api/mail/message/([A-Za-z0-9]{22})$~D', $path, $mailMatch)) {
        devstack_json(200, ['ok' => true, 'result' => devstack_mailpit_request('message', ['id' => $mailMatch[1]])]);
    }
    if ($method === 'GET' && preg_match('~^/api/mail/message/([A-Za-z0-9]{22})/part/([A-Za-z0-9][A-Za-z0-9._-]{0,63})$~D', $path, $mailMatch)) {
        $attachment = devstack_mailpit_request('attachment', ['id' => $mailMatch[1], 'part' => $mailMatch[2]]);
        header('Content-Type: application/octet-stream');
        header('Content-Disposition: attachment');
        header('Content-Length: ' . strlen($attachment));
        echo $attachment;
        exit;
    }
    if ($method === 'POST' && $path === '/api/mail/delete') {
        if (count($body) !== 1) throw new InvalidArgumentException('Wybierz jedną wiadomość do usunięcia.');
        devstack_json(200, ['ok' => true, 'result' => devstack_notice_operation($path,$body,devstack_mailpit_request('delete', $body))]);
    }
    if ($method === 'POST' && $path === '/api/mail/delete-many') {
        devstack_json(200, ['ok' => true, 'result' => devstack_notice_operation($path,$body,devstack_mailpit_request('delete-many', $body))]);
    }
    if ($method === 'POST' && $path === '/api/mail/clear') {
        devstack_json(200, ['ok' => true, 'result' => devstack_notice_operation($path,$body,devstack_mailpit_request('clear', $body))]);
    }
    if ($method === 'POST' && $path === '/api/services/control') {
        devstack_json(200, ['ok' => true, 'result' => devstack_notice_operation($path,$body,devstack_rpc('service_control', $body))]);
    }
    if ($method === 'POST' && $path === '/api/widget/services') {
        devstack_json(200, ['ok' => true, 'result' => devstack_notice_operation($path,$body,devstack_rpc('set_widget_services', $body))]);
    }
    if ($method === 'POST' && $path === '/api/preferences') {
        $theme = $body['theme'] ?? null;
        if (!is_string($theme) || !in_array($theme, ['light', 'dark'], true) || count($body) !== 1) {
            devstack_json(422, ['ok' => false, 'error' => 'Nieprawidłowa wartość motywu.']);
        }
        devstack_json(200, ['ok' => true, 'result' => devstack_notice_operation($path,$body,devstack_rpc('set_preferences', ['theme' => $theme]))]);
    }
    $engineRoute = preg_match('~^/api/engines/([a-z][a-z0-9-]{1,39})$~D', $path, $engineMatch) === 1;
    if ($method === 'GET' && $engineRoute) {
        if (!devstack_engine_access_allowed()) {
            devstack_json(403, ['ok' => false, 'error' => 'Instalatory wymagają logowania lub jednorazowej zgody administratora na pracę bez hasła.']);
        }
        devstack_json(200, ['ok' => true, 'result' => devstack_rpc('engine_tool_status', ['engine' => $engineMatch[1]])]);
    }
    if ($method === 'POST' && $engineRoute) {
        if (!devstack_engine_access_allowed()) {
            devstack_json(403, ['ok' => false, 'error' => 'Instalatory wymagają logowania lub jednorazowej zgody administratora na pracę bez hasła.']);
        }
        if (count($body) !== 1 || !is_string($body['action'] ?? null) ||
            !in_array($body['action'], ['check', 'install-tool', 'update-tool'], true)) {
            devstack_json(422, ['ok' => false, 'error' => 'Nieprawidłowa operacja instalatora.']);
        }
        devstack_json(200, ['ok' => true, 'result' => devstack_notice_operation($path,$body,devstack_rpc('engine_tool_action', [
            'engine' => $engineMatch[1], 'action' => $body['action'],
        ]))]);
    }
    if ($method === 'POST' && $path === '/api/projects') {
        $engine = $body['engine'] ?? 'none';
        if (!is_string($engine) || !preg_match('~^[a-z][a-z0-9-]{1,39}$~D', $engine)) {
            devstack_json(422, ['ok' => false, 'error' => 'Nieprawidłowy framework.']);
        }
        if ($engine !== 'none' && !devstack_engine_access_allowed()) {
            devstack_json(403, ['ok' => false, 'error' => 'Instalatory wymagają logowania lub jednorazowej zgody administratora na pracę bez hasła.']);
        }
        devstack_json(200, ['ok' => true, 'result' => devstack_notice_operation($path,$body,devstack_rpc('create', [
            'slug' => $body['slug'] ?? null,
            'name' => $body['name'] ?? null,
            'domain' => $body['domain'] ?? null,
            'webroot' => $body['webroot'] ?? null,
            'database' => $body['database'] ?? false,
            'https' => $body['https'] ?? true,
            'local_mail' => $body['local_mail'] ?? false,
            'engine' => $engine,
            'starter_kit' => $body['starter_kit'] ?? 'none',
            'installer_options' => is_array($body['installer_options'] ?? []) && empty($body['installer_options'])
                ? (object) [] : ($body['installer_options'] ?? (object) []),
        ]))]);
    }
    if ($method === 'POST' && $path === '/api/projects/test-db') {
        if (count($body) !== 1 || !is_string($body['slug'] ?? null)) {
            devstack_json(422, ['ok' => false, 'error' => 'Nieprawidłowe dane testu MariaDB.']);
        }
        devstack_json(200, ['ok' => true, 'result' => devstack_notice_operation($path,$body,devstack_rpc('test_project_database', ['slug' => $body['slug']]))]);
    }
    if ($method === 'POST' && $path === '/api/projects/delete') {
        if (count($body) !== 3 ||
            !is_string($body['slug'] ?? null) ||
            !is_string($body['confirmation'] ?? null) ||
            !is_bool($body['delete_database'] ?? null)) {
            devstack_json(422, ['ok' => false, 'error' => 'Nieprawidłowe dane usuwania projektu.']);
        }
        devstack_json(200, ['ok' => true, 'result' => devstack_notice_operation($path,$body,devstack_rpc('delete_project', [
            'slug' => $body['slug'],
            'confirmation' => $body['confirmation'],
            'delete_database' => $body['delete_database'],
        ]))]);
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
        devstack_json(200, ['ok' => true, 'result' => devstack_notice_operation($path,$body,devstack_rpc('update_full_config', [
            'slug' => $body['slug'] ?? null,
            'full_config' => $body['full_config'] ?? null,
        ]))]);
    }
    if ($method === 'POST' && $path === '/api/config') {
        if (array_key_exists('rules', $body)) {
            if (count($body) !== 2 || !array_key_exists('slug', $body)) {
                throw new InvalidArgumentException('Nieprawidłowe pola konfiguracji Nginx.');
            }
            devstack_json(200, ['ok' => true, 'result' => devstack_notice_operation($path,$body,devstack_rpc('update_config', [
                'slug' => $body['slug'],
                'rules' => $body['rules'],
            ]))]);
        }
        devstack_json(200, ['ok' => true, 'result' => devstack_notice_operation($path,$body,devstack_rpc('update_config', [
            'slug' => $body['slug'] ?? null,
            'overrides' => $body['overrides'] ?? null,
            'extra_locations' => $body['extra_locations'] ?? '',
        ]))]);
    }
} catch (InvalidArgumentException $e) {
    devstack_json(422, ['ok' => false, 'error' => $e->getMessage()]);
} catch (Throwable $e) {
    $notice=[];
    if ($method==='POST' && !str_starts_with($path,'/api/notifications/') && !str_starts_with($path,'/api/engines/') && !($path==='/api/projects' && ($body['engine']??'none')!=='none')) {
        $notice=devstack_notice_operation($path,$body??[],[],false);
    }
    error_log('DevStack error: ' . $e->getMessage());
    devstack_json(422, ['ok' => false, 'error' => $e->getMessage(), ...$notice]);
}
devstack_json(404, ['ok' => false, 'error' => 'Nieznana operacja API.']);
