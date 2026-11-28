---
name: devstack-playwright
description: Use for end-to-end tests and interactive verification of local DevStack web apps, including registration, password reset, and other flows that send local mail.
---

# DevStack Playwright

- Use the registered application's local origin and its normal browser flows.
  Run tests with `/opt/devstack-tools/bin/playwright test` from the project.
  In the Playwright config select Chromium with `channel: 'chromium'`.
- For interactive inspection use `/opt/devstack-tools/bin/playwright-cli`
  with a unique named session. Close that session when finished. Keep TLS
  verification enabled and use the project's normal DevStack certificate.
- Use an ephemeral test account and a unique recipient per worker and retry.
  Record the test start before triggering registration. If the project uses
  local mail and the trusted developer session is authorized to read it,
  use the [DevStack Mail skill](../devstack-mail/SKILL.md) for verification or
  reset messages.
- Keep credentials, messages and private URLs out of logs and artifacts.
  Disable traces, video and captures for the portion of the flow that receives
  or navigates with a signed URL.
