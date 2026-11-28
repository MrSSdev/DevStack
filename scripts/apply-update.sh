#!/usr/bin/env bash
# Root-only second stage for scripts/update.sh. Do not run by itself.
# Stages files, backs up the previous code, verifies syntax, swaps directories,
# checks live services and automatically restores the old code on failure.
# Preserves project/state data. The known legacy helper unit is independently
# corrected so a code rollback cannot start an optional database service.
set -Eeuo pipefail
umask 077

if [[ "$EUID" -ne 0 ]] || [[ ! -v SUDO_USER ]] || [[ "$SUDO_USER" == root ]]; then
  echo 'Use the non-root entry point: bash scripts/update.sh' >&2
  exit 2
fi
dev_user="$SUDO_USER"
if [[ ! "$dev_user" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]]; then
  echo 'Unexpected Ubuntu developer username.' >&2
  exit 2
fi

dev_home="$(getent passwd "$dev_user" | cut -d: -f6)"
src="$dev_home/projects/devstack-source"
if [[ "$dev_home" != "/home/$dev_user" || "$(pwd -P)" != "$src" || -L "$src" ]]; then
  echo 'Root update helper must run from the canonical Ubuntu developer checkout.' >&2
  exit 2
fi
if [[ ! -f /etc/devstack/server.json || ! -d /opt/devstack || -L /opt/devstack ||
      ! -f /opt/devstack/daemon/devstackd.py || ! -f /opt/devstack/public/index.php ]]; then
  echo 'Existing DevStack installation is missing or unsafe; refusing update.' >&2
  exit 2
fi

python3 - "$dev_user" "$dev_home" <<'PY'
import json
import pathlib
import sys
cfg = json.loads(pathlib.Path('/etc/devstack/server.json').read_text())
if cfg.get('dev_user') != sys.argv[1] or cfg.get('projects_dir') != sys.argv[2] + '/projects':
    sys.exit('DevStack configured owner/project root does not match the updating user.')
PY

if [[ -L "$src/framework-installers" || -L "$src/framework-installers/laravel" ]]; then
  echo 'Unsafe adapter source directory.' >&2
  exit 1
fi

for file in backend/public/index.php backend/public/index.html backend/public/sw.js backend/public/manifest.webmanifest backend/public/pwa/icon-192.png backend/public/pwa/icon-512.png backend/public/pwa/devstack.svg backend/security.php backend/mail-preview.php backend/notifications.php \
    daemon/devstackd.py daemon/wsl_ca.py daemon/project_frameworks.py daemon/project_isolation_preflight.py daemon/project_pilot.py daemon/project_runtime_identity.py daemon/project_idmap.py daemon/project_mounts.py daemon/project_access.py daemon/project_data_views.py daemon/project_runtime.py daemon/project_http.py daemon/project_lifecycle.py daemon/managed_services.py daemon/service-catalog.json framework-installers/README.md framework-installers/laravel/README.md framework-installers/catalog.json framework-installers/common.py framework-installers/registry.py framework-installers/runner.py framework-installers/laravel/__init__.py framework-installers/laravel/adapter.py scripts/list-domains.py scripts/watch-domains.py \
    scripts/set-password.php scripts/configure-auth.php scripts/notifications-worker.php scripts/devstack-notifications.service scripts/devstack-notifications.timer scripts/devstackd.service; do
  if [[ ! -f "$src/$file" || -L "$src/$file" ]]; then
    echo "Missing or unsafe source file: $file" >&2
    exit 1
  fi
done
if [[ ! -d "$src/backend/public/assets" || -L "$src/backend/public/assets" ]]; then
  echo 'Frontend is not built or assets directory is unsafe.' >&2
  exit 1
fi
if [[ -n "$(find "$src/backend/public/assets" -type l -print -quit)" ]]; then
  echo 'Refusing symlinks inside frontend build assets.' >&2
  exit 1
fi

if [[ -L /var/backups/devstack ]]; then
  echo 'Unsafe backup directory symlink.' >&2
  exit 1
fi
install -d -m 0700 -o root -g root /var/backups/devstack
backup_dir="$(mktemp -d /var/backups/devstack/update-XXXXXXXX)"
stage_dir="$(mktemp -d /opt/.devstack-stage.XXXXXXXX)"
prior_dir="$stage_dir.previous"
old_moved=0
notice_units_created=0
core_unit_changed=0
unit_tmp=''
notice_timer_was_active=0
if systemctl is-active --quiet devstack-notifications.timer; then notice_timer_was_active=1; fi

