# Project isolation and AI integration (design, not implemented)

This document defines the security requirements for future project isolation
and AI configuration management. It does not grant access or describe an
available sandbox. Existing installations remain trusted local development
environments until the new boundary is implemented and verified.

## Existing boundary

- The panel runs as `devstackweb`; the root-owned local daemon accepts
  typed operations and verifies the Unix socket peer UID. Never give an AI
  process that identity, administrative credentials, or direct daemon access.
- Managed projects are separate directories with separate PHP-FPM sockets,
  but their pools currently use the same configured developer identity.
  Nginx receives public-directory ACLs. Separate sockets are not separate
  file permissions; code from one project can exercise the shared identity.
- Framework installer workers already use systemd restrictions, but their
  writable scope includes the developer home. They are trusted developer
  tooling, not an untrusted agent sandbox.
- DevStack Tools is optional shared tooling, not an isolation boundary.
  The project registry is currently JSON; an application's use of SQLite
  does not imply that the manager's registry has been migrated.

## Target security contract

First audit and isolate the project runtime, then integrate one local agent.
A model providing inference, an agent making decisions and the tools executing
actions are distinct components. Native Windows model servers and agents are
supported design targets; do not require them to run in WSL or introduce
compute quotas just to isolate project access. Linux-specific work may be
delegated to an isolated execution identity or narrowly scoped broker. Its
mechanism must not be bypassable by the native agent or its child processes.

- Default deny. Assign an installed agent and/or permitted model to an
  explicitly registered project. A model association selects an inference
  provider; it grants no filesystem or command access by itself.
- Project grants distinguish read, write, execution and optional network or
  browser access. Multiple agents per project and distinct grants per
  agent/project are possible. Separate project runtime identities or
  equivalently enforced boundaries must prevent agent-modified application
  code from gaining access outside its project.
- A grant is enforced at execution, not just displayed in the panel. Keep
  admin/update sources, host secrets, other projects, privileged sockets,
  administrative panel operations, Windows private files and unrestricted
  interop out of the granted scope. Consider indirect execution (PHP,
  Composer/npm hooks, child processes, browser sessions and local services).
  Do not disable existing host integration globally to isolate an agent.
- Revocation blocks new managed actions and stops affected managed sessions,
  or clearly reports a restart or unsupported enforcement requirement.
  It cannot erase data already copied. A manual external app session is not
  automatically controlled by the panel.
- Prefer lightweight native enforcement when sufficient; evaluate stronger
  isolation based on concrete threat and test results. Bubblewrap is the selected candidate for isolated Linux tool execution;
  its production policy and the project runtime identity are not implemented.

## Runtime capability checks

Do not infer namespace support from installed executables or assume that a
Linux security module is enforcing policy. Before choosing Bubblewrap or
systemd isolation, test the required namespace and mount operations on the
target WSL kernel with an ordinary unprivileged user and disposable fixtures.
A minimal Bubblewrap probe should expose only one writable fixture and the
necessary read-only runtime, use separate user, PID and network namespaces,
clear inherited environment variables, and verify that an unmounted sibling,
an absolute symlink to it, host home, Windows mounts and control sockets
cannot be reached. A failure to create a required namespace is a failed
probe, not permission to retry without it or relax host-wide security.
The probe does not establish a production sandbox: inherited descriptors,
IPC, subprocess lifecycle, kernel attack surface, WSL interoperability
and selectively brokered networking remain separate acceptance concerns.

The repository contains a standalone capability check:

~~~bash
bash tests/project-sandbox-probe.sh
~~~

Run it as an ordinary user, never as root. It does not accept a project
path or arbitrary commands. It creates and removes two temporary fixtures,
tests both read/write and read-only mounts, verifies namespace separation,
and fails if the required protection is unavailable. The dedicated GitHub
Actions workflow installs the distribution package on a disposable runner,
without disabling AppArmor or changing global namespace policy. CI and
workstation results are distinct. The expanded automated probe is not a
production launcher and is not installed or exposed through the manager.

Do not reuse the interactive developer identity as the project PHP runtime
or as an unrestricted tool executor. Evaluate distinct non-administrative
project identities and compatible filesystem access for the trusted
developer, Nginx, Laravel storage, caches, SQLite and build/test tools.
Separate identities alone do not restrict local services, interop or
untrusted application subprocesses. Plan migration and rollback before
changing ownership, ACLs or pools; verify both HTTP and CLI execution paths.
If only one managed project exists, use a second disposable fixture for
cross-project negative tests rather than a real repository.

