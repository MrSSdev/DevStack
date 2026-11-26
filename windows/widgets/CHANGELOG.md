## 0.5.2.0 — protected Windows layout

- Move configuration, cache and diagnostics to the administrator-provisioned private Widgets directory; no legacy path fallback.
- Preserve existing credentials and data through ordinary-user migration; retain old trees until acceptance.
- Bundle the root/Hosts migration and versioned MSIX installer. No change to widget visuals or Linux integrations.

## 0.5.1.0 — widget update finalization

- Keep immediate verified deployment reset, and perform a second reset after the first actual Win+W initialization, guided by an explicit user confirmation.
- Optional `Install-Widgets.ps1 -UpdateServiceIntegration` updates the service bridge before finalizing; preserves the web dashboard, WSL telemetry and lifecycle.
- Standalone `Complete-WidgetUpdate.ps1` can finish deferred setup without reinstalling. No persistent watcher or guessed delay. Widget UI and provider logic unchanged; local shell acceptance remains necessary.

## 0.5.0.0 — managed service selection

- Dashboard lists all installed registered services; Start / Stop / Restart are web controls.
- Widget reads saved service visibility and renders selected tiles dynamically. Legacy four-tile layout, restart confirmation and WSL controls retained. More than four selected tiles scroll. No new background WSL wake/poll behavior.
- Update Linux first, then MSIX and service integration with `-SkipFrontend`. Old v1 read clients remain supported.

# Widget release notes

For setup and updates, see the [widget guide](README.md).

## 0.4.2.0

- Resource detail dismissal by outside click.
- Service health verification and automatic recovery-notice expiry.
- Automatic widget process reset after verified package deployment.

## 0.4.1.0

- Service restart confirmation and inline progress.
- Verified restart results with 30-second success notices.

## 0.4.0.0

- Service status, response times, memory and CPU details.
- Confirmed service restarts, project count and dashboard shortcuts.

## 0.3.x

- Ubuntu resource readings, native Start/Stop controls and packaged web interface.
- Light and dark themes.

## 0.2.x and earlier

- Two separate Windows widgets and service status monitoring.
