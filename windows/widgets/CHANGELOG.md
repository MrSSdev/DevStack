# Widget release notes

Package versions are independent of the DevStack dashboard version. For current setup and supported configuration, use [README](README.md), not historical installation snippets.

## 0.4.2.0

- Ubuntu resource details dismiss on outside click within the card, explicit close or Escape; clicks inside retain the details.
- DevStack restart warnings resolve only after a fresh healthy read of the same service. Recovery/availability messages expire after 30 seconds and do not claim an unconfirmed restart succeeded.
- Matching installer resets the Widgets Board and provider after verified package updates. This installer maintenance does not change the 0.4.2.0 application binaries.

## 0.4.1.0

- Service details close after explicit restart confirmation.
- Inline, service-specific progress beneath the project count replaces the overlapping persistent toast.
- Fresh post-command verification determines success; successful notices use absolute 30-second expiry.

## 0.4.0.0

- DevStack services card uses packaged HTML with a separate native message/resource boundary.
- Response time, service cgroup RAM/CPU, total project count, browser deep links and guarded restarts.
- Dedicated socket-activated Linux read/control integrations. Reader setup includes the fixed local non-login identity for D-Bus compatibility; old original bundles may need the reader correction.

## 0.3.x

- Ubuntu card moved to locally packaged HTML and a native control bridge, with authenticated resource readings.
- Bridge startup/recovery and message-call compatibility fixes.
- Dark outer fill made transparent. A temporary 0.3.4 white overlay was reverted in 0.3.5. Short native loading transitions remain possible.

## 0.2.x and earlier

- Initial COM activation, two separate cards, passive running-state/network probes and Adaptive Card layouts.
- Historical implementations are not current installation instructions.