Windows applications can reach the Linux filesystem via WSL UNC paths
with the distribution's default-user permissions. See
[Microsoft WSL file permissions](https://learn.microsoft.com/en-us/windows/wsl/file-permissions).
A Linux execution sandbox does not restrict a native Windows agent's
direct filesystem, process or network access. Evaluate and verify native
tool-specific or Windows-enforced restrictions independently; if they
cannot be enforced, do not claim that assigning a project is a complete
sandbox. Preserve the existing Windows/WSL integration for the trusted
developer.

## Isolated PHP runtime prototype (E1.3, not activated)

The first runtime implementation is `daemon/project_runtime.py`, tested by
`tests/project-runtime-systemd-smoke.py` in a dedicated CI workflow. It is
not called by the installed daemon and is not an installation or migration
command. The prototype only accepts protected fixture paths under /var/lib;
the fixture test runs on a disposable hosted runner, never a workstation.

Use an independent non-root FPM master/worker identity per project and
a separate Bubblewrap mount, user, PID, IPC and network namespace.
Preserve the project absolute path inside the mount namespace; mount the
source read-only, with explicit writable mounts for storage,
bootstrap/cache and database when present. Only the installed PHP
configuration needed for the interpreter is mounted, not the shared
FPM pool directory. Each instance has its own FastCGI socket directory;
Nginx's identity receives an explicit socket ACL, not membership in a
project runtime group. Writable directories use a dedicated
project group with setgid/default group permissions; only the trusted
developer is a member of multiple project groups. Verify runtime-created
files are editable from a fresh developer session. Group changes may
require re-login; no such change is applied to an installed workstation.
The fixture uses a newly created unprivileged developer account with actual
NSS memberships verified in its subprocess, not the hosted runner's login.
A preflight exercises shared group writes before either FPM instance starts. No project receives the manager identity.
Test both separate account DAC and the mount/network boundary using
two real FPM processes, FastCGI requests, SQLite writes, developer access
and independent stop. The initial prototype denies all host networking,
so it does not yet support MariaDB, Redis, Mailpit or external HTTP.

Before production integration, design race-safe source opening and mount
publication (the prototype path checks are not a security boundary);
protect against user-controlled directory replacement, symlink/hardlink
escapes, inherited FDs and WSL-specific IPC/interop. Review the shared
Nginx worker's static-file and symlink handling separately: private FPM
mounts do not isolate Nginx. Preserve Laravel build/cache/storage and
SQLite access for the developer and test real HTTP, CLI and framework
installation. Add explicit migrations, rollback, cleanup, service
registry and selective networking where required. No silent fallback
to the shared developer pool and no automatic migration of existing
projects. Native Windows agent policy remains a separate gate.

## HTTP ingress prototype (E1.3b, not activated)

The shared Nginx must not resolve agent-controlled project paths. The
experimental ingress in `daemon/project_http.py` uses fixed per-host
Unix-socket HTTP upstreams instead of shared static roots. Each project
has an unprivileged Nginx instance in a separate Bubblewrap namespace;
it mounts only public assets and the conventional published storage
directory read-only, plus the project's private FastCGI socket and HTTP
socket directory. It does not mount the other projects or the private
application root. The conventional Laravel public/storage symlink is
not followed; a fixed alias publishes storage/app/public if present.
Other symlinks are refused, hidden paths and non-front-controller PHP
files are denied. /build/ serves built assets; missing assets do not
fall through to PHP. Dynamic routes, method, query and body are passed
to the fixed index.php. The ingress terminates HTTPS, replaces forwarding
headers, ignores upstream X-Accel-Redirect, rejects unknown hosts and
fails closed if a project's HTTP instance is unavailable.

The real CI fixture exercises validated HTTPS (a disposable trusted
certificate), static JS MIME, storage assets, dynamic POST routing,
forwarded header replacement, sibling/private/host symlink denials and
independent HTTP shutdown. The same test preserves the earlier FPM,
SQLite and developer-write assertions. No deployment configuration,
registry or production Nginx file is changed; the prototype runs only on
a disposable hosted runner. Live Vite HMR, actual Laravel/Livewire
installation, TLS deployment, production socket publication, safe mount
source lifecycle, hardlink handling and management APIs are still
separate acceptance gates. Per-project HTTP worker overhead and lazy
activation must be considered before production integration.

## Isolated runtime lifecycle (E1.3c, fixture-only)

`daemon/project_lifecycle.py` coordinates a fixed PHP-FPM/HTTP pair:
start PHP then HTTP, check both transient unit identities and actual
Unix listener connectivity, stop HTTP before PHP, serialize per-project
operations with a root-owned lock, and roll back a partial startup.
Repeated start/stop is idempotent; restart does not touch source or
database files. Observed status is running/stopped/degraded; inability
to verify ownership or state is an error, not a stopped result. Listener
readiness is not a proof of application health. A mismatched unit
description, account or transient identity is refused rather than
stopped. An unprivileged fixed-path check, not a root unlink or generic
command endpoint, handles stale fixture sockets. There is no fallback to
the shared PHP pool.

The real test now uses the lifecycle after the independent FPM assertions,
keeps both SQLite databases, and induces a real invalid HTTP config after
PHP startup. It checks rollback, sibling HTTPS availability, idempotent
start/stop, foreign-unit refusal, restart and database continuity.
The controller requires the protected /var/lib disposable fixture layout
and is not registered with devstackd, installed as a service, or exposed
to the panel. This deliberately does not solve persistent runtime ownership,
crash recovery of a deployment transaction, safe mounting of a developer
tree, production socket publication, account/ACL provisioning, selective
networking, project authorization or Windows isolation. Those are gates
before production integration, not features inferred from fixture tests.

## Registered-project readiness and production gate (E1.3d)

The installed administrator diagnostic is read-only:

~~~bash
sudo /usr/bin/python3 -I /opt/devstack/daemon/project_isolation_preflight.py --project <registered-slug>
~~~

It reads the fixed root-owned server configuration and project registry,
validates the canonical registration, then runs a bounded metadata worker
as the configured developer. It inspects only the expected mount-source
directories and front controller with descriptor-relative, no-follow
opens and mount identity checks. It reports unsafe/missing paths,
registered MariaDB/mail and custom Nginx settings, and Bubblewrap package
metadata. It does not read application content, .env, credentials or
databases. No recursive permission changes, sandbox launch, project service
startup, registry update or Windows action occurs. A successful diagnostic
may report blocked; it is not permission to migrate. Unknown Redis or
other dependencies are not inferred from application configuration.

Production activation remains blocked pending a reviewed patched backend
and the actual integration. As reviewed on 2026-11-27, Ubuntu's
[CVE-2026-87766](https://ubuntu.com/security/CVE-2026-87766) record lists
Ubuntu 26.04 LTS as vulnerable; [USN-8779-2](https://ubuntu.com/security/notices/USN-8779-2)
describes the reversion of an earlier incomplete symlink fix. The issue
concerns setup before sandbox execution; a positive functional fixture
test does not establish that launching against attacker-controlled trees
is safe. Check the exact distribution package, signed origin, vendor
advisory and relevant patches rather than interpreting the upstream
version alone. No setuid fallback, arbitrary PPA, downgrade or runtime
override is offered. Reevaluate the approved backend (patched Bubblewrap
or a tested equivalent) before enabling real agents.

Metadata inspection is deliberately not mount authorization. A later
launcher must retain verified source descriptors or use a protected
mount lifecycle, enforce project identity at launch, handle directory
replacement and hardlinks throughout the tree, and protect all endpoints
and rollback. The diagnostic remains blocked for every project until
those production gates are implemented and reviewed; a future version
number alone cannot enable it.

## Configuration management and adapters

DevStack is a management plane, not a mandatory AI chat client or runtime.
A future AI section and per-project settings will expose the same project
assignments. Use per-tool adapters to discover installed versions, supported
settings, configuration locations and availability, and to read and update
supported settings using native APIs, CLI or documented files. Surface
sandbox modes, approval policies and allowed working directories according
to each tool's actual capabilities. Prefer automatically approving permitted
operations inside the enforced boundary; do not interpret approval policy as
a substitute for isolation or silently grant unrestricted access.

Keep external application settings authoritative where appropriate. Validate
changes, preserve unknown configuration keys and format, detect concurrent
or external edits, back up before writing, and expose restart requirements
and rollback. Do not copy secrets into manager state, logs or backups
unnecessarily. No arbitrary path editor, shell command or privileged write
endpoint. Windows user-owned settings must be edited with a narrowly scoped,
unprivileged mechanism, never through an elevated general-purpose helper.

The planned manager state includes stable project/agent/model identifiers,
assignments, policies and bounded audit history. SQLite may be evaluated for
that state with an explicit schema and migration; there is no migration or
new database in this design change. Distinguish requested, applied and
verified policy, and display unsupported controls or external drift rather
than claiming protection that cannot be enforced.

## Local model detection

Start with an explicitly configured or safely discovered local installation,
not an arbitrary network scan. Inspect known endpoints with bounded timeouts
and allowlisted requests, distinguish offline/unreachable, loading, ready
and unknown states, and retain last successful observation. Do not equate
an HTTP failure with a stopped process. No passive detection may start WSL
or the model, expose a server on the LAN, change GPU settings or weaken the
firewall. Native adapters may use documented local status mechanisms.
Any model connection available to an untrusted tool must be narrowly scoped.

## Tool compatibility contract (required before production activation)

Do not apply the PHP-FPM fixture policy unchanged to developer tools.
Application execution, agent file/CLI operations, browser automation and
model inference have different needs. Enforce independent, project-scoped
capabilities for each, using a common project identity and revocable
assignment. The backend must remain replaceable; security and compatibility
tests, not the choice of Bubblewrap, define acceptance.

| Profile | Required workflow | Boundary |
| --- | --- | --- |
| Application | PHP-FPM, Laravel/Livewire, database and storage | Read-only sources where compatible, explicitly writable runtime data; only authorized services |
| Developer tools | Git, PHP CLI/Artisan, Composer, npm, Vite builds and tests | Assigned source read/write, per-project caches, temporary directories and approved package access; no root or unrestricted WSL interop |
| Browser | Playwright Test and agent CLI, Chromium, screenshots, traces, downloads, Vite HMR | Isolated browser identity/session and allowed project origins; no personal profiles, sibling/admin endpoints or unrestricted debugging sockets |
| Native agent | Official ChatGPT desktop / Codex or supported local agent | Native permissions and approvals, controlled project access and brokered Linux tools; verify Windows/UNC/WSL and child-process denials |
| Model provider | Native Windows llama.cpp or another supported inference provider | Scoped inference connection, independent lifecycle, no project file access granted by model selection |

### Official ChatGPT and Codex

Preserve the official applications and account sign-in. Use documented
native settings, CLI and supported tool interfaces rather than wrapping
the whole app in a Linux sandbox or reverse-engineering credentials.
The Windows Codex sandbox supports native execution, workspace permissions
and approval policies. The config adapter must discover installed capabilities,
respect configuration precedence and distinguish legacy sandbox_mode /
sandbox_workspace_write from newer permission profiles; do not combine
incompatible settings. Auto-approval applies only to allowed operations;
out-of-scope actions remain denied or require explicit user approval.
Do not interpret native workspace-write as a verified deny-read policy.

A narrow project-bound tool broker can be exposed through supported MCP
interfaces (e.g. local stdio or authenticated Streamable HTTP where supported).
Authorize every operation independently at the broker; MCP tool labels,
model instructions or configuration text cannot grant access. Do not expose
the privileged daemon socket, arbitrary administrative operations or a general
WSL shell. Protect credentials and configuration from agent writes, and
test project switching, simultaneous sessions, revocation and native
bypass routes including wsl.exe and WSL UNC paths. Never assume a native
agent cannot bypass the broker simply because a Linux worker is isolated.

ChatGPT Chat, local Work, desktop Codex, built-in browser and cloud
execution are distinct surfaces with different capabilities. The desktop
app's own browser and local-folder permission prompts remain independent
of a Playwright integration. Verify each supported surface on the installed
version; expose unsupported controls explicitly. Cloud sessions do not
automatically have local filesystem or local .test connectivity, and
local processing does not imply that context never leaves the device.
No public exposure of local apps or inference endpoints, unofficial
session-token extraction, or separate API subscription requirement is
introduced by this architecture. Any cloud/API integration is separately
opt-in, with its own supported authentication and cost/data policy.

References: [Windows sandbox](https://learn.chatgpt.com/docs/windows/windows-sandbox),
[configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference),
[approvals and security](https://learn.chatgpt.com/docs/agent-approvals-security),
[MCP](https://learn.chatgpt.com/docs/extend/mcp),
[ChatGPT Work and Codex](https://help.openai.com/en/articles/20001275-chatgpt-work-and-codex),
[built-in browser](https://help.openai.com/en/articles/20001277-using-the-built-in-browser-in-the-chatgpt-desktop-app).

### Playwright and local development network

Reuse the optional DevStack Tools installation when available; mount
the validated active release and Chromium binaries read-only. Preserve
the pinned runner/browser pair, central Test/CommonJS support and CLI.
Allow a dedicated writable home, temporary files, runtime IPC and
project-scoped reports/screenshots/traces, with per-project and per-session
browser state and cleanup. No copy of the user's normal browser profile,
unrestricted CDP endpoint or CA private key. Use the existing public CA
in a dedicated browser trust store and retain HTTPS verification.
See [DevStack Tools](../extensions/devstack-tools/README.md) and
[Playwright BrowserType](https://playwright.dev/docs/api/class-browsertype).

A browser's own sandbox can need user namespaces/process facilities that
the existing PHP fixture deliberately blocks (including --disable-userns).
Test an appropriate separate browser profile or independently isolated
browser worker with a reviewed security boundary. Do not blindly add
--no-sandbox, relax the host globally or inherit the PHP policy.
Browser contexts and request interception are not sufficient network or OS
isolation for untrusted scripts. Test actual Chromium launch, child process
cleanup, filesystem and network denials.

The current network-denied PHP fixture is not the final developer network.
Provide narrowly scoped project HTTP(S) routing, WebSocket upgrade/HMR,
localhost and DNS resolution for the assigned project without access to
the admin panel, neighboring projects, host services, metadata endpoints
or unintended external destinations. Enforce the destination boundary
outside untrusted browser/test code, including redirects, alternate host
names, raw connections and applicable WebSockets. Any browser server
transport must be authenticated, local and tied to the grant/session.
If using Playwright's browser-server protocol, test compatible client/server
versions; do not substitute a user's existing browser debugging session.
Provide project-specific MariaDB/Redis/mail resources only when enabled;
do not start optional services merely to observe them. Dependency
downloads, git remotes and wider internet access are separate explicit
capabilities. Support actual development and E2E tests without disabling
TLS validation, changing global hosts or granting blanket network access.

### Native llama.cpp

Keep model files, native server, accelerator configuration, launch method
and resource availability in Windows; no required move to WSL, CPU/GPU
quota or new VM. Discover the configured local endpoint/version and
available model identifiers through documented APIs and distinguish
unreachable, loading and ready using bounded health probes. Where supported,
test streaming inference, cancellation and tool-call handling against the
installed model; validate all tool arguments and enforce grants at the
executor. A model's tool-call output does not authorize an action.
Use the configured loopback endpoint or an authenticated narrowly scoped
transport where cross-boundary access is necessary; never automatically
listen on all interfaces or open the firewall. Keep the existing launcher
and offline state independent of DevStack startup. Model/API credentials
do not flow into project PHP, Playwright or tool subprocesses by default.
See [llama.cpp server documentation](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md).

### Compatibility acceptance matrix

These are required positive AND negative tests, not currently implemented
features. Before migrating any real project, run a representative
two-project workflow on the selected production backend: actual Laravel
with Livewire/Flux and SQLite, PHP CLI/Artisan, Composer/npm, Vite build
and live HMR, central Playwright Test and CLI against validated HTTPS,
session isolation, screenshots/traces and revocation. Test allowed
project-specific optional services and denial of the sibling project,
admin endpoints, host credentials and uncontrolled egress. Reuse the
existing D2/D2a application regression where possible, but run it within
the new execution boundary; the old trusted-runtime PASS is not sufficient.

Before enabling local AI, test actual native llama.cpp inference and
one assigned agent doing a complete edit/build/test/browser cycle,
including loading/unavailable state, model switch and cancellation.
Before enabling an official app adapter, test that installed app's
documented project configuration, permissions/approval behavior, tool
connection and cross-project denial, including Windows-native paths
and direct WSL access. Require negative results for unsupported/bypassing
operations; do not grant full access to obtain a positive demo. Record
versioned evidence from CI where possible and the actual Windows/WSL
workstation where native features cannot be reproduced on CI.

## E1 acceptance before broader AI integration

1. Inspect deployed project owners, ACLs, PHP-FPM identities, helper access,
   app-to-app and Windows interop boundaries. Record metadata, not secrets.
2. Select a minimal boundary and migration/rollback strategy after the audit;
   keep existing trusted developer workflows and optional tools usable.
3. Exercise an assigned fixture project's intended read/write/test workflow.
   Test denial against a second fixture, host paths and management interfaces,
   including code running through PHP-FPM and child processes, symlink escapes
   and applicable network/browser paths. Verify revocation and no unintended
   WSL startup. Use disposable fixtures, never real private files as targets.
4. Only claim isolation after representative positive and negative tests in
   CI and the target environment. Document unsupported adapter capabilities
   and distinguish implementation evidence from workstation acceptance.

Then integrate one local agent end to end; add further native application
adapters and multi-agent delegation only after that boundary works.
