#!/usr/bin/env bash
# Run with the reviewed Bubblewrap package already installed on the runner.
set -Eeuo pipefail
[[ "${GITHUB_ACTIONS:-}" == true && "$EUID" -ne 0 && "$(ps -p 1 -o comm=)" == systemd ]]
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
# The preceding registered-preview fixture leaves only disposable control
# files at these paths. Keep them for any failure diagnostics.
sudo test -f /opt/devstack/daemon/project_pilot.py
sudo test ! -e /opt/devstack/daemon/devstackd.py
sudo test ! -e /etc/systemd/system/devstackd.service
preview_root="$(mktemp -d "$RUNNER_TEMP/devstack-preview.XXXXXXXX")"
sudo mv /opt/devstack "$preview_root/opt"
sudo mv /etc/devstack "$preview_root/etc"
sudo mv /var/lib/devstack "$preview_root/state"
sudo chmod go-w /opt

sudo apt-get install -y --no-install-recommends \
    composer php8.5-bcmath php8.5-curl php8.5-intl php8.5-zip python3-venv
mkdir -p "$HOME/projects" "$HOME/.local/bin"
ln -sf "$(command -v node)" "$HOME/.local/bin/node"
ln -sf "$(command -v npm)" "$HOME/.local/bin/npm"
git config --global user.name 'DevStack CI'
git config --global user.email 'ci@example.invalid'
composer global require laravel/installer --no-interaction --no-ansi --no-progress --prefer-dist
bash scripts/build-frontend.sh
printf '2\nDISABLE\n' | sudo bash scripts/install.sh
printf 'allow-passwordless\n' | sudo tee /etc/devstack/engine-access.mode >/dev/null
sudo bash scripts/install-mailpit.sh
sudo install -m 0644 /var/lib/devstack/ca/rootCA.pem /usr/local/share/ca-certificates/devstack-activation.crt
sudo update-ca-certificates
printf '127.0.0.1 devstack.test isolation-livewire.test isolation-sibling.test\n' | sudo tee -a /etc/hosts
mkdir -p "$HOME/.pki/nssdb"
certutil -N --empty-password -d "sql:$HOME/.pki/nssdb"
certutil -A -d "sql:$HOME/.pki/nssdb" -n DevStack-activation -t 'C,,' -i /usr/local/share/ca-certificates/devstack-activation.crt

venv="$RUNNER_TEMP/devstack-activation-venv"
python3 -m venv "$venv"
"$venv/bin/pip" install playwright==1.60.0
# The Ubuntu 26.04 image ships Chrome and its matching native libraries.
export DEVSTACK_CHROMIUM="$(command -v google-chrome)"
"$DEVSTACK_CHROMIUM" --version
# Use the same canonical source location and real second-stage updater.
mkdir "$HOME/projects/devstack-source"
cp -a backend daemon framework-installers scripts "$HOME/projects/devstack-source/"
"$venv/bin/python" tests/project-activation-smoke.py
