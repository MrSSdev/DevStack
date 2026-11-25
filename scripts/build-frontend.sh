#!/usr/bin/env bash
set -Eeuo pipefail
if [[ "$EUID" -eq 0 ]]; then
  echo 'Do not build frontend or run npm as root.' >&2
  exit 1
fi
cd "$(dirname "${BASH_SOURCE[0]}")/../frontend"
if [[ -f package-lock.json ]]; then
  npm ci --no-audit --no-fund
else
  npm install --no-audit --no-fund
fi
npm run build
printf 'Built static frontend in backend/public. No Node.js server is required at runtime.\n'
