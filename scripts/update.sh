#!/usr/bin/env bash
# Update an existing DevStack checkout and installed application.
# Run as your normal Ubuntu account, NEVER as root.
# Does not re-run the first-install-only scripts/install.sh.
set -Eeuo pipefail
umask 077

windows_local=0
if [[ "${1:-}" == --windows-local-notifications ]]; then
  windows_local=1
  shift
fi
if [[ "$#" -ne 0 ]]; then
  echo 'Usage: bash scripts/update.sh [--windows-local-notifications]' >&2
  exit 2
fi

if [[ "$EUID" -eq 0 ]]; then
  echo 'Run scripts/update.sh as your normal Ubuntu developer account (without sudo).' >&2
  exit 2
fi

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
if [[ "$root" != "$HOME/projects/devstack-source" ]]; then
  echo 'Expected checkout location: ~/projects/devstack-source' >&2
  exit 2
fi
cd "$root"

branch="$(git branch --show-current)"
if [[ "$branch" != main && "$branch" != test/windows-local-notifications && "$branch" != test/windows-component-layout && "$branch" != test/devstack-final-polish && "$branch" != test/devstack-independent-state ]]; then
  echo 'Update supports main or an explicitly verified DevStack test branch.' >&2
  exit 2
fi
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo 'Your tracked files have uncommitted changes. Commit or stash them before updating.' >&2
  exit 2
fi
# The ordinary Ubuntu developer cannot traverse /etc/devstack (0710,
# root:devstackweb); server.json is intentionally 0600. Do NOT check it here:
# the privileged helper validates the protected config after sudo.
if [[ ! -f /opt/devstack/daemon/devstackd.py ||
      ! -f /opt/devstack/public/index.php ]]; then
  echo 'Installed application files are missing from /opt/devstack.' >&2
  echo 'The protected /etc/devstack/server.json is checked later by the root helper.' >&2
  exit 2
fi

echo '[1/5] Verify source code...'
if [[ "$branch" == main ]]; then
  git pull --ff-only
else
  # Test exactly the checked-out, reviewed SHA. No implicit fetch or merge
  # after the user has selected and verified the experimental commit.
  git log -1 --oneline
fi

# The protected marker is the existing opt-in, even if Windows files
# are temporarily missing. Never infer consent from a directory or PWA.
# A sudo/read/validation failure is fatal, not interpreted as "disabled".
if ! local_notification_status="$(sudo bash scripts/enable-windows-local-notifications.sh --status)"; then
  echo 'Could not verify the existing Windows notification transport; no deployment attempted.' >&2
  exit 1
fi
case "$local_notification_status" in
  enabled)
    windows_local=1
    echo '[Windows] Existing local notifications will be updated.'
    ;;
  disabled) ;;
  *)
    echo 'Unexpected local notification status; no deployment attempted.' >&2
    exit 1
    ;;
esac
if [[ "$branch" == test/windows-local-notifications && "$windows_local" -ne 1 ]]; then
  echo 'The verified test branch requires an existing opt-in or --windows-local-notifications.' >&2
  exit 2
fi

echo '[2/5] Running Python and PHP unit tests and security scan...'
python3 -m unittest discover -s tests -v
php tests/php-security.php
if ! php -r 'exit(class_exists(\Dom\HTMLDocument::class) ? 0 : 1);'; then
  echo 'HTML mail preview requires PHP XML/DOM. Install with: sudo apt-get install php8.5-xml' >&2
  exit 1
fi
php tests/php-mail-preview.php
if ! php -r 'exit(extension_loaded("pdo_sqlite") && extension_loaded("mbstring") ? 0 : 1);'; then
  echo 'Notification history requires PHP SQLite and mbstring. Install with: sudo apt-get install php8.5-sqlite3 php8.5-mbstring' >&2
  exit 1
fi
php tests/php-notifications.php
bash scripts/security-scan.sh

