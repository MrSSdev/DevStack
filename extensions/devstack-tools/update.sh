#!/usr/bin/env bash
set -Eeuo pipefail
exec /bin/bash "$(dirname -- "${BASH_SOURCE[0]}")/install.sh" "$@"