# Serialize code-directory swaps with the installed CLI and registry writer.
project_lock=/var/lib/devstack/project-operations.lock
if [[ -L "$project_lock" || ( -e "$project_lock" && ! -f "$project_lock" ) ]]; then
  echo 'Unsafe project operations lock.' >&2
  exit 1
fi
exec {project_lock_fd}<>"$project_lock"

rollback() {
  local status="$1"
  trap - ERR INT TERM
  set +e
  if [[ -n "$unit_tmp" ]]; then rm -f -- "$unit_tmp"; fi
  if [[ "$core_unit_changed" -eq 1 ]]; then
    echo 'The compatible core-unit dependency correction is retained; the prior unit is in the protected backup.' >&2
  fi
  if [[ "$notice_units_created" -eq 1 ]]; then
    systemctl disable --now devstack-notifications.timer
    systemctl stop devstack-notifications.service
    rm -f -- /etc/systemd/system/devstack-notifications.service /etc/systemd/system/devstack-notifications.timer
    systemctl daemon-reload
  fi
  if [[ "$old_moved" -eq 1 && -d "$prior_dir" ]]; then
    echo 'UPDATE FAILED — restoring previous installed application...' >&2
    flock -x "$project_lock_fd"
    if [[ -e /opt/devstack ]]; then
      mv -- /opt/devstack "$stage_dir.failed"
    fi
    mv -- "$prior_dir" /opt/devstack
    flock -u "$project_lock_fd"
    if ! systemctl restart devstackd || ! systemctl reload php8.5-fpm; then
      echo 'CRITICAL: previous-code service restart failed; inspect the protected backup.' >&2
    fi
    if [[ "$notice_timer_was_active" -eq 1 ]]; then systemctl start devstack-notifications.timer; fi
    if [[ ! -d /opt/devstack ]]; then
      echo 'CRITICAL: automatic rollback failed; restore from protected backup!' >&2
    fi
  fi
  if [[ "$notice_timer_was_active" -eq 1 ]]; then systemctl start devstack-notifications.timer; fi
  if [[ -d "$stage_dir" ]]; then rm -rf -- "$stage_dir"; fi
  if [[ -d "$stage_dir.failed" ]]; then rm -rf -- "$stage_dir.failed"; fi
  echo "Previous-code backup: $backup_dir/installed" >&2
  exit "$status"
}
trap 'rollback "$?"' ERR
trap 'rollback 130' INT
trap 'rollback 143' TERM

echo '[update] Backing up existing installed code (root-only)...'
cp -a /opt/devstack "$backup_dir/installed"

echo '[update] Staging updated application without changing live files...'
cp -a /opt/devstack/. "$stage_dir/"
# Retire only the old installed Web Push code; previous-code backup is kept.
rm -f -- "$stage_dir/push-helper.php" "$stage_dir/scripts/notification-push.py"
install -m 0644 -o root -g root "$src/backend/security.php" "$stage_dir/security.php"
install -m 0644 -o root -g root "$src/backend/mail-preview.php" "$stage_dir/mail-preview.php"
for item in notifications.php; do
  install -m 0644 -o root -g root "$src/backend/$item" "$stage_dir/$item"
done
install -m 0644 -o root -g root "$src/backend/public/index.php" "$stage_dir/public/index.php"
install -m 0644 -o root -g root "$src/backend/public/index.html" "$stage_dir/public/index.html"
install -d -m 0755 -o root -g root "$stage_dir/public/pwa"
for item in sw.js manifest.webmanifest pwa/icon-192.png pwa/icon-512.png pwa/devstack.svg; do
  install -m 0644 -o root -g root "$src/backend/public/$item" "$stage_dir/public/$item"
done
install -m 0644 -o root -g root "$src/daemon/devstackd.py" "$stage_dir/daemon/devstackd.py"
install -m 0644 -o root -g root "$src/daemon/wsl_ca.py" "$stage_dir/daemon/wsl_ca.py"
install -m 0644 -o root -g root "$src/daemon/project_frameworks.py" "$stage_dir/daemon/project_frameworks.py"
install -m 0644 -o root -g root "$src/daemon/project_isolation_preflight.py" "$stage_dir/daemon/project_isolation_preflight.py"
for item in project_pilot.py project_runtime_identity.py project_idmap.py project_mounts.py project_access.py project_data_views.py project_runtime.py project_http.py project_lifecycle.py; do
  install -m 0644 -o root -g root "$src/daemon/$item" "$stage_dir/daemon/$item"
