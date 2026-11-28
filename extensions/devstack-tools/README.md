# DevStack Tools

Optional, independently managed tools for local development. Version 0.3.1 provides Playwright Test and its built-in CLI (1.65.1), DevStack Mail (1.0.0), and separate discoverable instructions for Playwright and Mail. The core installer/updater does not install this extension.

## Panel and project instructions

After updating DevStack Core once, install or update Tools from the terminal
and open **Rozszerzenia → Tools**, or go to
`https://devstack.test/#/extensions/devstack-tools`. The view shows the
currently installed releases, tools, read-only instructions, project
synchronization and automation settings. Routine updates to Tools and its
catalogue do not need a Core upgrade or frontend rebuild.

Core automatically adds `devstack-playwright` to registered web projects and
`devstack-mail` to projects with configured local mail and a compatible
installed Mailpit. If Mailpit stops, the project keeps its mail instruction.
In **Projekty**, inspect the reason, adjust automatic management or retry.
In **Automatyzacje**, configure default management for the extension.
An existing matching manual `devstack-tools` skill keeps working. The panel's
explicit migration removes that legacy link only after the selected
replacements are ready. See the [Core extension guide](../../docs/EXTENSIONS.md).

Close active Playwright tests and CLI sessions before rolling back:

```bash
bash extensions/devstack-tools/rollback.sh
```

Rollback reactivates the prior verified release and its central instructions.
Uninstall removes the menu and the project's DevStack-owned links
as the Core reconciler runs. It preserves manual links and project data
and leaves Mailpit installed. The CLI and manually linked skills remain
usable in a standalone Tools installation without DevStack Core.

## Tools in project details

With compatible Core, open any registered web project's details and use the
compact **DevStack Tools** card. Select an instruction name to preview it in
place. The status icons distinguish managed, manual, selected, pending, and
conflicting instructions. Expand **Szczegóły instrukcji** to see selection
reasons, counts, and the last synchronization time.

Use the switch to set automatic management for the current project and the
refresh button to synchronize or retry. When an exact legacy manual link can
be migrated, the card offers the same confirmation and safeguards as the
global Projects tab. A queued request reports acceptance and the card updates
from Core as synchronization completes. The global Tools tabs remain
available for managing multiple projects.

Install a Core version with project cards before using the contextual controls.
A rollback to Tools 0.3.0 hides the project card and leaves its global Tools view
and established links available. The card returns when a compatible release
with `project-cards.json` is reactivated.

## Install / update

In Ubuntu WSL, from a reviewed DevStack checkout, as the developer (Node.js 22+ and npm required):

~~~bash
cd ~/projects/devstack-source
bash extensions/devstack-tools/install.sh --with-deps
~~~

The explicit --with-deps option lets the upstream Playwright installer install missing system libraries via sudo. npm and browser downloads run as the developer; only publishing the installation uses sudo. The relocated candidate is smoke-tested as the developer before activation. Playwright downloads the full Chromium-family browser with --no-shell, not a second headless shell. Browser revisions follow the pinned package version.

For extension-only updates after reviewing and pulling the new source:

~~~bash
bash extensions/devstack-tools/update.sh
~~~

When DevStack is installed, installation and update automatically trust its existing public CA in the configured developer's Chromium NSS database. This uses the shared core helper, installing libnss3-tools if missing; an older core without the helper must be updated once first. Standalone installation without DevStack skips CA setup. The helper is also available in DevStack Settings → Security → Skopiuj do WSL, or via `sudo python3 -I /opt/devstack/daemon/devstackd.py --trust-wsl-ca`. No Windows certificate store, system-wide Linux trust store or CA private key is modified.

No routine core update, frontend build or service restart. An identical build is a no-op; pass --with-deps if new libraries are needed, or --force for repair. Close active Playwright tests and CLI sessions before updating; an active launcher or detected browser/CLI process blocks replacement. The previous installation remains active if the candidate fails. The active release and one previously verified release are retained. Staging temporarily uses additional disk space.

## Layout

~~~text
/opt/devstack-tools/
  bin/          -> current/bin      playwright, playwright-cli, devstack-mail
  playwright/   -> current/playwright
  browsers/     -> current/browsers
  skills/       -> current/skills
  current       -> releases/release-...
  previous      -> previously verified release, when available
  releases/     root-owned active and previous package/browser releases
