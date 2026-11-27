#!/usr/bin/env bash
# Fresh Ubuntu 26.04 / WSL2 bootstrap from an inspected DevStack git checkout.
# Run as the ordinary Ubuntu user. The actual installer prompts before setup.
set -Eeuo pipefail
if [[ "$#" -gt 1 || ( "$#" -eq 1 && "$1" != --core-only ) ]]; then
  echo 'Usage: bash scripts/bootstrap-ubuntu.sh [--core-only]' >&2
  exit 2
fi

if [[ "${EUID}" -eq 0 ]]; then
  echo 'Run as a normal Ubuntu developer, not as root/sudo.' >&2
  exit 1
fi
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "$SRC" != "$HOME/projects/devstack-source" ]]; then
  echo 'Expected checkout: ~/projects/devstack-source' >&2
  exit 1
fi
if [[ ! -d "$HOME/projects" ]]; then
  echo 'Missing ~/projects.' >&2
  exit 1
fi
if [[ -e /etc/devstack/server.json || -e /etc/systemd/system/devstackd.service ]]; then
  echo 'DevStack is already installed: do not run a fresh-install bootstrap.' >&2
  exit 1
fi

echo '[1/5] Installing Ubuntu build tools...'
sudo apt-get update
sudo apt-get install -y nodejs npm git python3

# Vite 8 requires an up-to-date Node release. Do not silently install an
# arbitrary third-party NodeSource package/script as root.
node -e '
const [major,minor] = process.versions.node.split(".").map(Number);
if (!((major===20 && minor>=19) || (major===22 && minor>=12) || major>=24)) {
  console.error("Node.js 20.19+, 22.12+ or 24+ is required; found " + process.version);
  process.exit(1);
}'
echo '[2/5] Building production frontend as the current user...'
cd "$SRC"
bash scripts/build-frontend.sh

echo '[3/5] Checking WSL2 environment...'
bash scripts/preflight.sh

echo '[4/5] Running tests that do not require privileged services...'
python3 -m unittest discover -s tests -v

echo '[5/5] Installing DevStack server (requires sudo and auth-mode selection)...'
sudo bash scripts/install.sh "$@"

echo
echo 'Ubuntu installation finished. Open elevated Windows PowerShell and run:'
echo '  $script = "\\\\wsl.localhost\\Ubuntu-26.04\\home\\<ubuntu-user>\\projects\\devstack-source\\windows\\setup-windows.ps1"'
echo '  & $script'
echo 'Replace <ubuntu-user> with your Ubuntu user. The Windows setup prompts for CA trust once.'
