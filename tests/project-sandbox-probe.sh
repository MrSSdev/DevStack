#!/usr/bin/env bash
# Capability probe only: fixed commands, disposable fixtures, no project access.
set -Eeuo pipefail

if (( EUID == 0 )) || (( $# != 0 )); then
    echo 'Run this fixture probe as an ordinary user, with no arguments.' >&2
    exit 1
fi
if [[ ! -x /usr/bin/bwrap ]]; then
    echo 'Bubblewrap is not installed at /usr/bin/bwrap.' >&2
    exit 1
fi

umask 077
probe=$(mktemp -d /tmp/devstack-sandbox-probe.XXXXXXXX)
trap 'rm -rf -- "$probe"' EXIT
mkdir "$probe/allowed" "$probe/denied"
printf 'allowed\n' > "$probe/allowed/input.txt"
printf 'denied\n' > "$probe/denied/marker.txt"
ln -s "$probe/denied/marker.txt" "$probe/allowed/outside-link"

host_net=$(readlink /proc/self/ns/net)
host_pid=$(readlink /proc/self/ns/pid)
host_user=$(readlink /proc/self/ns/user)
export DEVSTACK_PROBE_HOST_ONLY=sentinel
/usr/bin/bwrap --version

# No fallback without a required namespace and no host-wide configuration changes.
sandbox=(
    /usr/bin/bwrap
    --unshare-user
    --unshare-pid
    --unshare-net
    --unshare-ipc
    --unshare-uts
    --disable-userns
    --die-with-parent
    --new-session
    --ro-bind /usr /usr
    --symlink usr/bin /bin
    --symlink usr/lib /lib
    --symlink usr/lib64 /lib64
    --proc /proc
    --dev /dev
    --tmpfs /tmp
    --dir /home
    --dir /mnt
    --dir /run
    --chdir /work
    --clearenv
    --setenv HOME /tmp
    --setenv PATH /usr/bin:/bin
)

timeout --kill-after=2s 20s "${sandbox[@]}" \
    --bind "$probe/allowed" /work \
    -- /usr/bin/bash -c '
        set -eu
        check() {
            local label=$1
            shift
            if "$@"; then
                printf "PASS: %s\n" "$label"
            else
                printf "FAIL: %s\n" "$label" >&2
                exit 1
            fi
        }
        check "Project read" test "$(cat /work/input.txt)" = allowed
        printf "ok\n" > /work/output.txt
        echo "PASS: Project write"
        check "Other project hidden" test ! -e "$1"
        check "Symlink present" test -L /work/outside-link
        check "Symlink escape blocked" test ! -e /work/outside-link
        check "Host home hidden" test -z "$(ls -A /home)"
        check "Windows mount hidden" test ! -e /mnt/c
        check "DevStack socket hidden" test ! -e /run/devstack
        check "PHP sockets hidden" test ! -e /run/php
        check "Network namespace isolated" \
            test "$(readlink /proc/self/ns/net)" != "$2"
        check "PID namespace isolated" \
            test "$(readlink /proc/self/ns/pid)" != "$3"
        check "User namespace isolated" \
            test "$(readlink /proc/self/ns/user)" != "$4"
        check "Host environment cleared" \
            test -z "${DEVSTACK_PROBE_HOST_ONLY+x}"
        if unshare --user true 2>/dev/null; then
            echo "FAIL: Nested user namespace" >&2
            exit 1
        fi
        echo "PASS: Nested user namespace blocked"
    ' bash "$probe/denied/marker.txt" "$host_net" "$host_pid" "$host_user"

test "$(cat "$probe/allowed/output.txt")" = ok
test "$(cat "$probe/denied/marker.txt")" = denied
echo 'PASS: Host verified fixture contents'

timeout --kill-after=2s 20s "${sandbox[@]}" \
    --ro-bind "$probe/allowed" /work \
    -- /usr/bin/bash -c '
        set -eu
        test "$(cat /work/input.txt)" = allowed
        if (printf "unexpected\n" > /work/read-only-marker) 2>/dev/null; then
            echo "FAIL: Read-only project accepted a write" >&2
            exit 1
        fi
        echo "PASS: Read-only project rejects writes"
    '
test ! -e "$probe/allowed/read-only-marker"
echo '=== SANDBOX PROBE PASSED ==='