~~~

Do not modify the managed directories as root outside the installer. Tools are read-only to ordinary users. Project code, browser profiles, sessions and test reports remain outside the installation. No project gets its own downloaded Chromium copy.

## Use from a project

~~~bash
export PATH="/opt/devstack-tools/bin:$PATH"
playwright --version
playwright test
playwright-cli -s=my-project open https://example.test
playwright-cli -s=my-project snapshot
playwright-cli -s=my-project close
~~~

The PATH change is for this terminal only. No shell profile is modified. Use your actual registered local domain. A project may keep its tests under version control. For central-only installation, CommonJS (.cjs) test/config files can import @playwright/test via the launcher's NODE_PATH; it is not a general ESM module-resolution mechanism. A project that already installs its own Playwright package should keep the versions compatible or use its own test runner. For the shared full browser, configure use: { channel: 'chromium' }; omit the default separate headless shell. Keep normal HTTPS certificate validation and use the developer's trusted local CA. Do not copy CA private keys or disable certificate checks.

Example playwright.config.cjs:

~~~js
const { defineConfig } = require('@playwright/test');
module.exports = defineConfig({
  testDir: './tests/e2e',
  use: {
    baseURL: 'https://example.test',
    channel: 'chromium',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure'
  },
  reporter: 'html'
});
~~~

The built-in CLI uses the same package and shared browser binaries. Its named sessions and temporary/persistent profiles are managed by Playwright under the invoking user's own account; explicitly close sessions after work. Reports, snapshots and traces may contain sensitive application data; do not commit them. No private browser profile is configured or attached.

## Agent instructions for project tests

The `devstack-tools` Agent Skill tells an agent when to use a dedicated
DevStack tool in a project workflow. For example, a Playwright registration
test can trigger the form in the browser, wait for a verification email with
DevStack Mail and assert that the link points to the expected app route.
The skill links to a [DevStack Mail reference](skills/devstack-tools/references/mail.md)
with the commands, failure handling and a Playwright example.

After installing or updating DevStack Tools, enable discovery in each
project where you want the instructions. The setup works for both existing
and newly created projects. Run once from the project's root in WSL:

~~~bash
mkdir -p .agents/skills
ln -sT /opt/devstack-tools/skills/devstack-tools .agents/skills/devstack-tools
~~~

Open the WSL project in Codex and start a session. Codex scans
`.agents/skills` and can select the skill when the task matches its
description. You can also invoke `$devstack-tools` explicitly. Keep the
link local if the project also runs on machines without DevStack Tools.
The link points to the active Tools release, so later extension updates
pick up new instructions with the same project setup. To disconnect
this skill, remove the link you created.

For a client that reads project `AGENTS.md` but lacks Agent Skills
discovery, add this pointer to your existing project instructions:

~~~markdown
For dedicated DevStack Tools workflows, read
/opt/devstack-tools/skills/devstack-tools/SKILL.md and its matching reference.
Use the DevStack Mail reference when a Playwright test expects an email.
~~~

