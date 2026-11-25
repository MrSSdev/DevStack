# 0.4.0 integration reader: static identity for D-Bus compatibility

## Device evidence, 25 November 2026

The ASUS test of the original 0.4.0 bundle found working application units,
HTTP control health 200 and reader HTTP 503. In a transient unit with exactly
the reader policy, auth and project credentials were readable and the dynamic
UID resolved locally. The first real systemctl show failed in 6 ms with
`Failed to get properties: Transport endpoint is not connected`.
No Windows services.json had been saved; installed MSIX was still 0.3.5.0.

This matches the documented dbus-daemon/DynamicUser incompatibility:
https://github.com/systemd/systemd/issues/22737
Local NSS success does not establish that the bus daemon accepts the dynamic
identity. The supplied diagnostic does not identify the exact dbus build or
prove all host internals. A successful post-fix ASUS read remains required.

## Narrow repair

Setup creates/reuses a LOCAL system user and primary group named
`devstack-widget-reader`, with disabled password, nologin shell, no home and
no supplementary groups. Existing conflicting identities are rejected, not
repurposed. It uses systemd-sysusers with one inline entry, not usermod/sudo.

The read unit explicitly selects that user/group. DynamicUser=yes is retained
for its sandbox defaults, but systemd reuses the preallocated static identity
instead of allocating a transient UID. This behaviour is documented under
DynamicUser in systemd.exec. All existing sandbox flags and LoadCredential
remain. No root reader, D-Bus restart/replacement, NSS change, broad bus policy
or chmod of protected credentials/projects is needed.

Setup now reports the failing stage and numeric HTTP status without tokens or
response bodies. Bridge/control logic, ports, WSL telemetry, Windows UI,
authorization and signed MSIX are unchanged. Health checks never restart
Nginx/PHP/MariaDB/Redis. After healthy read/control checks the existing installer
continues the previously authorized static frontend update and Windows config.

## Delivery

A small overlay contains exactly services/setup.py and
services/devstack-widget-services@.service. Extract it over the complete
0.4.0 bundle, preserving MSIX/CER/frontend and all other files. Rerun the
existing Install-DevStackWidget.ps1 while Ubuntu is already running. Do not
rerun Install-WslTelemetry.ps1 or reset Ubuntu. Install the existing signed
0.4.0 Windows package separately; extraction alone does not install it.
Do not re-extract the old full ZIP over the overlay afterward.

## Verification

Unit tests cover local-account creation/reuse, non-root/non-login constraints,
conflicts and preserved isolation, alongside the existing request/auth/replay
suite. A separate CI workflow runs the unchanged reader with the exact sandbox
against REAL systemd/D-Bus and real cgroup readings. It uses a harmless runner
unit and fixture credentials/project records, not application restarts. This
is not an ASUS/WSL test and must not be reported as device acceptance.

Public main remains unchanged. The Windows widget implementation is not being
redesigned; the user-accepted spinner/background tradeoff stays closed.
