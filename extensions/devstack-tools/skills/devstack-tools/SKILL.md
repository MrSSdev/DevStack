---
name: devstack-tools
description: Use when writing or running Playwright or other end-to-end tests for a DevStack project, especially registration, address verification, password reset, invitations, or other actions that send local email. Also use when asked to search or read local test email or inspect signed links. Covers dedicated DevStack Tools commands and when to use them.
---

# DevStack Tools

Use the reference for the dedicated developer tool relevant to the task.
Use the installed command paths so the workflow is independent of the app
framework and the shell's PATH.

- [DevStack Mail](references/mail.md) applies when a test expects an email
  as an outcome or when asked to check a known local test message. For a
  Playwright flow, use the browser to trigger the application action and
  DevStack Mail to verify the resulting message.

Check tool readiness with its documented command. If a required tool or
service is unavailable, report what is blocked and leave service management
to the developer.

Use the mailbox only in a trusted local developer session explicitly
authorized for the requested check. A managed agent sandbox needs an
authorized broker before it can read mail. Skill discovery and test
selectors do not grant access to the common mailbox.
