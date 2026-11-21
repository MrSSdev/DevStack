#!/usr/bin/env bash
set -u

printf '===== WSL / USER =====\n'
id -un
uname -r
cat /etc/os-release | grep '^PRETTY_NAME='
printf '\n===== PROJECTS DIRECTORY =====\n'
stat -c '%A %U:%G %n' "$HOME" "$HOME/projects" 2>&1 || true
findmnt -T "$HOME/projects" -o TARGET,SOURCE,FSTYPE 2>&1 || true
printf '\n===== SOFTWARE =====\n'
for cmd in php php-fpm8.5 nginx mariadb redis-cli python3 unzip; do
  command -v "$cmd" || true
done
printf '\n===== CURRENT SITES =====\n'
ls -l /etc/nginx/sites-enabled 2>&1 || true
printf '\n===== NGINX TEST =====\n'
nginx -t 2>&1 || true
printf '\n===== PORTS 80 / 443 / 8080 =====\n'
ss -lnt | grep -E ':(80|443|8080)[[:space:]]' || true
printf '\n===== SERVICES =====\n'
for svc in nginx php8.5-fpm mariadb redis-server; do
  printf '%s: ' "$svc"
  systemctl is-active "$svc" 2>&1 || true
done
printf '\n===== AVAILABLE SPACE =====\n'
df -h "$HOME/projects" 2>&1 || true
printf '\n===== DEPRECATED / EXISTING DEVSTACK =====\n'
if [[ -e /etc/devstack/server.json || -e /etc/nginx/sites-available/devstack-manager.conf ]]; then
  echo 'DevStack might already be installed! Do not rerun the installer.'
else
  echo 'No existing DevStack configuration detected.'
fi
