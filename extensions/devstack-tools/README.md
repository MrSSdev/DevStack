# DevStack Tools

Optional, independently managed tools for local development and future agent integrations. Version 0.1.0 contains Playwright Test and its built-in agent CLI (1.65.1). No agents, container engine, new daemon, WSL autostart, project permission changes, PHP-FPM changes or core update are required. The core installer/updater does not install this extension.

## Install / update

In Ubuntu WSL, from a reviewed DevStack checkout, as the developer (Node.js 22+ and npm required):

~~~bash
cd ~/projects/devstack-source
bash extensions/devstack-tools/install.sh --with-deps
~~~

The explicit --with-deps option lets the upstream Playwright installer install missing system libraries via sudo. npm and browser downloads run as the developer; only publishing the installation uses sudo. The relocated candidate is smoke-tested as the developer before activation. Playwright downloads the full Chromium-family browser with --no-shell, not a second headless shell. Browser revisions follow the pinned package version.

For extension-only updates after reviewing and pulling the new source:

~~~bash
bash extensions/devstack-tools/update.sh
~~~

No core update, frontend build or service restart. An identical build is a no-op; pass --with-deps if new libraries are needed, or --force for repair. Close active Playwright tests and CLI sessions before updating; an active launcher or detected browser/CLI process blocks replacement. The previous installation remains active if the candidate fails. Only one release is retained after successful replacement (temporarily more disk space is needed during staging).

## Layout

~~~text
/opt/devstack-tools/
  bin/          -> current/bin
  playwright/   -> current/playwright
  browsers/     -> current/browsers
  current       -> releases/release-...
  releases/     root-owned, one active package/browser release
~~~

Do not modify the managed directories as root outside the installer. Tools are read-only to ordinary users. Project code, browser profiles, sessions and test reports remain outside the installation. No project gets its own downloaded Chromium copy.

## Use from a project

~~~bash
export PATH="/opt/devstack-tools/bin:$PATH"
playwright --version
playwright test
playwright-cli -s=my-project open https://example.test
playwright-cli -s=my-project snapshot
playwright-cli -s=my-project close
~~~

The PATH change is for this terminal only. No shell profile is modified. Use your actual registered local domain. A project may keep its tests under version control. For central-only installation, CommonJS (.cjs) test/config files can import @playwright/test via the launcher's NODE_PATH; it is not a general ESM module-resolution mechanism. A project that already installs its own Playwright package should keep the versions compatible or use its own test runner. For the shared full browser, configure use: { channel: 'chromium' }; omit the default separate headless shell. Keep normal HTTPS certificate validation and use the developer's trusted local CA. Do not copy CA private keys or disable certificate checks.

Example playwright.config.cjs:

~~~js
const { defineConfig } = require('@playwright/test');
module.exports = defineConfig({
  testDir: './tests/e2e',
  use: {
    baseURL: 'https://example.test',
    channel: 'chromium',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure'
  },
  reporter: 'html'
});
~~~

The built-in CLI uses the same package and shared browser binaries. Its named sessions and temporary/persistent profiles are managed by Playwright under the invoking user's own account; explicitly close sessions after work. Reports, snapshots and traces may contain sensitive application data; do not commit them. No private browser profile is configured or attached.

## Remove

~~~bash
bash extensions/devstack-tools/uninstall.sh --confirm
~~~

Removes only the managed /opt/devstack-tools installation. Does not remove user profiles, reports, application files, OS libraries or other Playwright installations.

## Security / scope

This is tooling, not an AI isolation boundary. Agents running as the same Linux user can still access that user's resources. No agent has been installed or granted access to projects. A later project-access feature must enforce separate execution identities and application runtime isolation. Do not give agents sudo or access to the privileged DevStack control socket. Only the trusted developer should install/update tools from reviewed source. The root publication helper must not be added to passwordless agent sudoers. The install relies on the npm registry and upstream Playwright browser downloads over HTTPS; the exact version chain is pinned and the generated npm integrity lock is retained in the installed release.

## CI

The independent DevStack Tools workflow runs when extension files or its workflow change. Core CI and Windows push-only workflows ignore extension-only changes; a mixed change still triggers normal core checks. Existing component-specific filters and manual core workflow dispatch remain intact. CI uses a disposable Ubuntu runner, with a real relocated browser, central Test and CLI smoke, update/rollback and uninstall. Local WSL acceptance is separate.
