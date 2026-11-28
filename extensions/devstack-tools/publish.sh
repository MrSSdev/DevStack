#!/usr/bin/env bash
# Privileged filesystem publication only; never run package or browser code as root.
set -Eeuo pipefail
umask 022
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
base=/opt/devstack-tools
die() { echo "DevStack Tools: $*" >&2; exit 1; }
[[ $EUID -eq 0 && -n "${SUDO_USER:-}" && -n "${SUDO_UID:-}" && $SUDO_UID != 0 ]] ||
  die 'Use the unprivileged install/update/uninstall entry point.'
owner="$SUDO_USER"
[[ "$(id -u "$owner")" == "$SUDO_UID" ]] || die 'Invalid invoking user.'
home="$(getent passwd "$owner" | cut -d: -f6)"
action="${1:-}"
[[ $action == publish || $action == remove || $action == rollback ]] || die 'Unknown operation.'
[[ $action != publish || $# -eq 3 ]] || die 'Expected a stage and Node executable.'
[[ $action == publish || $# -eq 1 ]] || die 'Unexpected arguments.'
# Validate the candidate before creating any installation directories.
if [[ $action == publish ]]; then
  stage="$2"
  node_bin="$3"
  [[ $stage =~ ^/tmp/devstack-tools\.[a-zA-Z0-9]{8}$ && -d $stage && ! -L $stage ]] ||
    die 'Unsafe staging directory.'
  [[ "$(stat -c %u:%a -- "$stage")" == "$SUDO_UID:700" ]] ||
    die 'Stage must be private and owned by the invoking developer.'
  payload="$stage/payload"
  [[ -d $payload && ! -L $payload && $node_bin == /* && -x $node_bin ]] ||
    die 'Missing staged payload or Node executable.'
  [[ -z "$(find -P "$payload" ! -type f ! -type d ! -type l -print -quit)" ]] ||
    die 'Unsupported file in staged payload.'
fi
if [[ ! -e $base && ! -L $base ]]; then
  if [[ $action == remove ]]; then echo 'DevStack Tools is not installed.'; exit 0; fi
  [[ $action == publish ]] || die 'No installed release to roll back.'
  mkdir -m 0755 -- "$base"
  printf '%s\n' 'devstack-tools-v1' > "$base/.managed"
  mkdir -m 0755 -- "$base/releases"
  : > "$base/.lock"
  chmod 0644 "$base/.lock"
fi
[[ -d $base && ! -L $base && "$(stat -c %u:%a -- "$base")" == 0:755 &&
   -f $base/.managed && ! -L $base/.managed && "$(cat "$base/.managed")" == devstack-tools-v1 &&
   -f $base/.lock && ! -L $base/.lock && "$(stat -c %u:%a -- "$base/.lock")" == 0:644 &&
   -d $base/releases && ! -L $base/releases && "$(stat -c %u:%a -- "$base/releases")" == 0:755 ]] ||
  die 'Refusing to modify an unmanaged or unsafe /opt/devstack-tools.'
# Shared by the launchers. Never kill a running job to replace its tools.
exec 9<"$base/.lock"
flock -x -w 2 9 || die 'Tools are in use. Close active tests and CLI sessions first.'
previous=''
if [[ -e $base/current || -L $base/current ]]; then
  [[ -L $base/current ]] || die 'current must be a managed symlink.'
  previous="$(readlink -f -- "$base/current")"
  [[ $previous =~ ^/opt/devstack-tools/releases/release-[a-zA-Z0-9]{8}$ && -d $previous && ! -L $previous ]] ||
    die 'Unsafe current release.'
fi
retained=''
if [[ -e $base/previous || -L $base/previous ]]; then
  [[ -L $base/previous ]] || die 'previous must be a managed symlink.'
  retained="$(readlink -f -- "$base/previous")"
  [[ $retained =~ ^/opt/devstack-tools/releases/release-[a-zA-Z0-9]{8}$ && -d $retained && ! -L $retained &&
     "$(stat -c %u:%a -- "$retained")" == 0:755 ]] || die 'Unsafe rollback release.'
fi
for name in bin playwright browsers skills; do
  if [[ -e $base/$name || -L $base/$name ]]; then
    [[ -L $base/$name && "$(readlink -- "$base/$name")" == "current/$name" ]] ||
      die "Unmanaged path: $name"
  fi
done
# CLI browser/daemon children can outlive a short CLI invocation. Check their
# real executable/argv too. This is an update guard, not an agent sandbox.
python3 - "$base/releases" <<'PY'
import os, pathlib, sys
prefix = os.fsencode(sys.argv[1] + '/')
for p in pathlib.Path('/proc').iterdir():
    if not p.name.isdigit():
        continue
    try:
        args = (p / 'cmdline').read_bytes().split(b'\0')
        exe = os.fsencode(os.readlink(p / 'exe'))
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        continue
    if exe.startswith(prefix) or any(a.startswith(prefix) for a in args):
        sys.exit('DevStack Tools still has a running process (PID ' + p.name + '). Close sessions first.')
PY
if [[ $action == remove ]]; then
  rm -rf --one-file-system -- "$base"
  echo 'DevStack Tools removed; project files, user data and OS packages were not changed.'
  exit 0
fi
point_release() {
  local alias="$1" release="$2" link
  # A root-only temporary directory keeps pointer replacement atomic.
  link="$(mktemp -d "$base/.activate-XXXXXXXX")"
  ln -s "releases/${release##*/}" "$link/link"
  mv -Tf -- "$link/link" "$base/$alias"
  rmdir -- "$link"
}
check_manifest() {
  local release="$1" validator=/opt/devstack/daemon/extension_manifest.py
  if [[ -f $validator && ! -L $validator && "$(stat -c %u:%a -- "$validator")" == 0:644 ]]; then
    /usr/bin/python3 -IB "$validator" devstack-tools "$release" || return 1
  elif [[ -f "$(dirname -- "${BASH_SOURCE[0]}")/../../daemon/extension_manifest.py" ]]; then
    local source_validator
    source_validator="$(realpath -- "$(dirname -- "${BASH_SOURCE[0]}")/../../daemon/extension_manifest.py")"
    runuser -u "$owner" -- env -i HOME="$home" PATH=/usr/bin:/bin \
      /usr/bin/python3 -IB "$source_validator" devstack-tools "$release" || return 1
  fi
  # The standalone installation checks the bundle and installed versions
  # without importing code from the staged candidate as root.
  /usr/bin/python3 -I - "$release" <<'PYTHON'
import json, pathlib, sys

base = pathlib.Path(sys.argv[1])

def load(part):
    with (base / part).open('rb') as f:
        return json.loads(f.read(32769))

manifest = load('manifest.json')
if manifest['id'] != 'devstack-tools' or manifest['contract'] != 1 or manifest['core_contract'] != 1:
    sys.exit('Unsupported Tools manifest contract.')
if manifest['version'] != load('playwright/package.json')['version']:
    sys.exit('Tools version mismatch.')
tools = {tool['id']: tool['version'] for tool in manifest['tools']}
for name, package in (('playwright-test', '@playwright/test'), ('playwright-cli', 'playwright')):
    if tools[name] != load('playwright/node_modules/' + package + '/package.json')['version']:
        sys.exit('Playwright version mismatch.')
print(tools['devstack-mail'])
PYTHON
}
if [[ $action == rollback ]]; then
  [[ -n $previous && -n $retained && $previous != "$retained" ]] ||
    die 'No previous verified release is available.'
  if [[ -f $retained/manifest.json ]]; then
    check_manifest "$retained" >/dev/null
  fi
  point_release current "$retained"
  point_release previous "$previous"
  echo "Activated $retained"
  exit 0
fi
incoming="$(mktemp -d "$base/releases/release-XXXXXXXX")"
cleanup() {
  if [[ "$(readlink -f -- "$base/current")" != "$incoming" ]]; then
    rm -rf --one-file-system -- "$incoming"
  fi
}
trap cleanup EXIT
# Preserve executable bits (GNU cp --no-preserve=all also drops them), but
# not ownership, xattrs or hardlinks. The destination is root-only until
# permissions are normalized; no setuid/setgid file is exposed.
cp -R -P --preserve=mode -- "$payload/." "$incoming/"
[[ -z "$(find -P "$incoming" ! -type f ! -type d ! -type l -print -quit)" ]] ||
  die 'Unsupported copied file.'
while IFS= read -r -d '' link; do
  target="$(realpath -e -- "$link")" || die 'Broken link in release.'
  [[ $target == "$incoming/"* ]] || die 'Link escapes the release.'
done < <(find -P "$incoming" -type l -print0)
find -P "$incoming" -type f -exec chmod a-s,a-t,u=rwX,go=rX {} +
find -P "$incoming" -type d -exec chmod 0755 {} +
[[ -z "$(find -P "$incoming" ! -user root -print -quit)" ]] ||
  die 'Release ownership is not root.'
[[ -d $incoming/mail && ! -L $incoming/mail && -x $incoming/bin/devstack-mail ]] ||
  die 'Invalid DevStack Mail installation.'
for directory in skills skills/devstack-tools skills/devstack-tools/references skills/devstack-mail skills/devstack-playwright; do
  [[ -d $incoming/$directory && ! -L $incoming/$directory ]] ||
    die "Missing or unsafe skill directory: $directory"
done
for file in BUILD manifest.json smoke.cjs bin/playwright bin/playwright-cli bin/devstack-mail \
  mail/cli.py mail/contract.py mail/mailpit.py mail/message.py playwright/package.json \
  skills/devstack-tools/SKILL.md skills/devstack-tools/references/mail.md \
  skills/devstack-mail/SKILL.md skills/devstack-playwright/SKILL.md \
  playwright/chromium-path playwright/package-lock.json playwright/node_modules/@playwright/test/cli.js; do
  [[ -f $incoming/$file && ! -L $incoming/$file ]] || die "Missing release file: $file"
done
[[ "$(cat "$incoming/BUILD")" =~ ^[0-9a-f]{64}$ ]] || die 'Invalid build identifier.'
# Verify the relocated Python entry point and its imports without contacting
# Mailpit. The optional mail service need not be installed or started.
mail_version="$(check_manifest "$incoming")"
[[ $mail_version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die 'Invalid declared DevStack Mail version.'
runuser -u "$owner" -- env -i HOME="$home" PATH=/usr/bin:/bin \
  /usr/bin/python3 -IB "$incoming/mail/cli.py" --version |
  grep -Fx "devstack-mail $mail_version"
# Smoke the relocated candidate as the developer, not root. Preserve the old
# active symlink unless this browser actually starts and closes successfully.
runuser -u "$owner" -- env -i HOME="$home" PATH=/usr/local/bin:/usr/bin:/bin \
  PLAYWRIGHT_BROWSERS_PATH="$incoming/browsers" \
  "$node_bin" "$incoming/smoke.cjs"
for name in bin playwright browsers skills; do
  if [[ ! -L $base/$name ]]; then ln -s "current/$name" "$base/$name"; fi
done
# Only a validated candidate reaches the active pointer. Keep one known-good
# previous release so the terminal rollback can also roll back the catalogue.
if [[ -n $previous ]]; then point_release previous "$previous"; fi
point_release current "$incoming"
if [[ -n $retained && $retained != "$previous" && $retained != "$incoming" ]]; then
  rm -rf --one-file-system -- "$retained" ||
    echo "Warning: old release remains at $retained" >&2
fi
echo "Activated $incoming"