done
install -m 0644 -o root -g root "$src/daemon/managed_services.py" "$stage_dir/daemon/managed_services.py"
install -m 0644 -o root -g root "$src/daemon/service-catalog.json" "$stage_dir/daemon/service-catalog.json"
install -d -m 0755 -o root -g root "$stage_dir/framework-installers" "$stage_dir/framework-installers/laravel"
for item in README.md catalog.json common.py registry.py runner.py laravel/README.md laravel/__init__.py laravel/adapter.py; do
  install -m 0644 -o root -g root "$src/framework-installers/$item" "$stage_dir/framework-installers/$item"
done
for item in list-domains.py watch-domains.py set-password.php configure-auth.php notifications-worker.php devstack-notifications.service devstack-notifications.timer devstackd.service; do
  install -m 0644 -o root -g root "$src/scripts/$item" "$stage_dir/scripts/$item"
done

# Only delete compiled assets under the unique temporary staging directory.
if [[ -L "$stage_dir/public/assets" ]]; then
  echo 'Existing installed assets directory is a symlink; aborting.' >&2
  exit 1
fi
rm -rf -- "$stage_dir/public/assets"
cp -a "$src/backend/public/assets" "$stage_dir/public/assets"
chown -R root:root "$stage_dir"
find "$stage_dir" -type d -exec chmod 0755 {} +
find "$stage_dir" -type f -exec chmod 0644 {} +

echo '[update] Validating staged code and active web server configuration...'
php -l "$stage_dir/public/index.php"
php -l "$stage_dir/security.php"
php -l "$stage_dir/mail-preview.php"
php -l "$stage_dir/notifications.php"
php -l "$stage_dir/scripts/notifications-worker.php"
php -r 'exit(extension_loaded("pdo_sqlite") && extension_loaded("mbstring") ? 0 : 1);'
php -l "$stage_dir/scripts/set-password.php"
php -l "$stage_dir/scripts/configure-auth.php"
python3 - "$stage_dir" <<'PY'
import pathlib
import sys
root = pathlib.Path(sys.argv[1])
for rel in ('daemon/devstackd.py', 'daemon/wsl_ca.py', 'daemon/project_frameworks.py', 'daemon/project_isolation_preflight.py', 'daemon/project_pilot.py', 'daemon/project_runtime_identity.py', 'daemon/project_idmap.py', 'daemon/project_mounts.py', 'daemon/project_access.py', 'daemon/project_data_views.py', 'daemon/project_runtime.py', 'daemon/project_http.py', 'daemon/project_lifecycle.py', 'daemon/managed_services.py', 'framework-installers/common.py', 'framework-installers/registry.py', 'framework-installers/runner.py', 'framework-installers/laravel/adapter.py', 'scripts/list-domains.py', 'scripts/watch-domains.py'):
    path = root / rel
    compile(path.read_bytes(), str(path), 'exec')
print('Staged Python syntax OK')
PY
nginx -t
php-fpm8.5 -t

# Only replace the exact unit previously shipped by DevStack. Do not
# overwrite administrator customizations or follow a symlink. This change
# is compatible with the old Python daemon and deliberately survives a
# code rollback: restoring Wants=mariadb.service would wake a stopped DB.
unit_path=/etc/systemd/system/devstackd.service
new_unit="$stage_dir/scripts/devstackd.service"
if [[ ! -f "$unit_path" || -L "$unit_path" ||
      "$(systemctl show -p FragmentPath --value devstackd.service)" != "$unit_path" ]]; then
  echo 'Unexpected DevStack helper unit location; refusing update.' >&2
  false
fi
if ! cmp -s "$unit_path" "$new_unit"; then
  if ! cmp -s "$unit_path" <(sed -e '/^After=local-fs.target nginx.service php8.5-fpm.service$/s/$/ mariadb.service/' -e '/^Wants=nginx.service php8.5-fpm.service$/s/$/ mariadb.service/' "$new_unit"); then
    echo 'DevStack helper unit has custom changes; review them before upgrading.' >&2
    false
  fi
  cp -a -- "$unit_path" "$backup_dir/devstackd.service.before"
  unit_tmp="$(mktemp /etc/systemd/system/.devstackd-XXXXXXXX)"
  install -m 0644 -o root -g root "$new_unit" "$unit_tmp"
  mv -T -- "$unit_tmp" "$unit_path"
  core_unit_changed=1
  unit_tmp=''
