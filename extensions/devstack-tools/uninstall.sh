#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -ne 0 && $# -eq 1 && $1 == --confirm ]] ||
  { echo 'Run as the developer: bash uninstall.sh --confirm' >&2; exit 2; }
exec sudo -- /bin/bash "$(dirname -- "${BASH_SOURCE[0]}")/publish.sh" remove
