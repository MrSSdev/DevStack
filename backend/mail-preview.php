<?php
declare(strict_types=1);

// A separate opaque-origin document, not HTML injected into the dashboard.
// Keep this response policy independent of the normal panel's strict CSP.
function devstack_mail_preview_policy(bool $externalImages): string
{
    return "default-src 'none'; sandbox; script-src 'none'; style-src 'unsafe-inline'; img-src data:" .
        ($externalImages ? " https:" : "") .
        "; font-src data:; connect-src 'none'; frame-src 'none'; object-src 'none'; " .
        "form-action 'none'; base-uri 'none'; frame-ancestors https://devstack.test";
}

function devstack_preview_escape(string $value): string
{
    return htmlspecialchars($value, ENT_QUOTES | ENT_SUBSTITUTE | ENT_HTML5, 'UTF-8');
}

// Accept only bounded raster images, not arbitrary attachments or SVG documents.
function devstack_preview_image(string $bytes): ?string
{
    if ($bytes === '' || strlen($bytes) > 2 * 1024 * 1024) return null;
    $info = @getimagesizefromstring($bytes);
    if (!is_array($info) || !in_array($info['mime'] ?? '', [
        'image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/bmp',
    ], true) || $info[0] <= 0 || $info[1] <= 0 || $info[0] * $info[1] > 40_000_000) {
        return null;
    }
    return 'data:' . $info['mime'] . ';base64,' . base64_encode($bytes);
}

/**
 * Preserve email layout/styles in a sandboxed preview. Reconstruct only HTML
 * elements and passive attributes; never forward parsed HTML wholesale.
 * The HTML5 parser handles malformed markup, foreign content and raw-text
 * elements using browser-compatible rules. No remote requests are made here.
 */
