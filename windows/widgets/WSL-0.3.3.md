# WSL 0.3.3: host-backed outer surface; preserve working controls

## Accepted scope

The ASUS owner confirms that 0.3.2 displays measured resources and controls work
after restarting WidgetBoard and DevStack.Widgets once after deployment.
The remaining requests are assessing the brief host loading spinner on reopening
and matching the widget background to the surface visible behind that spinner.
The later instruction supersedes the earlier suggestion of a flat #323232
Weather-like fill. Only the WSL outer background is to change.

## Appearance change

The generated production CSS adds one scoped rule:

```css
html[data-host="windows"][data-theme="dark"] .widget{background:transparent}
```

The HTML and body were already transparent. Removing the outer opaque dark fill
lets the Windows host's existing surface show through; it does NOT set opacity
on the widget or its descendants. Do not guess an alpha value from a composited
screenshot, add a second acrylic simulation, or change inner panels/controls.
The approved source, geometry, text, icons, surfaces, light theme, corners,
JavaScript bridge, provider, resource readings and DevStack renderer are unchanged.
Actual host backing/clipping must still be confirmed on ASUS; a Chromium preview
cannot prove how Windows composites its own surface.

The package/installer and versioned document URL become 0.3.3.0 / 0.3.3, so this
CSS is fetched after deployment. This version change is not a navigation on
every ordinary activation.

## Loading assessment

The desired UX is: load once, retain the shell, then replace values on reopening.
The existing code already restores the registered URL and last measured display
data, and does not publish a new WSL template on each ordinary activation.
The supplied device logs nevertheless have document-served for repeated short
close/open cycles, including while the provider process remains alive. This is
evidence of repeated document loads, not proof of a single host-internal cause.

Microsoft's documented provider APIs cover content URLs, resources and messages;
no documented provider control to keep the host WebView alive or replace its
pre-document loading UI was found. Do not promise eliminating the native spinner.
Do not replace the accepted HTML with an Adaptive Card approximation, add a
resident hidden browser or poll Ubuntu while hidden simply to mask it.

The native loading indicator is unchanged in this narrowly scoped patch. Once
HTML is shown, the existing local display cache and native state messages still
avoid blanking the card just to refresh measurements. A fallback body for a host
without web support is not a documented replacement for the web loading UI.

References checked:
- https://learn.microsoft.com/en-us/windows/apps/develop/widgets/web-widget-providers
- https://learn.microsoft.com/en-us/windows/apps/design/widgets/widgets-states-and-ui
- https://learn.microsoft.com/en-us/windows/apps/develop/widgets/widgets-create-a-template

## Verification and remaining device check

Run existing production CSP/browser, geometry, native bridge, Linux metrics,
installer and Windows MSIX build tests. Inspect the actual packaged CSS and JS:
only the scoped background rule may differ, and script hash must stay unchanged.
Verify transparent outer pixels in dark previews and unchanged inner geometry;
retain the light theme. These tests do not replace an ASUS capture.

Install using matching MSIX/CER and Install-Widgets.ps1 in the established
Downloads/DevStack-Windows-Widgets-x64 folder. No telemetry reinstall, no deletion
of pins, no reset of Windows widget data. If the one-time post-upgrade host reset
is still necessary, reset only WidgetBoard and DevStack.Widgets, not WSL.
Do not claim spinner elimination or full device acceptance. Leave PR draft and
public main untouched; work on the second widget starts after WSL acceptance.