fi
systemctl daemon-reload
# Also catch a local drop-in or dependency symlink that would undo the
# correction. Inspect the effective unit before any helper restart.
dependencies="$(systemctl show --value -p Wants -p Requires -p Requisite -p BindsTo -p Upholds devstackd.service)"
if grep -Eq '(^|[[:space:]])(mariadb|mysql|mysqld|redis|redis-server)(@[^[:space:]]*)?\.service([[:space:]]|$)' <<< "$dependencies"; then
  echo 'DevStack helper still depends on an optional project service; review local systemd overrides.' >&2
  false
fi

echo '[update] Swapping installed code and restarting the helper...'
# Keep the previous application in /opt for same-filesystem atomic renames.
if [[ "$notice_timer_was_active" -eq 1 ]]; then systemctl stop devstack-notifications.timer; fi
systemctl stop devstack-notifications.service 2>/dev/null || true

# Earlier installations can have a root-owned web state directory. The
# unprivileged collector must be able to create/open its SQLite journal.
# Repair only this fixed, private directory, never recursively chown its
# contents or follow a symlink. Existing sessions, journals and keys stay put.
if [[ -L /var/lib/devstack-web || ( -e /var/lib/devstack-web && ! -d /var/lib/devstack-web ) ]]; then
  echo 'Unsafe DevStack web state directory; refusing update.' >&2
  false
fi
install -d -m 0700 -o devstackweb -g devstackweb /var/lib/devstack-web
# Create a missing session directory as the web account, not as root.
runuser -u devstackweb -- mkdir -p -m 0700 /var/lib/devstack-web/sessions

flock -x "$project_lock_fd"
old_moved=1
mv -- /opt/devstack "$prior_dir"
mv -- "$stage_dir" /opt/devstack
flock -u "$project_lock_fd"
systemctl restart devstackd
systemctl reload php8.5-fpm

for service in devstackd nginx php8.5-fpm; do
  if ! systemctl is-active --quiet "$service"; then
    echo "Required DevStack service is inactive: $service" >&2
    false
  fi
done

echo '[update] Checking the live HTTPS dashboard...'
healthy=0
for attempt in 1 2 3 4 5; do
  if curl -kfsS --noproxy '*' --max-time 8 \
      --resolve devstack.test:443:127.0.0.1 \
      https://devstack.test/ | grep -Fq '<div id="root"></div>'; then
    healthy=1
    break
  fi
  sleep 1
done
if [[ "$healthy" -ne 1 ]]; then
  echo 'Dashboard health check failed after deployment.' >&2
  false
fi

# Install the optional notification collector's fixed units once. Never
# overwrite unknown administrator-owned unit definitions on a code update.
if [[ ! -e /etc/systemd/system/devstack-notifications.service && ! -e /etc/systemd/system/devstack-notifications.timer ]]; then
  notice_units_created=1
  install -m 0644 -o root -g root /opt/devstack/scripts/devstack-notifications.{service,timer} /etc/systemd/system/
  systemctl daemon-reload
else
  for item in devstack-notifications.service devstack-notifications.timer; do
    cmp -s "/opt/devstack/scripts/$item" "/etc/systemd/system/$item" || { echo "Notification unit differs: $item" >&2; false; }
  done
fi
# A live timer alone is not proof that its one-shot worker can open SQLite.
# Run the actual restricted systemd service before accepting the update.
systemctl start devstack-notifications.service
[[ "$(systemctl show -p Result --value devstack-notifications.service)" == success ]]
[[ "$(systemctl show -p ExecMainStatus --value devstack-notifications.service)" == 0 ]]
systemctl enable --now devstack-notifications.timer
systemctl is-active --quiet devstack-notifications.timer

# Only remove the temporary previous directory after all health checks pass.
rm -rf -- "$prior_dir"
old_moved=0
trap - ERR INT TERM
echo 'DevStack update successful.'
echo "Previous-code backup kept at: $backup_dir/installed"
echo 'Persistent state, project code, MariaDB/Redis service state and mkcert CA were preserved.'
if [[ "$core_unit_changed" -eq 1 ]]; then
  echo "Previous helper unit kept at: $backup_dir/devstackd.service.before"
fi
