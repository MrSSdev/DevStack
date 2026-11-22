#!/usr/bin/env bash
# Update an existing DevStack checkout and installed application.
# Run as your normal Ubuntu account, NEVER as root.
# Does not re-run the first-install-only scripts/install.sh.
set -Eeuo pipefail
umask 077

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

if [[ "$(git branch --show-current)" != "main" ]]; then
  echo 'Update only supports the checked-out main branch.' >&2
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

echo '[1/5] Fast-forward source code...'
git pull --ff-only

echo '[2/5] Running Python and PHP unit tests and security scan...'
python3 -m unittest discover -s tests -v
php tests/php-security.php
bash scripts/security-scan.sh

echo '[3/5] Checking syntax...'
bash -n scripts/update.sh scripts/apply-update.sh
php -l backend/public/index.php
php -l backend/security.php
php -l scripts/set-password.php
php -l scripts/configure-auth.php
python3 -m py_compile daemon/devstackd.py scripts/list-domains.py scripts/watch-domains.py

echo '[4/5] Building frontend as non-root...'
bash scripts/build-frontend.sh

echo '[5/5] Applying staged update with root-controlled rollback...'
sudo bash scripts/apply-update.sh
echo 'DevStack update complete. Check the browser and an existing disposable project.'