function devstack_render_mail_preview(
    string $html, array $parts = [], ?callable $readPart = null, bool $externalImages = false
): string {
    if (strlen($html) > 2 * 1024 * 1024) {
        throw new RuntimeException('HTML wiadomości jest za duży dla podglądu. Otwórz ją w Mailpit.');
    }
    if (!class_exists(\Dom\HTMLDocument::class)) {
        throw new RuntimeException('Podgląd HTML wymaga rozszerzenia PHP 8.5 XML (DOM).');
    }
    $doc = \Dom\HTMLDocument::createFromString($html, LIBXML_NOERROR, 'UTF-8');
    $tags = array_fill_keys(explode(' ',
        'div p span h1 h2 h3 h4 h5 h6 ul ol li strong b em i u s strike small big sub sup blockquote br hr pre code ' .
        'table thead tbody tfoot tr td th caption colgroup col dl dt dd a img center font address abbr bdi bdo wbr ' .
        'header footer main article section aside figure figcaption mark del ins style'
    ), true);
    $void = array_fill_keys(['br', 'hr', 'img', 'col', 'wbr'], true);
    // Legacy HTML email attributes are important for table-based layouts.
    $attrs = explode(' ', 'class id style lang dir title role aria-label aria-hidden align valign width height ' .
        'bgcolor border bordercolor cellpadding cellspacing colspan rowspan color face size hspace vspace ' .
        'nowrap span start type compact');
    $partMap = [];
    foreach ($parts as $part) {
        if (!is_array($part) || !is_string($part['PartID'] ?? null) ||
            !preg_match('~^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$~D', $part['PartID']) ||
            !is_int($part['Size'] ?? null) || $part['Size'] < 1 || $part['Size'] > 2 * 1024 * 1024 ||
            !is_string($part['ContentType'] ?? null) ||
            !in_array(strtolower(trim(explode(';', $part['ContentType'], 2)[0])), [
                'image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/bmp',
            ], true)) continue;
        $cid = $part['ContentID'] ?? '';
        if (is_string($cid) && $cid !== '' && strlen($cid) <= 512) {
            $partMap['cid:' . trim($cid, '<>')] ??= $part['PartID'];
        }
        $filename = $part['FileName'] ?? '';
        if (is_string($filename) && preg_match('~^[A-Za-z0-9_.-]{1,255}$~D', $filename)) {
            $partMap[$filename] ??= $part['PartID'];
        }
    }
    $cachedParts = [];
    $embeddedBytes = 0;
    $source = static function (string $value) use (
        $partMap, $readPart, $externalImages, &$cachedParts, &$embeddedBytes
    ): ?string {
        $value = trim($value);
        if (str_starts_with(strtolower($value), 'cid:')) {
            $value = 'cid:' . trim(rawurldecode(substr($value, 4)), '<>');
        }
        $partId = $partMap[$value] ?? null;
        if (is_string($partId) && $readPart !== null) {
            if (array_key_exists($partId, $cachedParts)) return $cachedParts[$partId];
            if (count($cachedParts) >= 8 || $embeddedBytes >= 4 * 1024 * 1024) return null;
            $cachedParts[$partId] = null;
            try {
                $bytes = $readPart($partId);
                if (is_string($bytes) && $embeddedBytes + strlen($bytes) <= 4 * 1024 * 1024) {
                    $embeddedBytes += strlen($bytes);
                    $cachedParts[$partId] = devstack_preview_image($bytes);
                }
            } catch (Throwable) {
                // An unavailable inline image must not hide the rest of the mail.
            }
            return $cachedParts[$partId];
        }
        if (strlen($value) <= 2_900_000 &&
            preg_match('~^data:image/(?:png|jpeg|gif|webp|bmp);base64,(.+)$~isD', $value, $match)) {
            $bytes = base64_decode($match[1], true);
            return is_string($bytes) ? devstack_preview_image($bytes) : null;
        }
        // Remote images are browser requests only, deliberately opt-in for
        // this message. Do not proxy arbitrary URLs through the PHP process.
        if ($externalImages && preg_match('~^https://[^\x00-\x20\x7f]{1,4096}$~iD', $value)) {
            $url = parse_url($value);
            if (is_array($url) && isset($url['host']) && !isset($url['user']) && !isset($url['pass'])) {
                return $value;
            }
        }
        return null;
    };
    // Repeating the same CID must not inflate the response without bound.
    $imageOutputBytes = 0;
    $boundedSource = static function (string $value) use ($source, &$imageOutputBytes): ?string {
        $result = $source($value);
        if ($result === null || $imageOutputBytes + strlen($result) > 6 * 1024 * 1024) return null;
        $imageOutputBytes += strlen($result);
        return $result;
    };
    $attributes = static function (\Dom\Element $el, string $tag) use ($attrs, $boundedSource): string {
        $out = '';
        foreach ($attrs as $name) {
            if ($el->hasAttribute($name)) {
                $out .= ' ' . $name . '="' . devstack_preview_escape(($el->getAttribute($name) ?? '')) . '"';
            }
        }
        if ($tag === 'a') {
            // Preserve anchor styling but remove href/target/ping/download.
            $out .= ' aria-disabled="true"';
        }
        if ($tag === 'img') {
            $alt = $el->getAttribute('alt') ?? '';
            $out .= ' alt="' . devstack_preview_escape($alt) . '"';
            $src = $boundedSource($el->getAttribute('src') ?? '');
            if ($src !== null) $out .= ' src="' . devstack_preview_escape($src) . '"';
            $out .= ' referrerpolicy="no-referrer"';
        }
        if (in_array($tag, ['body', 'table', 'td', 'th', 'tr'], true) && $el->hasAttribute('background')) {
            $src = $boundedSource($el->getAttribute('background') ?? '');
            if ($src !== null) $out .= ' background="' . devstack_preview_escape($src) . '"';
        }
        return $out;
    };
    $remaining = 20_000;
    $render = static function (\Dom\Node $node, int $depth = 0) use (
        &$render, &$remaining, $tags, $void, $attributes
    ): string {
        if (--$remaining < 0 || $depth > 64) {
            throw new RuntimeException('Zbyt złożona wiadomość. Otwórz ją w Mailpit.');
        }
        if ($node instanceof \Dom\Text) return devstack_preview_escape($node->textContent);
        if (!$node instanceof \Dom\Element || $node->namespaceURI !== 'http://www.w3.org/1999/xhtml') return '';
        $tag = strtolower($node->localName);
        if (!isset($tags[$tag])) return '';
        if ($tag === 'style') {
            $css = $node->textContent;
            // Never serialize raw-text closing tags recovered by a parser.
            if (stripos($css, '</style') !== false) return '';
            $media = $node->getAttribute('media') ?? '';
            return '<style' . ($media !== '' ? ' media="' . devstack_preview_escape($media) . '"' : '') .
                '>' . $css . '</style>';
        }
        $out = '<' . $tag . $attributes($node, $tag) . '>';
        if (isset($void[$tag])) return $out;
        foreach ($node->childNodes as $child) $out .= $render($child, $depth + 1);
        return $out . '</' . $tag . '>';
    };
    $root = $doc->documentElement;
    $head = $doc->head;
    $body = $doc->body;
    $out = '<!doctype html><html' . ($root instanceof \Dom\Element ? $attributes($root, 'html') : '') .
        '><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">' .
        '<style>html{color-scheme:light;background:#fff}a{color:#0000ee;text-decoration:underline}</style>';
    if ($head instanceof \Dom\Element) {
        foreach ($head->childNodes as $child) {
            if ($child instanceof \Dom\Element && $child->localName === 'style') $out .= $render($child);
        }
    }
    $out .= '</head><body' . ($body instanceof \Dom\Element ? $attributes($body, 'body') : '') . '>';
    if ($body instanceof \Dom\Element) {
        foreach ($body->childNodes as $child) $out .= $render($child);
    }
    return $out . '</body></html>';
}
