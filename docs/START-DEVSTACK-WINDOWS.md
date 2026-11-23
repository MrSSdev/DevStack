# Open DevStack on demand from Windows 11

This optional, **per-user** Start-menu shortcut opens the DevStack dashboard after
starting the existing Ubuntu WSL2 distribution. It is independent of the
privileged Windows hosts synchronization task.

**Prerequisites:** DevStack is already installed; Windows hosts and the local CA
have been configured; Ubuntu-26.04 starts normally and systemd services
(`nginx`, `php8.5-fpm`, `mariadb`, `redis-server`, `devstackd`) are enabled.

## Behavior

1. Only when the user launches DevStack, run
   `wsl.exe -d Ubuntu-26.04 -- /usr/bin/true`.
   This starts a stopped distribution but **does not reboot an already-running one**.
2. Poll the locally trusted `https://devstack.test/` endpoint until HTTP 200
   (default 90-second startup window). HTTPS certificate validation remains enabled.
3. Open the panel in the Windows default browser.
4. Exit. The shortcut leaves **no additional resident process** and never
   automatically stops Ubuntu; the hosts watcher remains independently managed.

The shortcut does not run Nginx/systemctl commands, modify services, edit Windows
hosts, change CA trust, or request administrator privileges. On failure, it shows
a Windows dialog rather than silently failing with a hidden PowerShell window.

## 1. Update source code, not the installed DevStack services

Inside the Ubuntu development checkout:

```bash
cd ~/projects/devstack-source
git status --short
git pull --ff-only
```

Review pending local modifications before pulling. **Do not rerun**
`scripts/install.sh`, `scripts/update.sh` or Windows CA setup merely to add
this shortcut.

## 2. Test the launcher while Ubuntu is already running

From an ordinary (not elevated) **Windows PowerShell** console:

```powershell
$source = '\\wsl.localhost\Ubuntu-26.04\home\<ubuntu-user>\projects\devstack-source\windows'
$launcher = Join-Path $source 'open-devstack.ps1'
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $launcher -NoBrowser
$LASTEXITCODE
```

Replace `<ubuntu-user>` with your Linux account, for example `ubuntu`.
The expected output is `DevStack ready: https://devstack.test/` and code 0.
`-NoBrowser` suppresses opening the browser during the first test, but still
performs the full WSL/service readiness check.

## 3. Install the Start-menu shortcut

After the no-browser test succeeds, use an ordinary Windows PowerShell session:

```powershell
$installer = Join-Path $source 'install-devstack-shortcut.ps1'
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $installer
$LASTEXITCODE
```

The installer validates PowerShell syntax and copies the launcher to
`%LOCALAPPDATA%\DevStack\open-devstack.ps1`. It creates
`DevStack.lnk` in **your** Start-menu Programs folder. No scheduled task,
admin rights or system-wide policy changes are required. The
`-ExecutionPolicy Bypass` switch affects only that child PowerShell process.

Find **DevStack** in the Start menu. You may pin it manually.

## 4. Test startup from the stopped state

Save your work in Ubuntu before terminating the distribution.

1. In Windows PowerShell: `wsl --terminate Ubuntu-26.04`.
2. Verify `wsl --list --running` reports no running distributions.
3. Click **DevStack** in the Windows Start menu.
4. Verify the default browser opens `https://devstack.test/`, Ubuntu is running,
   and the existing `DevStack Hosts Event Sync` task is still running.
5. Close the browser; Ubuntu should **remain running**. Never add a background
   idle shutdown action to the shortcut.

The Windows hosts watcher remains independent: opening a Start-menu shortcut is
a user-initiated start, whereas merely logging into Windows or displaying WSL
status must never start Ubuntu.

## Troubleshooting

- Run the installed launcher visibly with
  `powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\DevStack\open-devstack.ps1" -NoBrowser`
  to see diagnostic errors and the process exit code.
- If WSL fails, check `wsl --list --verbose` and your Ubuntu installation.
- If the readiness check times out, inspect
  `wsl -d Ubuntu-26.04 -- systemctl is-active devstackd nginx php8.5-fpm mariadb redis-server`.
  Confirm that Windows `hosts` maps `devstack.test` to `127.0.0.1` and the
  already-approved local CA is trusted.
- Do not bypass TLS validation or reinstall the CA as a troubleshooting shortcut.
- To remove this optional launcher, remove only the per-user
  `DevStack.lnk` from Start-menu Programs and
  `%LOCALAPPDATA%\DevStack\open-devstack.ps1`; leave
  `%ProgramData%\DevStack` and the hosts synchronization task untouched.

**Status:** source code is available for on-device verification. Windows
PowerShell 5.1 execution, the Start-menu shortcut and stopped-to-running
behavior must be smoke-tested on the target workstation before marking this
feature as complete.
