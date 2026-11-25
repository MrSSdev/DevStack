#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo 'Requires Git checkout for tracked-file scan' >&2
  exit 1
fi
if git ls-files | grep -E '(^|/)(\.env(\..*)?|credentials\.json|auth\.hash|rootCA-key\.pem|id_rsa|id_ed25519|.*\.(pem|pfx|p12|key|sqlite|db))$' >/dev/null; then
  echo 'Potential private file committed to public repository.' >&2
  exit 1
fi
if git grep -n -E -- '-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----' -- . ':!scripts/security-scan.sh'; then
  echo 'Private key content found in repository.' >&2
  exit 1
fi
echo 'Public repository secret guard: OK (heuristic, not a full secret scanner)'
