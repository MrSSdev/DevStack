#!/usr/bin/env bash
# Inspect existing opt-in or publish it after Windows and Ubuntu updates.
# The marker is the sole transport selector; publishing it is atomic.
set -Eeuo pipefail
umask 077
status_only=0
if [[ "${1:-}" == --status ]]; then
  status_only=1
  shift
fi
if [[ "$EUID" -ne 0 || "$#" -ne 0 ]]; then
  echo 'Run only as the privileged fixed-path status/enable step of scripts/update.sh.' >&2
  exit 2
fi
config=/etc/devstack
marker="$config/windows-local-notifications"
launcher=/mnt/c/DevStack/LocalNotifications/DevStack.Notifications.exe
if [[ ! -d "$config" || -L "$config" || "$(stat -c %u "$config")" != 0 ]]; then
  echo 'The DevStack configuration directory is missing/unsafe.' >&2
  exit 1
fi
config_permissions="$(stat -c %a "$config")"
if (( (8#$config_permissions & 022) != 0 )); then
  echo 'The DevStack configuration directory is writable by a non-root account.' >&2
  exit 1
fi
enabled=0
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
  enabled=1
fi
# A missing launcher does not revoke the recorded consent: a normal
# update must be able to repair it. Status never touches Windows or mtime.
if [[ "$status_only" -eq 1 ]]; then
  if [[ "$enabled" -eq 1 ]]; then echo enabled; else echo disabled; fi
  exit 0
fi
if [[ ! -f "$launcher" || ! -f /opt/devstack/daemon/devstackd.py ]]; then
  echo 'The installed local launcher or Ubuntu helper is missing.' >&2
  exit 1
fi
# Confirm that deployed code supports the new path before opting in.
if ! grep -Fq 'WINDOWS_NOTIFICATION_TARGET = ' /opt/devstack/daemon/devstackd.py; then
  echo 'Install the corresponding Ubuntu update first.' >&2
  exit 1
fi
if [[ "$enabled" -eq 1 && "$(cat "$marker")" == "$launcher" ]]; then
  echo 'Windows local notifications already enabled.'
  exit 0
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
