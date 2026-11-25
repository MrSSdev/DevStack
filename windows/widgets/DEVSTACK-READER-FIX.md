# Service reader: D-Bus compatibility

Current installer bundles include this correction. See [installation](README.md#installation) and [technical reference](DEVSTACK-0.4.md).

## Symptom

An original 0.4.0 integration could have active socket listeners and a successful control health response, while its read endpoint returned HTTP 503. Running `systemctl show` as an ordinary account worked, but inside the dynamically identified reader it failed with `Failed to get properties: Transport endpoint is not connected`.

Local NSS resolution of a temporary user does not prove D-Bus accepts that identity. The observed failure is consistent with the [documented systemd/D-Bus compatibility issue](https://github.com/systemd/systemd/issues/22737); the message alone is not a diagnosis for every machine.

## Current implementation

Setup creates or validates the local account/group `devstack-widget-reader`, with a disabled password, nologin shell, no home and no supplementary groups. The reader unit selects that account explicitly. `DynamicUser=yes` is retained for sandbox defaults while the preallocated identity is reused. Existing conflicting accounts are rejected, not repurposed.

The reader still runs without root. The correction does not change D-Bus policy, NSS settings, file permissions on protected project records, or control-service privileges. Setup reports the failing stage and HTTP status without printing tokens. Automated tests include real systemd/D-Bus access under the reader policy, in addition to unit tests.

## Upgrading a failed initial setup

Use a complete current bundle. With Ubuntu deliberately running, rerun `Install-DevStackWidget.ps1` only when the integration was not successfully configured or the release explicitly requires the correction. Keep the matching Windows MSIX/CER together and install it separately through `Install-Widgets.ps1`. Do not rerun working WSL telemetry setup or overwrite the current reader with an old original archive.

Successful integration setup and MSIX installation are distinct steps. A listening socket alone does not prove the reader works; look for the setup health-check success and protected `services.json` creation. No application restart is performed by those health checks.
