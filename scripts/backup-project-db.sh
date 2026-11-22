#!/usr/bin/env bash
# Back up one DevStack-managed MariaDB database without exposing credentials.
# Produces a user-owned 0600 gzip dump under ~/devstack-backups.
# Usage: sudo bash scripts/backup-project-db.sh <project-slug>
set -Eeuo pipefail
umask 077

if [[ "$#" -ne 1 ]]; then
  echo 'Usage: sudo bash scripts/backup-project-db.sh <project-slug>' >&2
  exit 2
fi
slug="$1"
if [[ ! "$slug" =~ ^[a-z][a-z0-9-]{1,39}$ ]]; then
  echo 'Invalid project slug.' >&2
  exit 2
fi
if [[ "$EUID" -ne 0 ]] || [[ ! -v SUDO_USER ]] || [[ "$SUDO_USER" == root ]]; then
  echo 'Run with sudo from your ordinary Ubuntu developer account.' >&2
  exit 2
fi

dev_user="$SUDO_USER"
dev_group="$(id -gn "$dev_user")"
dev_home="$(getent passwd "$dev_user" | cut -d: -f6)"
if [[ "$dev_home" != "/home/$dev_user" || ! -d "$dev_home" ]]; then
  echo 'Unexpected developer home directory; refusing to write backup.' >&2
  exit 1
fi

# Validate against DevStack state and derive the expected database name.
# No password or arbitrary SQL/database name comes from arguments.
db_name="$(python3 - "$slug" <<'PY'
import hashlib
import json
import pathlib
import sys

slug = sys.argv[1]
state_file = pathlib.Path('/var/lib/devstack/projects.json')
state = json.loads(state_file.read_text(encoding='utf-8'))
project = state.get(slug)
if not isinstance(project, dict) or project.get('database') is not True:
    sys.exit('Project is missing or does not have a managed MariaDB database.')
short = slug.replace('-', '')[:21]
expected = 'dev' + short + hashlib.sha256(slug.encode('ascii')).hexdigest()[:8]
if project.get('db_name') != expected:
    sys.exit('Managed database identifier does not match DevStack state.')
print(expected)
PY
)"

if ! mariadb --batch --skip-column-names -e \
  "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='$db_name';" |
    grep -Fxq "$db_name"; then
  echo 'Managed MariaDB database is missing; refusing to generate an empty backup.' >&2
  exit 1
fi

target="$dev_home/devstack-backups"
if [[ -L "$target" || ( -e "$target" && ! -d "$target" ) ]]; then
  echo 'Unsafe backup destination. Expected an ordinary directory, not a symlink.' >&2
  exit 1
fi
install -d -m 0700 -o "$dev_user" -g "$dev_group" "$target"
chmod 0700 "$target"

temp_file="$(mktemp -- "$target/.$slug-XXXXXXXX")"
trap 'rm -f -- "$temp_file"' EXIT
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
identifier="$(python3 -c 'import secrets; print(secrets.token_hex(4))')"
out="$target/$slug-$stamp-$identifier.sql.gz"
if [[ -e "$out" || -L "$out" ]]; then
  echo 'Backup filename collision; refusing overwrite.' >&2
  exit 1
fi

# No --databases flag: dump does not select or create the original schema.
# Avoid concurrent migrations or schema changes while taking a logical backup.
mariadb-dump --protocol=socket --single-transaction --quick \
  --routines --events --triggers --hex-blob "$db_name" | gzip -9 > "$temp_file"
gzip -t "$temp_file"
test -s "$temp_file"
chown "$dev_user:$dev_group" "$temp_file"
chmod 0600 "$temp_file"
mv -- "$temp_file" "$out"
sha256sum "$out" | awk '{print $1}' > "$out.sha256"
chown "$dev_user:$dev_group" "$out.sha256"
chmod 0600 "$out.sha256"
echo "Backup MariaDB complete: $out"
echo "SHA-256: $out.sha256 (hash only)"
echo 'The dump contains application data. Keep it secure and copy it off the WSL virtual disk.'
