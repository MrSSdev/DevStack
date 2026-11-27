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
[[ $action == publish || $action == remove ]] || die 'Unknown operation.'
[[ $action != publish || $# -eq 3 ]] || die 'Expected a stage and Node executable.'
[[ $action != remove || $# -eq 1 ]] || die 'Unexpected arguments.'
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
flock -xn 9 || die 'Tools are in use. Close active tests and CLI sessions first.'
previous=''
if [[ -e $base/current || -L $base/current ]]; then
  [[ -L $base/current ]] || die 'current must be a managed symlink.'
  previous="$(readlink -f -- "$base/current")"
  [[ $previous =~ ^/opt/devstack-tools/releases/release-[a-zA-Z0-9]{8}$ && -d $previous && ! -L $previous ]] ||
    die 'Unsafe current release.'
fi
for name in bin playwright browsers; do
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
incoming="$(mktemp -d "$base/releases/release-XXXXXXXX")"
next=''
committed=false
cleanup() {
  [[ -z $next ]] || rm -f -- "$next"
  if ! $committed; then rm -rf --one-file-system -- "$incoming"; fi
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
for file in BUILD smoke.cjs bin/playwright bin/playwright-cli playwright/package.json \
  playwright/package-lock.json playwright/node_modules/@playwright/test/cli.js; do
  [[ -f $incoming/$file && ! -L $incoming/$file ]] || die "Missing release file: $file"
done
[[ "$(cat "$incoming/BUILD")" =~ ^[0-9a-f]{64}$ ]] || die 'Invalid build identifier.'
# Smoke the relocated candidate as the developer, not root. Preserve the old
# active symlink unless this browser actually starts and closes successfully.
runuser -u "$owner" -- env -i HOME="$home" PATH=/usr/local/bin:/usr/bin:/bin \
  PLAYWRIGHT_BROWSERS_PATH="$incoming/browsers" \
  "$node_bin" "$incoming/smoke.cjs"
for name in bin playwright browsers; do
  if [[ ! -L $base/$name ]]; then ln -s "current/$name" "$base/$name"; fi
done
# Atomic activation on the same filesystem. The lock protects launchers.
next="$base/.current-${incoming##*/}"
ln -s "releases/${incoming##*/}" "$next"
mv -Tf -- "$next" "$base/current"
committed=true
# No permanent duplicate browser copies. Only remove the previously managed
# release, after activation; no project data is stored in either release.
if [[ -n $previous && $previous != $incoming ]]; then
  rm -rf --one-file-system -- "$previous" ||
    echo "Warning: old release remains at $previous" >&2
fi
echo "Activated $incoming"
