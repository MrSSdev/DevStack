<?php
declare(strict_types=1);
require_once __DIR__ . '/../backend/mail-preview.php';

function preview_check(bool $ok, string $message): void
{
    if (!$ok) { fwrite(STDERR, "FAIL: {$message}\n"); exit(1); }
}
$png = base64_decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a6uoAAAAASUVORK5CYII=', true);
preview_check(is_string($png) && devstack_preview_image($png) !== null, 'Valid raster image');
preview_check(devstack_preview_image('<svg onload="alert(1)"></svg>') === null, 'Reject SVG document');
$parts = [['PartID'=>'1.2', 'ContentID'=>'logo@test', 'FileName'=>'logo.png', 'Size'=>strlen($png), 'ContentType'=>'image/png']];
$reads = [];
$read = static function (string $part) use (&$reads, $png): string { $reads[] = $part; return $png; };
$input = <<<'HTML'
<!doctype html><html lang="pl"><head><meta http-equiv="refresh" content="0;url=https://example.invalid/redirect">
<base href="https://example.invalid/"><link rel="stylesheet" href="https://example.invalid/style.css">
<style>.card{background:#f8fafc;width:600px}@media(max-width:600px){.card{width:100%}}</style></head>
<body style="background:#eeeeee"><table role="presentation" cellpadding="20" width="100%"><tr><td class="card">
<a href="https://app.test/verify?token=fixture%2Fvalue&amp;signature=abc%3D#finish" target="_top" ping="https://example.invalid/ping" download="bad" style="background:#161616;color:white">Verify</a><a href="javascript:alert(1)">Bad</a>
<img src="cid:logo@test" alt="Logo" width="40"><img src="logo.png" alt="Logo2">
<img src="https://example.invalid/logo.png" alt="Remote" onerror="alert(1)">
<script>alert(1)</script><iframe src="https://example.invalid/frame"></iframe>
<svg><script>alert(1)</script></svg><math><mtext>bad</mtext></math>
<form action="https://example.invalid/form"><input autofocus></form>
</td></tr></table></body></html>
HTML;
$result = devstack_render_mail_preview($input, $parts, $read);
preview_check(str_contains($result, '<html lang="pl"'), 'Preserve document attributes');
preview_check(str_contains($result, '<body style="background:#eeeeee"'), 'Preserve sender body style');
preview_check(str_contains($result, '@media(max-width:600px)'), 'Preserve responsive CSS');
preview_check(str_contains($result, 'cellpadding="20"') && str_contains($result, 'role="presentation"'), 'Preserve table layout');
preview_check(str_contains($result, 'style="background:#161616;color:white"'), 'Preserve button appearance');
preview_check(str_contains($result, 'href="https://app.test/verify?token=fixture%2Fvalue&amp;signature=abc%3D#finish" target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer"'), 'Exact HTTP(S) link and isolated target');
preview_check(str_contains($result, 'aria-disabled="true"'), 'Unsafe link is inactive');
preview_check(substr_count($result, 'src="data:image/png;base64,') === 2 && $reads === ['1.2'], 'CID and filename resolve once to bounded data image');
foreach (['http-equiv=', '<base', '<link', 'target="_top"', 'ping=', 'download=', 'javascript:', 'onerror=', '<script', '<iframe', '<svg', '<math', '<form', '<input', 'https://example.invalid/logo.png'] as $forbidden) {
    preview_check(!str_contains($result, $forbidden), 'Forbidden markup: ' . $forbidden);
}
$withImages = devstack_render_mail_preview($input, $parts, $read, true);
preview_check(str_contains($withImages, 'src="https://example.invalid/logo.png"'), 'Explicit external image opt-in');
preview_check(substr_count($withImages, ' href=') === 1 && !str_contains($withImages, 'onerror='), 'Opt-in does not enable additional navigation or scripts');
foreach (['javascript:alert(1)', 'data:text/html,bad', 'file:///etc/passwd', '//app.test/verify', '/verify', 'https://user:pass@app.test/a', 'https://app.test\\@evil.invalid/a', "https://app.test/a\n", 'https://', 'https://app.test:99999/a'] as $url) {
    preview_check(devstack_preview_link($url) === null, 'Reject unsafe link');
}
preview_check(devstack_preview_link('http://127.0.0.1:8080/verify?token=a%2Fb&signature=c%3D') === 'http://127.0.0.1:8080/verify?token=a%2Fb&signature=c%3D', 'Local HTTP link remains exact');
$unsafe = devstack_render_mail_preview('<img src="javascript:alert(1)"><img src="data:text/html;base64,PHNjcmlwdD4="><img src="https://u:p@example.invalid/a"><img src="http://example.invalid/a">', [], null, true);
preview_check(!str_contains($unsafe, 'src='), 'Reject unsupported and credentialed image URLs');
$missing = devstack_render_mail_preview('<style>p{color:red}</style><img><p>Safe text</p>');
preview_check(str_contains($missing, '<style>p{color:red}</style>') && str_contains($missing, '<img alt=""'), 'Missing optional modern DOM attributes are safe');
$policy = devstack_mail_preview_policy(false);
preview_check(str_contains($policy, "sandbox allow-popups allow-popups-to-escape-sandbox; script-src 'none'; style-src 'unsafe-inline'; img-src data:;"), 'Isolated no-network default policy');
preview_check(str_contains($policy, "form-action 'none'; base-uri 'none'; frame-ancestors https://devstack.test"), 'Restrict forms and framing');
preview_check(str_contains(devstack_mail_preview_policy(true), 'img-src data: https:;'), 'Only explicit preview widens image policy');
echo "Sandboxed mail preview, formatting, bounded images, isolated HTTP(S) links and inactive content: OK\n";
