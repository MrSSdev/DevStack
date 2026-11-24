# WSL 0.3.5: restore the accepted transparent dark surface

## User decision, 25 November 2026

After seeing 0.3.4 in both dark and light themes, the user explicitly requested
one appearance change back: the darker host-backed surface from 0.3.3, without
the 8% white overlay introduced in 0.3.4. They accept the brief native spinner
and transient colour changes on opening and do not want further investigation
of these cosmetic loading differences now. The light-theme screenshots show
loading surfaces that differ from the final card; this is recorded evidence,
not a new diagnosis or request to redesign light mode.

## Exact rollback, packaged as a forward update

Restore scripts/Build-WslWeb.py byte-for-byte from 0.3.3 commit
`afefb6c556fb5636f4861c8401296e91a73b467d` (blob
`0139355149558d595ba75563345192a5f9c7a298`). The outer dark rule is again:

```css
html[data-host="windows"][data-theme="dark"] .widget{background:transparent}
```

The comment retained in that generator describes its 0.3.3 origin. The MSIX,
installer version check and versioned page URL are 0.3.5.0 / 0.3.5 so the user
can update in place rather than uninstalling or forcing a package downgrade.
The version bump is not a new visual design.

HTML/body transparency, rounded corners, the approved inner layout, icons,
light theme, JavaScript, control authorization, native bridge, polling and
hidden-state cancellation, telemetry, COM and the DevStack renderer remain
unchanged. No changes to color-scheme or WebView lifecycle are included.

## Verification and installation

Run the existing CI and compare the actual packaged HTML/CSS/JS and CSP with
the 0.3.3 archive. Since the generator and JS are unchanged from that version,
the generated page should be identical. Do not claim device tests from browser
mock tests or claim the spinner has been removed.

Install matching MSIX/CER using the included Install-Widgets.ps1 in
`%USERPROFILE%\Downloads\DevStack-Windows-Widgets-x64`. Do not reinstall the
working Ubuntu telemetry, remove widget pins or reset WSL. The known one-time
post-update stop of WidgetBoard and DevStack.Widgets is acceptable; never add
per-opening resets or hidden polling.

The loading-colour/spinner investigation is deliberately parked by the user.
The second widget is not modified in this release. Public main is unchanged
and PR #2 remains draft; unrelated full sleep/power acceptance is not implied.
