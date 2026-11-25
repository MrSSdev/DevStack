#!/usr/bin/env bash
# Install only after reading README/SECURITY and running the preflight.
set -Eeuo pipefail
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
if [[ ! -f "$SRC/backend/public/index.html" ]] || [[ ! -d "$SRC/backend/public/assets" ]]; then
  echo 'Frontend is not built. First run bash scripts/build-frontend.sh as normal user.' >&2
  exit 1
fi
if [[ ! -f "$SRC/backend/public/index.php" || ! -f "$SRC/daemon/devstackd.py" ]]; then
  echo 'Source tree is incomplete.' >&2
  exit 1
fi
if ! command -v systemctl >/dev/null || [[ "$(ps -p 1 -o comm=)" != systemd ]]; then
  echo 'Requires Ubuntu WSL2 with systemd.' >&2
  exit 1
fi
printf '[1/8] Installing official Ubuntu prerequisites...\n'
apt-get update
apt-get install -y nginx php8.5-cli php8.5-fpm php8.5-mysql php8.5-redis mariadb-server redis-server mkcert acl python3 ca-certificates
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
cp -a "$SRC/daemon/devstackd.py" /opt/devstack/daemon/devstackd.py
if [[ -L "$SRC/engines" || -L "$SRC/engines/laravel" ]]; then
  echo 'Unsafe adapter source directory.' >&2
  exit 1
fi
install -d -m 0755 -o root -g root /opt/devstack/engines /opt/devstack/engines/laravel
for item in runner.py laravel/__init__.py laravel/adapter.py; do
  [[ -f "$SRC/engines/$item" && ! -L "$SRC/engines/$item" ]] || { echo "Unsafe engine source: $item" >&2; exit 1; }
  install -m 0644 -o root -g root "$SRC/engines/$item" "/opt/devstack/engines/$item"
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
cat > /etc/systemd/system/devstackd.service <<'UNIT'
[Unit]
Description=DevStack local development manager helper
After=local-fs.target nginx.service php8.5-fpm.service mariadb.service
Wants=nginx.service php8.5-fpm.service mariadb.service

[Service]
Type=simple
User=root
Group=root
ExecStart=/usr/bin/python3 /opt/devstack/daemon/devstackd.py
Restart=on-failure
RestartSec=3s
RuntimeDirectory=devstack
RuntimeDirectoryMode=0750
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true

[Install]
WantedBy=multi-user.target
UNIT
printf '[7/8] Select local panel authentication (password is recommended)...\n'
/usr/bin/php /opt/devstack/scripts/configure-auth.php --setup
printf '[8/8] Validating and starting services...\n'
php-fpm8.5 -t
nginx -t
systemctl daemon-reload
systemctl enable --now nginx php8.5-fpm mariadb redis-server
systemctl reload php8.5-fpm
systemctl reload nginx
systemctl enable --now devstackd
curl -fsSk --resolve devstack.test:443:127.0.0.1 https://devstack.test/ | grep -q '<div id="root"></div>'
systemctl --no-pager is-active devstackd nginx php8.5-fpm mariadb redis-server
printf '\nDevStack installed on WSL. Run windows/sync-hosts.ps1 as Windows Administrator.\n'
printf 'Protect /var/lib/devstack/ca/rootCA-key.pem and all secret/state files.\n'
