#!/usr/bin/env bash
# Explicit opt-in after the Windows component and the Ubuntu update succeed.
# The marker is the sole transport selector; publishing it is atomic.
set -Eeuo pipefail
umask 077
if [[ "$EUID" -ne 0 || "$#" -ne 0 ]]; then
  echo 'Run only as the privileged fixed-path enable step of scripts/update.sh.' >&2
  exit 2
fi
config=/etc/devstack
marker="$config/windows-local-notifications"
launcher=/mnt/c/DevStack/LocalNotifications/DevStack.Notifications.exe
if [[ ! -d "$config" || -L "$config" || ! -f "$launcher" || ! -f /opt/devstack/daemon/devstackd.py ]]; then
  echo 'The DevStack configuration or installed local launcher is missing/unsafe.' >&2
  exit 1
fi
# Confirm that deployed code supports the new path before opting in.
if ! grep -Fq 'WINDOWS_NOTIFICATION_TARGET = ' /opt/devstack/daemon/devstackd.py; then
  echo 'Install the corresponding Ubuntu update first.' >&2
  exit 1
fi
if [[ -e "$marker" || -L "$marker" ]]; then
  if [[ ! -f "$marker" || -L "$marker" || "$(stat -c %u "$marker")" != 0 ]]; then
    echo 'Unsafe existing transport marker; refusing replacement.' >&2
    exit 1
  fi
  permissions="$(stat -c %a "$marker")"
  if (( (8#$permissions & 022) != 0 )); then
    echo 'Existing transport marker is writable by a non-root account.' >&2
    exit 1
  fi
  if [[ "$(cat "$marker")" == "$launcher" ]]; then
    echo 'Windows local notifications already enabled.'
    exit 0
  fi
fi
temporary="$(mktemp "$config/.windows-local-notifications.XXXXXXXX")"
trap 'rm -f -- "$temporary"' EXIT
printf '%s\n' "$launcher" > "$temporary"
chown root:root "$temporary"
chmod 0644 "$temporary"
# Migration must not reset the initial opt-in cutoff. On first enable, the
# new file's timestamp excludes the pre-opt-in backlog.
if [[ -f "$marker" ]]; then
  touch -r "$marker" "$temporary"
fi
mv -T -- "$temporary" "$marker"
trap - EXIT
echo 'Windows local notifications enabled.'
