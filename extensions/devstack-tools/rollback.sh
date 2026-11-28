#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -ne 0 && $# -eq 0 ]] ||
  { echo 'Run as the developer: bash rollback.sh' >&2; exit 2; }
exec sudo -- /bin/bash "$(dirname -- "${BASH_SOURCE[0]}")/publish.sh" rollback
