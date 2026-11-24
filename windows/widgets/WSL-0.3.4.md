# WSL 0.3.4: match the initial lighter tile, not the darker WebView backing

## New device evidence

The user's three captures after 0.3.3 distinguish three stages:
1. A light, subtly tinted tile with the Windows spinner, similar to Weather.
2. A darker tile with the same system spinner, before our page is visible.
3. The working approved WSL page on the darker surface.

The previous transparent-fill change did NOT match the initial surface. It
exposed the later WebView backing. This corrects that interpretation; do not
claim the host uses one identical backing throughout its loading sequence.

The user would prefer retaining the current darker palette IF the recurring
native spinner could reliably be removed. No supported way to guarantee that
has been verified, so this candidate takes the user's alternative: match the
initial lighter surface while leaving working controls and lifecycle intact.

## Measured, bounded appearance change

Neutral interior samples in the initial-light capture are RGB (50,50,50), while
the darker pre-page capture has RGB (32,32,32). Six interior 7x7 sample patches
at comparable relative tile positions are consistent with an approximately
8% white layer (small differences are expected: the captures are shifted and
the desktop tint is not identical). The neutral alpha-composite calculation is
32 * 0.92 + 255 * 0.08 = 49.84, rounding to 50, or #323232.

Replace the 0.3.3 dark background rule with:

```css
html[data-host="windows"][data-theme="dark"] .widget{
    background:rgba(255,255,255,0.08)
}
```

This is a chosen appearance match based on two observed surfaces, NOT proof of
the proprietary host's alpha/material formula. It preserves the backing tint
instead of painting a flat sampled gradient. No opacity on the widget or its
children, no backdrop blur, no extra full-card overlay over content. HTML/body
and the outer corners remain transparent. Inner panels, typography, dimensions,
controls and light theme are unchanged. On-device composition must be checked.

## Loading boundary and limits

The production provider already avoids publishing a new URL for every normal
Activate, and restores the registered URL and prior measured data. Reopening
can still cause Windows to load the document again. The documented desktop
provider contract gives us resource responses and content messages, not a
verified control to retain or cover the host's pre-document loading surface.

This candidate does NOT eliminate the native spinner, and CSS cannot guarantee
removing the intermediate darker frame BEFORE our document is painted. Once
our page is visible, both its initial shell and populated state have the same
lighter outer fill. Never describe this as matching all pre-page frames.
No hidden polling, forced active state, persistent browser, Adaptive Card
substitution, or unverified cache/authentication change was introduced.

Reviewed primary references:
- https://learn.microsoft.com/en-us/windows/apps/develop/widgets/web-widget-providers
- https://learn.microsoft.com/en-us/windows/apps/design/widgets/widgets-states-and-ui
- https://learn.microsoft.com/en-us/windows/apps/develop/widgets/widget-providers

## Verification and installation

Run the existing production CSP/browser, provider-policy, Linux metrics,
installer, build and signing checks. Download the actual signed archive; verify
version 0.3.4.0, unchanged JS/telemetry, valid CSP hashes and the single CSS-rule
change. Compare dark and light preview renders with 0.3.3, preserving fully
opaque content pixels and transparent outer corners.

This is a candidate until the ASUS capture confirms the final outer fill.
A short system-spinner stage may remain. Use Install-Widgets.ps1 from the same
MSIX/CER artifact in %USERPROFILE%\Downloads\DevStack-Windows-Widgets-x64.
No telemetry reinstall, no unpinning, no WSL reset. A one-time post-update
restart of WidgetBoard/DevStack.Widgets is the existing deployment workaround,
not a recurring mechanism. Keep the PR draft and public main unchanged.
Do not redesign the DevStack widget before WSL acceptance.
