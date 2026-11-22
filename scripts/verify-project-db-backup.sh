#!/usr/bin/env bash
# Verify a trusted DevStack MariaDB dump by restoring it into a temporary DB.
# This NEVER modifies the project's original database.
# Usage: sudo bash scripts/verify-project-db-backup.sh ~/devstack-backups/<file>.sql.gz
set -Eeuo pipefail
umask 077

if [[ "$#" -ne 1 || ! -f "$1" || -L "$1" ]]; then
  echo 'Usage: sudo bash scripts/verify-project-db-backup.sh <trusted-backup.sql.gz>' >&2
  exit 2
fi
if [[ "$EUID" -ne 0 ]]; then
  echo 'Run this recovery test with sudo.' >&2
  exit 2
fi

archive="$(realpath -- "$1")"
checksum="$archive.sha256"
if [[ ! -f "$checksum" || -L "$checksum" ]]; then
  echo 'Missing .sha256 file; refusing unverified database backup.' >&2
  exit 1
fi
expected="$(head -n 1 "$checksum" | tr -d '\r\n')"
if [[ ! "$expected" =~ ^[a-f0-9]{64}$ ]]; then
  echo 'Invalid SHA-256 checksum file.' >&2
  exit 1
fi
actual="$(sha256sum "$archive" | awk '{print $1}')"
if [[ "$actual" != "$expected" ]]; then
  echo 'Backup checksum mismatch; restore test cancelled.' >&2
  exit 1
fi
gzip -t "$archive"

# This executes SQL: accept only trusted backups made by this installation.
# A checksum protects against corruption, not a maliciously constructed dump.
echo 'Only run this command on a trusted dump created on your own workstation.'
temp_db="devstackverify$(python3 -c 'import secrets; print(secrets.token_hex(8))')"
created=0
cleanup() {
  if [[ "$created" -eq 1 ]]; then
    mariadb --protocol=socket -e "DROP DATABASE IF EXISTS \`$temp_db\`;" ||
      echo "WARNING: temporary verification database $temp_db needs manual cleanup." >&2
  fi
}
trap cleanup EXIT

mariadb --protocol=socket -e "CREATE DATABASE \`$temp_db\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
created=1
gzip -dc -- "$archive" | mariadb --protocol=socket --database="$temp_db"
tables="$(mariadb --protocol=socket --batch --skip-column-names -e \
  "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='$temp_db';")"
echo "Restore test passed. Temporary database: $temp_db; restored tables/views: $tables."
echo 'The original managed project database was not modified.'
