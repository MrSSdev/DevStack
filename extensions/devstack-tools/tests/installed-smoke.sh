#!/usr/bin/env bash
# Disposable GitHub-hosted runner only. Exercises the installed extension, not DevStack core.
set -Eeuo pipefail
[[ ${GITHUB_ACTIONS:-} == true && $EUID -ne 0 ]] || exit 2
src="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
base=/opt/devstack-tools
[[ ! -e $base && ! -L $base ]] || { echo 'Expected a fresh runner'; exit 1; }
bash "$src/install.sh" --with-deps
first="$(readlink -f "$base/current")"
[[ ! -w $first/playwright/package.json && ! -w $first/browsers ]]
[[ "$(stat -c %u "$first")" == 0 ]]
"$base/bin/playwright" --version | grep -Fx 'Version 1.65.1'
bash "$src/install.sh"
[[ "$(readlink -f "$base/current")" == "$first" ]]
work="$(mktemp -d)"
stage=''
cleanup() {
  (cd "$work" && "$base/bin/playwright-cli" -s=devstack-smoke close >/dev/null 2>&1) || true
  [[ -z $stage ]] || rm -rf -- "$stage"
  rm -rf -- "$work"
}
trap cleanup EXIT
cd "$work"
cat > playwright.config.cjs <<'JS'
const { defineConfig } = require('@playwright/test');
module.exports = defineConfig({
  testDir: '.',
  workers: 1,
  reporter: 'list',
  use: { channel: 'chromium', headless: true, trace: 'retain-on-failure' }
});
JS
cat > shared.spec.cjs <<'JS'
const { test, expect } = require('@playwright/test');
test('shared Playwright outside its installation', async ({ page }) => {
  await page.setContent('<input aria-label="Name"><button>Save</button>');
  await page.getByRole('textbox').fill('DevStack');
  await expect(page.getByRole('textbox')).toHaveValue('DevStack');
});
JS
"$base/bin/playwright" test
"$base/bin/playwright-cli" -s=devstack-smoke open 'data:text/html,<title>DevStackCLI</title><h1>DevStackCLI</h1>'
"$base/bin/playwright-cli" -s=devstack-smoke eval '() => document.title' | grep -F DevStackCLI
"$base/bin/playwright-cli" -s=devstack-smoke close
# CLI daemon cleanup is explicit and limited to this disposable CI runner.
"$base/bin/playwright-cli" kill-all
# Wait only for daemon shutdown, not for core services or WSL.
for i in {1..20}; do
  if ! pgrep -f "^.*node .*$first/playwright/" >/dev/null; then break; fi
  sleep 0.25
done
# Reuse downloaded files to exercise a failed and successful replacement;
# no second package/browser download. No root-owned state in the candidate.
stage="$(mktemp -d /tmp/devstack-tools.XXXXXXXX)"
mkdir "$stage/payload"
cp -R -P --preserve=mode -- "$first/." "$stage/payload/"
chmod -R u+rwX "$stage/payload"
cp "$stage/payload/smoke.cjs" "$stage/smoke-original.cjs"
printf '%s\n' 'process.exit(42);' > "$stage/payload/smoke.cjs"
node_bin="$(readlink -f -- "$(command -v node)")"
if sudo -- /bin/bash "$src/publish.sh" publish "$stage" "$node_bin"; then
  echo 'A failed candidate unexpectedly became active' >&2
  exit 1
fi
[[ "$(readlink -f "$base/current")" == "$first" ]]
cp "$stage/smoke-original.cjs" "$stage/payload/smoke.cjs"
sudo -- /bin/bash "$src/publish.sh" publish "$stage" "$node_bin"
second="$(readlink -f "$base/current")"
[[ $second != "$first" && ! -e $first ]]
[[ "$(find "$base/releases" -mindepth 1 -maxdepth 1 -type d | wc -l)" -eq 1 ]]
# The exact npm registry lock, with integrity, is retained by the install.
cat "$second/playwright/package-lock.json"
cd "$src"
bash "$src/uninstall.sh" --confirm
[[ ! -e $base ]]
echo 'PASS: shared runner and CLI, read-only tools, no-op update, failed-candidate rollback, replacement and uninstall.'
