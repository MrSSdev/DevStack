#!/usr/bin/env bash
# Install only after reading README/SECURITY and running the preflight.
set -Eeuo pipefail
project_services=1
if [[ "${1:-}" == --core-only ]]; then
  project_services=0
  shift
fi
if [[ "$#" -ne 0 ]]; then
  echo 'Usage: sudo bash scripts/install.sh [--core-only]' >&2
  exit 2
fi
if [[ "$EUID" -ne 0 ]]; then
  echo 'Use: sudo bash scripts/install.sh' >&2
  exit 1
fi
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEV_USER="${SUDO_USER:-}"
if ! [[ "$DEV_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]]; then
  echo 'Run with sudo as a normal Linux developer account.' >&2
  exit 1
fi
DEV_HOME="$(getent passwd "$DEV_USER" | cut -d: -f6)"
if [[ "$DEV_HOME" != "/home/$DEV_USER" || ! -d "$DEV_HOME/projects" ]]; then
  echo 'Expected ~/projects under /home/<user>. Run scripts/preflight.sh.' >&2
  exit 1
fi
if [[ -e /etc/devstack/server.json || -e /etc/nginx/sites-available/devstack-manager.conf || -e /etc/systemd/system/devstackd.service ]]; then
  echo 'Existing DevStack installation found. Installer refuses overwrite.' >&2
  exit 1
fi
if [[ ! -f "$SRC/backend/public/index.html" ]] || [[ ! -d "$SRC/backend/public/assets" ]] || [[ ! -f "$SRC/backend/public/sw.js" ]] || [[ ! -f "$SRC/backend/public/manifest.webmanifest" ]]; then
  echo 'Frontend is not built. First run bash scripts/build-frontend.sh as normal user.' >&2
  exit 1
fi
if [[ ! -f "$SRC/backend/public/index.php" || ! -f "$SRC/daemon/devstackd.py" || ! -f "$SRC/daemon/project_frameworks.py" || ! -f "$SRC/scripts/devstackd.service" ]]; then
  echo 'Source tree is incomplete.' >&2
  exit 1
fi
if ! command -v systemctl >/dev/null || [[ "$(ps -p 1 -o comm=)" != systemd ]]; then
  echo 'Requires Ubuntu WSL2 with systemd.' >&2
  exit 1
fi
printf '[1/8] Installing official Ubuntu prerequisites...\n'
apt-get update
packages=(nginx php8.5-cli php8.5-fpm php8.5-mysql php8.5-redis php8.5-xml php8.5-sqlite3 php8.5-mbstring mkcert acl python3 ca-certificates libnss3-tools)
if [[ "$project_services" -eq 1 ]]; then
  packages+=(mariadb-server redis-server)
fi
apt-get install -y "${packages[@]}"
printf '[2/8] Creating unprivileged panel account and root-owned directories...\n'
if ! id devstackweb >/dev/null 2>&1; then
  useradd --system --user-group --home-dir /nonexistent --shell /usr/sbin/nologin devstackweb
fi
install -d -m 0755 -o root -g root /opt/devstack /opt/devstack/public /opt/devstack/backend /opt/devstack/daemon /opt/devstack/scripts
install -d -m 0700 -o root -g root /var/lib/devstack /var/lib/devstack/ca /etc/devstack/certs
# The web worker must traverse /etc/devstack to read auth.mode and auth.hash,
# but must not be allowed to list the directory or read private CA material.
install -d -m 0710 -o root -g devstackweb /etc/devstack
# Keep PHP session storage OUTSIDE /var/lib/devstack (root-only system secrets).
# The web user must traverse every parent directory to persist CSRF sessions.
install -d -m 0700 -o devstackweb -g devstackweb /var/lib/devstack-web /var/lib/devstack-web/sessions
cp -a "$SRC/backend/public/." /opt/devstack/public/
cp -a "$SRC/backend/security.php" /opt/devstack/security.php
cp -a "$SRC/backend/mail-preview.php" /opt/devstack/mail-preview.php
for item in notifications.php; do
  install -m 0644 -o root -g root "$SRC/backend/$item" "/opt/devstack/$item"
done
for item in notifications-worker.php devstack-notifications.service devstack-notifications.timer devstackd.service; do
  install -m 0644 -o root -g root "$SRC/scripts/$item" "/opt/devstack/scripts/$item"
done
install -m 0644 -o root -g root "$SRC/daemon/devstackd.py" /opt/devstack/daemon/devstackd.py
install -m 0644 -o root -g root "$SRC/daemon/wsl_ca.py" /opt/devstack/daemon/wsl_ca.py
install -m 0644 -o root -g root "$SRC/daemon/project_frameworks.py" /opt/devstack/daemon/project_frameworks.py
install -m 0644 -o root -g root "$SRC/daemon/managed_services.py" /opt/devstack/daemon/managed_services.py
install -m 0644 -o root -g root "$SRC/daemon/service-catalog.json" /opt/devstack/daemon/service-catalog.json
if [[ -L "$SRC/framework-installers" || -L "$SRC/framework-installers/laravel" ]]; then
  echo 'Unsafe adapter source directory.' >&2
  exit 1
fi
install -d -m 0755 -o root -g root /opt/devstack/framework-installers /opt/devstack/framework-installers/laravel
for item in README.md catalog.json common.py registry.py runner.py laravel/README.md laravel/__init__.py laravel/adapter.py; do
  [[ -f "$SRC/framework-installers/$item" && ! -L "$SRC/framework-installers/$item" ]] || { echo "Unsafe engine source: $item" >&2; exit 1; }
  install -m 0644 -o root -g root "$SRC/framework-installers/$item" "/opt/devstack/framework-installers/$item"
done
cp -a "$SRC/scripts/list-domains.py" /opt/devstack/scripts/list-domains.py
cp -a "$SRC/scripts/watch-domains.py" /opt/devstack/scripts/watch-domains.py
cp -a "$SRC/scripts/set-password.php" /opt/devstack/scripts/set-password.php
cp -a "$SRC/scripts/configure-auth.php" /opt/devstack/scripts/configure-auth.php
# Source is immutable to the web user; only explicit session files are web-writable.
chown -R root:root /opt/devstack
find /opt/devstack -type d -exec chmod 0755 {} +
find /opt/devstack -type f -exec chmod 0644 {} +
cat > /etc/devstack/server.json <<CONFIG
{"dev_user":"$DEV_USER","projects_dir":"$DEV_HOME/projects"}
CONFIG
chmod 0600 /etc/devstack/server.json
printf '[3/8] Generating a new local mkcert CA and TLS certificate...\n'
CAROOT=/var/lib/devstack/ca mkcert -cert-file /etc/devstack/certs/devstack.pem -key-file /etc/devstack/certs/devstack-key.pem devstack.test
chmod 0600 /etc/devstack/certs/devstack-key.pem /var/lib/devstack/ca/rootCA-key.pem
chmod 0644 /etc/devstack/certs/devstack.pem /var/lib/devstack/ca/rootCA.pem
printf '[4/8] Installing isolated PHP-FPM pool...\n'
cat > /etc/php/8.5/fpm/pool.d/devstack-manager.conf <<'FPM'
[devstack-manager]
user = devstackweb
group = devstackweb
listen = /run/php/devstack-manager.sock
listen.owner = www-data
listen.group = www-data
listen.mode = 0660
pm = ondemand
pm.max_children = 3
pm.process_idle_timeout = 15s
pm.max_requests = 500
clear_env = yes
php_admin_value[session.save_path] = /var/lib/devstack-web/sessions
php_admin_value[expose_php] = Off
php_admin_value[display_errors] = Off
php_admin_value[log_errors] = On
FPM
printf '[5/8] Creating loopback-only Nginx vhost...\n'
cat > /etc/nginx/sites-available/devstack-manager.conf <<'NGINX'
server {
    listen 127.0.0.1:80;
    server_name devstack.test;
    return 308 https://devstack.test$request_uri;
}
server {
    listen 127.0.0.1:443 ssl;
    server_name devstack.test;
    ssl_certificate /etc/devstack/certs/devstack.pem;
    ssl_certificate_key /etc/devstack/certs/devstack-key.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    server_tokens off;
    root /opt/devstack/public;
    index index.php;
    client_max_body_size 32k;
    add_header X-Content-Type-Options nosniff always;
    add_header Referrer-Policy no-referrer always;
    location /assets/ {
        try_files $uri =404;
        access_log off;
        expires 1h;
    }
    location / {
        try_files $uri /index.php$is_args$args;
    }
    location ~ ^/index\.php$ {
        include snippets/fastcgi-php.conf;
        fastcgi_pass unix:/run/php/devstack-manager.sock;
    }
    location ~ \.php$ { return 404; }
    location ~ /\. { deny all; }
}
NGINX
ln -s /etc/nginx/sites-available/devstack-manager.conf /etc/nginx/sites-enabled/devstack-manager.conf
printf '[6/8] Installing restricted systemd helper...\n'
install -m 0644 -o root -g root "$SRC/scripts/devstackd.service" /etc/systemd/system/devstackd.service
printf '[7/8] Select local panel authentication (password is recommended)...\n'
/usr/bin/php /opt/devstack/scripts/configure-auth.php --setup
printf '[8/8] Validating and starting services...\n'
php-fpm8.5 -t
nginx -t
systemctl daemon-reload
systemctl enable --now nginx php8.5-fpm
# Installing optional project services is an explicit choice of the full
# first-install path, not a dependency of the DevStack helper.
if [[ "$project_services" -eq 1 ]]; then
  systemctl enable --now mariadb redis-server
fi
systemctl reload php8.5-fpm
systemctl reload nginx
systemctl enable --now devstackd
install -m 0644 -o root -g root /opt/devstack/scripts/devstack-notifications.{service,timer} /etc/systemd/system/
systemctl daemon-reload
# Initialize the journal with the actual unprivileged, sandboxed worker.
systemctl start devstack-notifications.service
[[ "$(systemctl show -p Result --value devstack-notifications.service)" == success ]]
[[ "$(systemctl show -p ExecMainStatus --value devstack-notifications.service)" == 0 ]]
systemctl enable --now devstack-notifications.timer
curl -fsSk --resolve devstack.test:443:127.0.0.1 https://devstack.test/ | grep -q '<div id="root"></div>'
for service in devstackd nginx php8.5-fpm; do
  systemctl --no-pager is-active "$service"
done
if [[ "$project_services" -eq 1 ]]; then
  for service in mariadb redis-server; do
    systemctl --no-pager is-active "$service"
  done
fi
printf '\nDevStack installed on WSL. Run windows/sync-hosts.ps1 as Windows Administrator.\n'
printf 'Protect /var/lib/devstack/ca/rootCA-key.pem and all secret/state files.\n'
