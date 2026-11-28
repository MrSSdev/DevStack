#!/usr/bin/env bash
# Explicit developer action only. Downloads and Node code run without root.
set -Eeuo pipefail
umask 077
[[ $EUID -ne 0 ]] || { echo 'Run as the developer, not root.' >&2; exit 2; }
src="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
base=/opt/devstack-tools
deps=false
force=false
for arg in "$@"; do
  case "$arg" in
    --with-deps) deps=true ;;
    --force) force=true ;;
    -h|--help)
      echo 'Usage: bash install.sh [--with-deps] [--force]'
      echo 'Also updates the installed extension; --with-deps allows Playwright to install OS libraries.'
      exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done
for tool in node npm sudo python3 flock sha256sum; do
  command -v "$tool" >/dev/null || { echo "Missing tool: $tool" >&2; exit 1; }
done
node_bin="$(readlink -f -- "$(command -v node)")"
node -e 'if (+process.versions.node.split(".")[0] < 22) { console.error("Node.js 22+ is required"); process.exit(1); }'
# With DevStack installed, trust its existing public CA by default. Never
# generate another CA, run a browser as root, or bypass TLS validation.
if [[ -f /opt/devstack/daemon/devstackd.py ]]; then
  [[ -f /opt/devstack/daemon/wsl_ca.py ]] || {
    echo 'Update DevStack core first to enable its WSL CA helper.' >&2
    exit 1
  }
  if [[ ! -x /usr/bin/certutil ]]; then
    sudo apt-get install -y libnss3-tools
  fi
  sudo -- /usr/bin/python3 -I /opt/devstack/daemon/devstackd.py --trust-wsl-ca
fi
# Hash the actual distribution files, not the DevStack commit or project tree.
mail_files=(mail/cli.py mail/contract.py mail/mailpit.py mail/message.py)
skill_files=(skills/devstack-tools/SKILL.md skills/devstack-tools/references/mail.md
  skills/devstack-playwright/SKILL.md skills/devstack-mail/SKILL.md)
files=(package.json manifest.json project-cards.json install.sh publish.sh update.sh uninstall.sh rollback.sh smoke.cjs
  bin/playwright bin/playwright-cli bin/devstack-mail "${mail_files[@]}" "${skill_files[@]}")
for file in "${files[@]}"; do
  [[ -f "$src/$file" && ! -L "$src/$file" ]] || { echo "Missing/unsafe source: $file" >&2; exit 1; }
done
build="$(cd "$src" && sha256sum -- "${files[@]}" | sha256sum | cut -d' ' -f1)"
if ! $force && ! $deps && [[ -f "$base/current/BUILD" ]] && [[ "$(cat "$base/current/BUILD")" == "$build" ]]; then
  echo 'DevStack Tools is already at this build. Use --force to repair.'
  exit 0
fi
stage="$(mktemp -d /tmp/devstack-tools.XXXXXXXX)"
trap 'rm -rf -- "$stage"' EXIT
payload="$stage/payload"
mkdir -p "$payload/playwright" "$payload/browsers" "$payload/bin" "$payload/mail"
cp -- "$src/package.json" "$payload/playwright/package.json"
cp -- "$src/smoke.cjs" "$src/manifest.json" "$src/project-cards.json" "$payload/"
cp -- "$src/bin/playwright" "$src/bin/playwright-cli" "$src/bin/devstack-mail" "$payload/bin/"
for file in "${mail_files[@]}" "${skill_files[@]}"; do
  install -D -m 0644 -- "$src/$file" "$payload/$file"
done
chmod 0755 "$payload/bin/"*
printf '%s\n' "$build" > "$payload/BUILD"
cd "$payload/playwright"
# All three npm packages have exact version dependencies (no floating npm tags).
# Retain the registry-generated integrity lock in the installed release.
export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
npm install --ignore-scripts --no-audit --no-fund --package-lock=true \
  --registry=https://registry.npmjs.org/ --cache "$stage/npm-cache"
node - <<'JS'
const fs = require('node:fs');
const expected = require('./package.json').dependencies['@playwright/test'];
const lock = require('./package-lock.json');
const names = ['@playwright/test', 'playwright', 'playwright-core'];
if (Object.keys(lock.packages).length !== names.length + 1)
  throw new Error('Unexpected dependency graph');
for (const name of names) {
  const entry = lock.packages['node_modules/' + name];
  if (!entry || entry.version !== expected || !entry.integrity?.startsWith('sha512-') ||
      !entry.resolved?.startsWith('https://registry.npmjs.org/'))
    throw new Error('Unexpected package lock: ' + name);
  if (require(name + '/package.json').version !== expected)
    throw new Error('Installed package mismatch: ' + name);
}
JS
unset PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD
export PLAYWRIGHT_BROWSERS_PATH="$payload/browsers"
cli="$payload/playwright/node_modules/@playwright/test/cli.js"
if $deps; then
  # The upstream install-deps command explicitly uses sudo for system libraries.
  "$node_bin" "$cli" install-deps chromium
fi
"$node_bin" "$cli" install --no-shell chromium
"$node_bin" - <<'JS' > chromium-path
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('@playwright/test');
const root = path.dirname(process.env.PLAYWRIGHT_BROWSERS_PATH);
const executable = path.relative(root, fs.realpathSync(chromium.executablePath()));
if (!/^browsers\/chromium-/.test(executable))
  throw new Error('Chromium executable is outside the staged browser directory.');
process.stdout.write(executable + '\n');
JS
# The staged client's cache registration is not meaningful after relocation.
rm -rf -- "$payload/browsers/.links"
cd "$src"
sudo -- /bin/bash "$src/publish.sh" publish "$stage" "$node_bin"
echo "DevStack Tools installed in $base/bin: playwright, playwright-cli, devstack-mail"
