---
name: devstack-mail
description: Use when a trusted local DevStack test expects an email after registration, address verification, password reset, or invitations, or when asked to read a specific local test message.
---

# DevStack Mail

Use the command contract and examples in
`/opt/devstack-tools/skills/devstack-tools/references/mail.md`.
Run `/opt/devstack-tools/bin/devstack-mail status --json` first. A stopped Mailpit
blocks mail assertions until the developer makes it available.

For Laravel registration in Playwright:

1. Get a unique recipient per run, worker and retry. Capture the UTC start time
   before submitting the registration form through the UI.
2. Run `wait --to RECIPIENT --since START --timeout 30 --json`, with
   `--subject SUBJECT` when the exact subject is known. Use the returned
   `message.id` with `get ID --origin https://app.test --json`.
3. Inspect the returned links with an URL parser. Require the exact registered
   origin and the application's verification route, such as
   `/email/verify/{id}/{hash}`. Navigate explicitly with the original matching
   string so Laravel receives the same signed query and percent encoding.
4. Wait for verified state in the app. For password reset, use the app's reset
   route and the same recipient and origin checks. Keep token-bearing URLs,
   message bodies, and test credentials out of assertions with raw values,
   logs, screenshots, traces and reports.

DevStack Mail reads without setting the Mailpit read flag. It does not open
links. The inbox is shared by trusted local developers. Recipient and origin
filters do not grant access. Managed isolated agents must wait for the
DevStack permission broker.
