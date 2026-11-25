#!/usr/bin/env bash
# Root-only second stage for scripts/update.sh. Do not run by itself.
# Stages files, backs up the previous code, verifies syntax, swaps directories,
# checks live services and automatically restores the old code on failure.
# Does not touch /var/lib/devstack, /etc/devstack, project files or databases.
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

if [[ -L "$src/engines" || -L "$src/engines/laravel" ]]; then
  echo 'Unsafe adapter source directory.' >&2
  exit 1
fi

for file in backend/public/index.php backend/public/index.html backend/security.php \
    daemon/devstackd.py engines/runner.py engines/laravel/__init__.py engines/laravel/adapter.py scripts/list-domains.py scripts/watch-domains.py \
    scripts/set-password.php scripts/configure-auth.php; do
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

rollback() {
  local status="$1"
  trap - ERR INT TERM
  set +e
  if [[ "$old_moved" -eq 1 && -d "$prior_dir" ]]; then
    echo 'UPDATE FAILED — restoring previous installed application...' >&2
    if [[ -e /opt/devstack ]]; then
      mv -- /opt/devstack "$stage_dir.failed"
    fi
    mv -- "$prior_dir" /opt/devstack
    systemctl restart devstackd
    systemctl reload php8.5-fpm
    if [[ ! -d /opt/devstack ]]; then
      echo 'CRITICAL: automatic rollback failed; restore from protected backup!' >&2
    fi
  fi
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
install -m 0644 -o root -g root "$src/backend/security.php" "$stage_dir/security.php"
install -m 0644 -o root -g root "$src/backend/public/index.php" "$stage_dir/public/index.php"
install -m 0644 -o root -g root "$src/backend/public/index.html" "$stage_dir/public/index.html"
install -m 0644 -o root -g root "$src/daemon/devstackd.py" "$stage_dir/daemon/devstackd.py"
install -d -m 0755 -o root -g root "$stage_dir/engines" "$stage_dir/engines/laravel"
for item in runner.py laravel/__init__.py laravel/adapter.py; do
  install -m 0644 -o root -g root "$src/engines/$item" "$stage_dir/engines/$item"
done
for item in list-domains.py watch-domains.py set-password.php configure-auth.php; do
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
php -l "$stage_dir/scripts/set-password.php"
php -l "$stage_dir/scripts/configure-auth.php"
python3 - "$stage_dir" <<'PY'
import pathlib
import sys
root = pathlib.Path(sys.argv[1])
for rel in ('daemon/devstackd.py', 'engines/runner.py', 'engines/laravel/adapter.py', 'scripts/list-domains.py', 'scripts/watch-domains.py'):
    path = root / rel
    compile(path.read_bytes(), str(path), 'exec')
print('Staged Python syntax OK')
PY
nginx -t
php-fpm8.5 -t

echo '[update] Swapping installed code and restarting the helper...'
# Keep the previous application in /opt for same-filesystem atomic renames.
old_moved=1
mv -- /opt/devstack "$prior_dir"
mv -- "$stage_dir" /opt/devstack
systemctl restart devstackd
systemctl reload php8.5-fpm

for service in devstackd nginx php8.5-fpm mariadb redis-server; do
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

# Only remove the temporary previous directory after all health checks pass.
rm -rf -- "$prior_dir"
old_moved=0
trap - ERR INT TERM
echo 'DevStack update successful.'
echo "Previous-code backup kept at: $backup_dir/installed"
echo 'Persistent state, project code, MariaDB and mkcert CA were not changed.'