echo '[3/5] Checking syntax...'
bash -n scripts/update.sh scripts/apply-update.sh scripts/install-mailpit.sh scripts/enable-windows-local-notifications.sh
php -l backend/public/index.php
php -l backend/security.php
php -l backend/mail-preview.php
php -l backend/notifications.php
php -l scripts/notifications-worker.php
php -l scripts/set-password.php
php -l scripts/configure-auth.php
python3 -m py_compile daemon/devstackd.py daemon/extension_manifest.py daemon/extension_instructions.py daemon/extension_instruction_worker.py daemon/wsl_ca.py daemon/project_frameworks.py daemon/project_isolation_preflight.py daemon/project_pilot.py daemon/project_runtime_identity.py daemon/project_idmap.py daemon/project_mounts.py daemon/project_access.py daemon/project_data_views.py daemon/project_runtime.py daemon/project_http.py daemon/project_lifecycle.py daemon/managed_services.py scripts/list-domains.py scripts/watch-domains.py

echo '[4/5] Building frontend as non-root...'
bash scripts/build-frontend.sh

if [[ "$windows_local" -eq 1 ]]; then
  echo '[Windows] Preparing optional local notifications...'
  # Call Windows as the ordinary developer, never via sudo. Interop must
  # already be available in the signed-in Windows user's running WSL session.
  command -v wslpath >/dev/null || { echo 'WSL Interop required.' >&2; exit 1; }
  ps=/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe
  installer="$(wslpath -w "$root/windows/install-local-notifications.ps1")"
  launcher=/mnt/c/DevStack/LocalNotifications/DevStack.Notifications.exe
  [[ -f "$ps" ]] || { echo 'Windows PowerShell 5.1 not found.' >&2; exit 1; }
  sudo -v
  # Avoid swapping the launcher while the one-shot collector is using it.
  # The existing Linux updater also pauses/restarts its timer; restore the
  # prior timer on failure before it gets to that stage.
  timer_was_active=0
  if systemctl is-active --quiet devstack-notifications.timer; then
    timer_was_active=1
    sudo systemctl stop devstack-notifications.timer
  fi
  restore_timer() {
    if [[ "$timer_was_active" -eq 1 ]]; then
      sudo systemctl start devstack-notifications.timer || true
    fi
  }
  trap restore_timer EXIT
  for ((attempt=0; attempt<50; attempt++)); do
    active="$(systemctl show -p ActiveState --value devstack-notifications.service)"
    [[ "$active" == inactive || "$active" == failed ]] && break
    sleep 1
  done
  [[ "$active" == inactive || "$active" == failed ]] || { echo 'Notification collector is still busy; retry the update.' >&2; exit 1; }
  "$ps" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$installer"
  [[ -f "$launcher" ]] || { echo 'Windows launcher not installed at the expected location.' >&2; exit 1; }
  echo '[Windows] Installed. The transport is not switched until Linux deployment succeeds.'
fi

echo '[5/5] Applying staged update with root-controlled rollback...'
if ! sudo bash scripts/apply-update.sh; then
  if [[ "$windows_local" -eq 1 ]]; then
    echo 'Linux deployment failed. The Windows component may be installed, but the transport marker has not been changed. Retry or uninstall as described in docs/LOCAL-WINDOWS-NOTIFICATIONS.md.' >&2
  fi
  exit 1
fi
if [[ "$windows_local" -eq 1 ]]; then
  # A separate fixed-path, root-only helper publishes the opt-in marker.
  if ! sudo bash scripts/enable-windows-local-notifications.sh; then
    echo 'Linux and Windows code installed, but switching the transport failed. Previous transport retained; retry the enable step.' >&2
    exit 1
  fi
  if ! "$ps" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$installer" -Mode CleanupLegacy; then
    echo 'Local transport enabled; legacy cleanup failed. Inspect the old LocalNotifications directory before manually removing it.' >&2
    exit 1
  fi
  trap - EXIT
  echo 'Windows local notifications enabled at C:\\DevStack\\LocalNotifications.'
fi
echo 'DevStack update complete. Check the browser and an existing disposable project.'
