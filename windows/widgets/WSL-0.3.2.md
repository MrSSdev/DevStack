# WSL 0.3.2: native-control transport correction and diagnostics

## Evidence and limits

The device log for 0.3.1 contains repeated successful `document-served`,
`state-resource-read` and `metrics-ok` events, but no accepted `hello-active`,
`state-sent` or `state-seen-by-page`. The approved page renders and reopening
works after a host restart, while Start/Stop remain disabled. Read-only resource
fallback is functioning; this does not establish the cause of missing native
messages, nor does it authorize enabling controls without a handshake.

## Narrow change

WebView2 documents `chrome.webview.postMessage(message)` with ONE argument:
https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/javascript/webview#postmessagemessage

Our old code passed a second origin argument, confusing this with the Window
postMessage signature. The browser mock repeated that same assumption, so its
success did not validate the documented WebView2 call. Use the one-argument
form, keep an explicit check of the registered document origin, and keep all
existing provider-side session, client, sequence, authentication, live-state
and Stop-confirmation rules. No parent-window relay or wildcard origin.

Whether removing that second argument resolves this specific host failure is
still a DEVICE TEST, not a fact established by the supplied logs. JavaScript
bindings may handle surplus arguments differently. Do not claim that a mock
proves the host root cause.

## Failure visibility without weakening control

An existing read-only state request carries one enumerated observation in
`X-DevStack-Bridge`: missing, listen-error, origin-mismatch, post-error, posted,
received or connected. Only an explicit allowlist is logged as `bridge-js-*`.
No message body, nonce, token, URL or raw browser exception is logged. The
header never grants command authority and does not trigger extra polling.
`posted` means only that the JS call returned, not that Windows delivered it.
`received` without connection can mean an incoming payload was rejected.

## Unchanged

Approved HTML/CSS, outer corners, collector, telemetry config, StateProbe,
WidgetProvider command policy, COM registration and DevStack rendering remain
unchanged. The brief host loading spinner is not targeted by this change.
Passive reads must never start WSL. The telemetry installer must not be rerun
when metrics already work.

## Validation

The production-page browser test now uses a strict single-argument mock and
checks normal/delayed/throwing/dropped-hello paths plus a missing-bridge case:
resource values remain visible, but controls stay disabled and diagnostic
state is available. Existing visual geometry, theme, action, confirmation,
cache and hidden-state tests remain. This is not the actual Widgets Board.

Install MSIX/CER from one successful run using the bundled Install-Widgets.ps1.
Expected version: 0.3.2.0. First device test is Stop request -> Cancel, which
must not terminate Ubuntu. Only after saving work test confirmed Stop and
Start, then reopen. If still disabled, inspect `bridge-js-*` together with
`hello-*` / `state-*`; do not reinstall Ubuntu or force-enable the button.