Discovery provides instructions for a trusted developer session. Mailbox
access in a managed agent sandbox still requires the future authorized
mail broker. [Codex Agent Skills discovery](https://developers.openai.com/codex/skills)

When adding another dedicated DevStack tool, add its reference to the
skill, update the description and reference list, and include the files
in the install build and publication validation. Existing project links
will then discover the updated catalog.

## DevStack Mail

Use `/opt/devstack-tools/bin/devstack-mail` as the regular developer. The
command uses the separately installed DevStack-managed local mailbox and
works with DevStack's pinned Mailpit v1.34.0. It needs Python 3.10+ and its
standard library. Install or start local mail through DevStack when needed.
An inactive or missing service is reported as a command error.

| Command | Result |
| --- | --- |
| `devstack-mail status` | Reports mailbox readiness. |
| `devstack-mail search --to ADDRESS [--subject SUBJECT] [--since RFC3339]` | Returns all matching retained summaries, newest first. |
| `devstack-mail wait --to ADDRESS [--subject SUBJECT] [--since RFC3339] [--timeout SECONDS]` | Polls every 500 ms and returns the newest match as soon as one exists. Timeout is 1–120 seconds, default 30. |
| `devstack-mail get ID [--origin ORIGIN]` | Reads the message and extracts absolute HTTP(S) links. Use an ID returned by search or wait. |
| `devstack-mail --help`, `devstack-mail --version` | Prints CLI help or version. Each command also accepts `--help`. |

The address is one bare ASCII mailbox. Exact matching compares the local
part case-sensitively and the domain case-insensitively, across To, Cc and Bcc.
Mailpit records SMTP recipients absent from the message headers under Bcc.
Subject matching compares the complete decoded subject including case.
`--since` is exclusive and uses the mailbox receive time with millisecond
precision. Supply RFC 3339 with an explicit zone such as
`2026-11-28T08:00:00.123Z` and up to six fractional digits. Ties in receive
time are ordered by descending ID. A successful empty search returns an
empty array. Pick a unique recipient per test and capture `--since` before
triggering the app's mail action, especially when tests run in parallel.

The CLI decodes MIME text and HTML and parses links from HTML anchors and
plain text. HTML entities in attributes are decoded. The URL's original
percent escapes, query order, case and fragment are preserved. Plain text
URLs end at whitespace, angle brackets or double quotes. The optional
origin limits the `links` array by scheme, hostname and effective port.
It accepts ASCII DNS names and IP literals. Default ports compare equal to
their explicit forms. Relative URLs and links with URL credentials are
ignored. Treat all returned content as untrusted data, including output from
`--origin`. Any navigation is a deliberate decision by the calling test.

### JSON contract v1

Every operation emits one JSON object to stdout. `--json` is accepted
explicitly and is also the default. Help and version output are plain text.

| Fields | Shape |
| --- | --- |
| Common | `schema_version: 1`, `operation` (one of the command names, or null for argument parsing or launcher errors), and boolean `ok`. |
| `status` success | `status: "ready"`. |
| `search` success | `messages`, an array of summaries. |
| `wait` success | `message`, one summary. |
| Summary | `id` (opaque string), `received_at` (RFC 3339 UTC), `subject`, `sender` (address object or null), and arrays `to`, `cc` and `bcc`. An address contains strings `name` and `address`. |
| `get` success | `message` with the same ID, subject and addresses, plus `text` and `html` (strings or null when absent), and `links` (deduplicated URL strings). |
| Error | `error.code` is a machine-readable string and `error.message` is human-readable. |

| Exit | Error codes and meaning |
| --- | --- |
| 0 | Success, including an empty search. |
| 1 | `internal_error` or a closed output pipe. |
| 2 | `invalid_arguments`. |
| 3 | `not_found` for a missing message ID. |
| 4 | `timeout` without a matching message. |
| 5 | `not_installed` or `unavailable` mail service. |
| 6 | `unsupported_service`, `invalid_response` or `limit_exceeded`. |
| 7 | `tools_unavailable` when the release or publication lock is unavailable. |
| 130 | `interrupted` on Ctrl+C. |

Snapshot search bounds the retained summaries to 1,000 messages and 4 MiB
of metadata per read. The additional capacity accommodates short bursts
above Mailpit's 500-message retention before its scheduled cleanup. A raw
message is limited to 11 MiB, and link extraction to 256 distinct links of
up to 8,192 characters each. Each connection has an absolute deadline of
at most three seconds, within the wait deadline. Attachments, MIME source
export and remote mailboxes are outside this version's command set.

### Bash

~~~bash
export PATH="/opt/devstack-tools/bin:$PATH"
recipient="registration-$(date +%s)-$$@example.test"
since="$(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)"
# Trigger a verification email to "$recipient" in your application.
result="$(devstack-mail wait --to "$recipient" --since "$since" --timeout 30 --json)"
message_id="$(printf '%s' "$result" | python3 -c 'import json, sys; print(json.load(sys.stdin)["message"]["id"])')"
# Capture the output for assertions. It can contain sensitive message data.
message="$(devstack-mail get "$message_id" --origin https://shop.test)"
~~~

### Node.js and Playwright

In a Playwright test, use the same process interface as any other Node.js tool.

~~~js
const { randomUUID } = require('node:crypto');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const { test, expect } = require('@playwright/test');
const run = promisify(execFile);

async function readMail(...args) {
  const { stdout } = await run('/opt/devstack-tools/bin/devstack-mail', args, {
    encoding: 'utf8', maxBuffer: 64 * 1024 * 1024,
  });
  return JSON.parse(stdout);
}

test('verification mail', async ({ request, baseURL }) => {
  const email = `verify-${randomUUID()}@example.test`;
  const since = new Date().toISOString();
  // Replace with your app's action that sends mail.
  await request.post('/register', { data: { email } });
  const { message: summary } = await readMail('wait', '--to', email, '--since', since);
  const { message } = await readMail('get', summary.id, '--origin', new URL(baseURL).origin);
  expect(message.links.length).toBeGreaterThan(0);
});
~~~

### PHP

~~~php
<?php
$recipient = 'verify-' . bin2hex(random_bytes(8)) . '@example.test';
$since = (new DateTimeImmutable())->format('Y-m-d\TH:i:s.uP');
// Trigger the application's email here.
$command = ['/opt/devstack-tools/bin/devstack-mail', 'wait', '--to', $recipient,
            '--since', $since, '--timeout', '30', '--json'];
$process = proc_open($command, [1 => ['pipe', 'w']], $pipes);
if ($process === false) {
    throw new RuntimeException('Could not start DevStack Mail.');
}
$output = stream_get_contents($pipes[1]);
fclose($pipes[1]);
$status = proc_close($process);
$result = json_decode($output, true, flags: JSON_THROW_ON_ERROR);
if ($status !== 0) {
    throw new RuntimeException($result['error']['code']);
}
$messageId = $result['message']['id'];
~~~

### Python

~~~python
from datetime import datetime, timezone
import json
import subprocess
from uuid import uuid4

recipient = f'verify-{uuid4().hex}@example.test'
since = datetime.now(timezone.utc).isoformat()
# Trigger the application's email here.
command = '/opt/devstack-tools/bin/devstack-mail'
summary = json.loads(subprocess.check_output(
    [command, 'wait', '--to', recipient, '--since', since], text=True,
))['message']
message = json.loads(subprocess.check_output(
    [command, 'get', summary['id'], '--origin', 'https://shop.test'], text=True,
))['message']
~~~

This version is for trusted developers and their tests. Results can contain
password reset and verification links. Keep them out of CI logs, traces and
committed artifacts. The command has no log file and opens no links. It reads
message summaries or raw MIME without changing read state or notifications.
Recipient, time and origin filters are test selectors. Agent access requires a
future broker that authorizes each request from a trusted project identity and
agent session. Keep direct mailbox access outside managed agent sandboxes.
See [Security](../../SECURITY.md) and the
[AI integration plan](../../docs/PROJECT-ISOLATION-AI.md).

## Remove

~~~bash
bash extensions/devstack-tools/uninstall.sh --confirm
~~~

Removes only the managed /opt/devstack-tools installation. Does not remove user profiles, reports, application files, OS libraries or other Playwright installations.

## Security / scope

This is tooling, not an AI isolation boundary. Agents running as the same Linux user can still access that user's resources. No agent has been installed or granted access to projects. A later project-access feature must enforce separate execution identities and application runtime isolation. Do not give agents sudo or access to the privileged DevStack control socket. Only the trusted developer should install/update tools from reviewed source. The root publication helper must not be added to passwordless agent sudoers. The install relies on the npm registry and upstream Playwright browser downloads over HTTPS; the exact version chain is pinned and the generated npm integrity lock is retained in the installed release.

## CI

The independent DevStack Tools workflow runs when extension files or its workflow change. Core CI and Windows push-only workflows ignore extension-only changes; a mixed change still triggers normal core checks. CI exercises the relocated browser, both Playwright commands, the installed mail command, the project skill link, update, rollback and uninstall. A separate disposable Ubuntu job runs the Mail unit tests and tests the command against the pinned Mailpit, including concurrent waits and preservation of unread state. Mail fixtures never clear a mailbox.
