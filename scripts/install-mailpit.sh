#!/usr/bin/env bash
# Optional first installation of the official, checksum-pinned Mailpit release.
set -Eeuo pipefail
umask 077
if [[ "$EUID" -ne 0 || -z "${SUDO_USER:-}" || "$SUDO_USER" == root ]]; then
  echo 'Use: sudo bash scripts/install-mailpit.sh (as your developer account).' >&2
  exit 2
fi
if [[ "$(ps -p 1 -o comm=)" != systemd ]]; then
  echo 'Mailpit requires systemd.' >&2
  exit 2
fi
version=v1.34.0
case "$(dpkg --print-architecture)" in
  amd64) arch=amd64; digest=af236b42f171b1c74f0ac5ff444f44d36f2f2ecc0134f5a2f1d4e307e56a0d2c ;;
  arm64) arch=arm64; digest=179f3fe9642edb3a123edc613003e45641869c5b2728c2076c7bef1c3e09dd01 ;;
  *) echo 'Unsupported Mailpit architecture.' >&2; exit 2 ;;
esac
dest=/opt/devstack-mailpit
unit=devstack-mailpit.service
unitfile=/etc/systemd/system/$unit
if [[ -e "$dest" || -L "$dest" || -e "$unitfile" || -L "$unitfile" ]] || systemctl cat "$unit" >/dev/null 2>&1; then
  echo 'Existing or partial DevStack Mailpit installation; refusing overwrite.' >&2
  exit 1
fi
python3 - <<'PY'
import socket
for port in (1025, 8025):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            raise SystemExit(f"Local port {port} is unavailable; no installation changes made.")
PY
stage="$(mktemp -d /opt/.devstack-mailpit.XXXXXXXX)"
installed=0
unit_installed=0
success=0
cleanup() {
  local rc=$?
  trap - EXIT
  set +e
  if [[ "$success" != 1 ]]; then
    if [[ "$unit_installed" == 1 ]]; then
      systemctl disable --now "$unit" >/dev/null 2>&1
      rm -f -- "$unitfile"
      systemctl daemon-reload
    fi
    if [[ "$installed" == 1 ]]; then
      rm -f -- "$dest/mailpit" "$dest/VERSION"
      rmdir -- "$dest"
    fi
    echo 'Mailpit setup failed; project files and mailbox data were not deleted.' >&2
  fi
  rm -rf -- "$stage"
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
echo "Downloading official Mailpit $version ($arch)..."
curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
  --max-time 120 --retry 2 --output "$stage/mailpit.tar.gz" \
  "https://github.com/axllent/mailpit/releases/download/$version/mailpit-linux-$arch.tar.gz"
printf '%s  %s\n' "$digest" "$stage/mailpit.tar.gz" | sha256sum --check --status
# Extract exactly one expected regular file, never arbitrary archive paths.
python3 - "$stage/mailpit.tar.gz" "$stage/mailpit" <<'PY'
import pathlib
import shutil
import sys
import tarfile
with tarfile.open(sys.argv[1], "r:gz") as archive:
    members = [member for member in archive.getmembers() if member.name in ("mailpit", "./mailpit")]
    if len(members) != 1 or not members[0].isfile() or not 0 < members[0].size < 64 * 1024 * 1024:
        raise SystemExit("Unexpected Mailpit release archive.")
    with archive.extractfile(members[0]) as source, pathlib.Path(sys.argv[2]).open("xb") as target:
        shutil.copyfileobj(source, target)
PY
printf '%s\n' "$version" > "$stage/VERSION"
cat > "$stage/$unit" <<'UNIT'
[Unit]
Description=DevStack local test mailbox (Mailpit)
After=network.target

[Service]
Type=simple
DynamicUser=yes
StateDirectory=devstack-mailpit
StateDirectoryMode=0700
ExecStart=/opt/devstack-mailpit/mailpit --listen 127.0.0.1:8025 --smtp 127.0.0.1:1025 --allowed-hosts localhost,127.0.0.1 --database /var/lib/devstack-mailpit/mailpit.db --label DevStack --max 500 --max-message-size 10 --disable-version-check --block-remote-css-and-fonts --smtp-disable-rdns
Restart=on-failure
RestartSec=3s
UMask=0077
NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectSystem=strict
ProtectHome=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
IPAddressDeny=any
IPAddressAllow=localhost
MemoryMax=512M
TasksMax=128

[Install]
WantedBy=multi-user.target
UNIT
mkdir -m 0755 -- "$dest"
installed=1
install -m 0755 -o root -g root "$stage/mailpit" "$dest/mailpit"
install -m 0644 -o root -g root "$stage/VERSION" "$dest/VERSION"
unit_installed=1
install -m 0644 -o root -g root "$stage/$unit" "$unitfile"
systemctl daemon-reload
systemctl enable --now "$unit"
ready=0
for attempt in {1..15}; do
  if systemctl is-active --quiet "$unit" && curl --noproxy '*' --fail --silent --max-time 2 \
      'http://127.0.0.1:8025/api/v1/messages?limit=1' >/dev/null; then
    ready=1
    break
  fi
  sleep 1
done
if [[ "$ready" != 1 ]]; then
  echo 'Local Mailpit API did not become ready.' >&2
  exit 1
fi
success=1
echo "Mailpit $version installed. SMTP: 127.0.0.1:1025. Open E-mail in DevStack."
echo 'Existing projects and mail settings have not been changed.'
