#!/usr/bin/env bash
# Build/test an interim distribution-package backport. Disposable CI ONLY.
# No production installer, system source-list changes, global sandbox weakening,
# or fallback to an unsigned archive.
set -Eeuo pipefail
umask 022
if (( EUID == 0 )) || (( $# != 0 )) ||
   [[ "${GITHUB_ACTIONS:-}" != true || "${RUNNER_ENVIRONMENT:-}" != github-hosted ]]; then
    echo 'Only for an ordinary user on a disposable GitHub-hosted CI runner.' >&2
    exit 2
fi
. /etc/os-release
if [[ "$ID" != ubuntu || "$VERSION_ID" != 26.04 || "$(dpkg --print-architecture)" != amd64 ]] ||
   grep -qi microsoft /proc/sys/kernel/osrelease; then
    echo 'Expected disposable Ubuntu 26.04 amd64, not WSL.' >&2
    exit 2
fi
root="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT
source_version=0.13.1-1
version=0.13.1-1~ubuntu26.04.1+devstack1
mkdir "$work/source" "$work/lists" "$work/cache" "$work/rollback" "$work/unpacked"

# Install binary dependencies exclusively from the runner's original LTS
# sources, never from tenacious or another release.
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
    ubuntu-keyring build-essential debhelper devscripts fakeroot \
    bash-completion docbook-xml docbook-xsl iproute2 libcap-dev libcap2-bin \
    libipc-run-perl libselinux-dev meson perl pkgconf python3 xsltproc \
    bubblewrap php8.5-fpm php8.5-sqlite3 nginx libfcgi-bin acl curl
old_version="$(dpkg-query -W -f='${Version}' bubblewrap)"
dpkg --compare-versions "$version" gt "$old_version"
(cd "$work/rollback" && apt-get download "bubblewrap=$old_version")
original="$work/rollback/bubblewrap_${old_version}_amd64.deb"
test -f "$original"

# Authenticate the newer *source index* with Ubuntu's archive keyring.
# The separate list/cache directory is temporary and contains NO deb line.
# No write to /etc/apt, no newer-suite binary indexes, no trust bypass.
printf '%s\n' \
    'deb-src [signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] https://archive.ubuntu.com/ubuntu tenacious main' \
    > "$work/source.list"
opts=(
    -o "Dir::Etc::sourcelist=$work/source.list"
    -o "Dir::Etc::sourceparts=-"
    -o "Dir::State::lists=$work/lists"
    -o "Dir::Cache=$work/cache"
    -o "Acquire::AllowInsecureRepositories=false"
    -o "Acquire::AllowDowngradeToInsecureRepositories=false"
    -o "APT::Get::AllowUnauthenticated=false"
)
apt-get "${opts[@]}" update
cd "$work/source"
apt-get "${opts[@]}" source --download-only --only-source "bubblewrap=$source_version"

# Additionally pin the orig archive to the SHA-256 printed in the
# verified upstream v0.13.1 release. A repack/different archive must be
# reviewed explicitly rather than weakening verification.
printf '%s  %s\n' \
    0e85c8702248b8dd64144f594147553650cdd0e4c951690345faace79c217c4d \
    bubblewrap_0.13.1.orig.tar.xz | sha256sum --check
sha256sum bubblewrap_0.13.1-1.dsc bubblewrap_0.13.1.orig.tar.xz \
    bubblewrap_0.13.1-1.debian.tar.xz > "$work/source-sha256.txt"
dpkg-source -x bubblewrap_0.13.1-1.dsc "$work/build"
cd "$work/build"
export DEBFULLNAME='DevStack CI' DEBEMAIL='ci@example.invalid'
dch --force-distribution --distribution resolute --newversion "$version" \
    'Test-only rebuild of authenticated Ubuntu source for Ubuntu 26.04 LTS.'
dpkg-checkbuilddeps
# Full package build, including the upstream/package test suite; no nocheck.
dpkg-buildpackage -us -uc -b -j2

candidate="$work/bubblewrap_${version}_amd64.deb"
test -f "$candidate"
test "$(dpkg-deb -f "$candidate" Package)" = bubblewrap
test "$(dpkg-deb -f "$candidate" Version)" = "$version"
test "$(dpkg-deb -f "$candidate" Architecture)" = amd64
dpkg-deb -x "$candidate" "$work/unpacked"
test -f "$work/unpacked/usr/bin/bwrap"
test ! -L "$work/unpacked/usr/bin/bwrap"
test "$(stat -c '%a' "$work/unpacked/usr/bin/bwrap")" = 755

# APT-managed install and rollback on the disposable runner only.
# Fail if this would remove another package.
sudo apt-get --simulate --no-remove install "$candidate"
sudo apt-get -y --no-remove install "$candidate"
test "$(dpkg-query -W -f='${Version}' bubblewrap)" = "$version"
test "$(stat -c '%a %u %g' /usr/bin/bwrap)" = '755 0 0'
test -z "$(getcap /usr/bin/bwrap)"
test "$(/usr/bin/bwrap --version)" = 'bubblewrap 0.13.1'
cd "$root"
bash tests/project-sandbox-probe.sh
sudo env GITHUB_ACTIONS=true DEVSTACK_RUNTIME_CI=1 \
    python3 -I tests/project-runtime-systemd-smoke.py

sudo apt-get -y --allow-downgrades --no-remove install "$original"
test "$(dpkg-query -W -f='${Version}' bubblewrap)" = "$old_version"
echo 'PASS: Installed candidate, isolated runtime, APT rollback'

# Publish evidence only after every check. A PR artifact is not a trusted
# workstation release. Final approval requires review + a main workflow
# run and target WSL/official tool compatibility checks.
out="$root/bubblewrap-backport-ci-output"
mkdir "$out"
cp "$candidate" "$out/"
cp "$work/source-sha256.txt" "$out/"
cp "$work/source/bubblewrap_0.13.1-1.dsc" "$out/"
cp "$work/"*.buildinfo "$work/"*.changes "$out/"
printf 'source=%s\ncandidate=%s\nrollback=%s\nsuite=resolute\narch=amd64\ncommit=%s\n' \
    "$source_version" "$version" "$old_version" "$GITHUB_SHA" > "$out/provenance.txt"
(cd "$out" && sha256sum -- *.deb *.dsc *.buildinfo *.changes *.txt > SHA256SUMS)
