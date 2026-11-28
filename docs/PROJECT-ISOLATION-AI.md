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

The installed diagnostic requires root-owned, non-group/world-writable
ancestors of its control files and installed code. GitHub-hosted CI may
provide a world-writable /opt; the disposable installation test prepares
that ancestor explicitly. No installer or preflight changes permissions
of a workstation's /opt automatically. An unsafe ancestor must be reviewed
by the administrator before relying on the installed code.

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

### Reviewed backend selection

The Ubuntu LTS 0.11.1-1ubuntu0.3 package was the USN-8779-2
regression-revert release, not a fix for CVE-2026-87766 at review time.
Prefer a fixed official distribution package. Where unavailable, the
[APT-managed 0.13.1 backport experiment](BUBBLEWRAP-BACKPORT-EXPERIMENT.md)
rebuilds authenticated source on the target LTS, verifies the upstream
archive digest, tests the package, namespace/runtime and exact rollback.
A separate versioned backend is an alternative, not a second mandatory
installation. Never install an unreviewed archive, disable archive trust
or weaken global security settings. A local backport must be reconciled
with future official security releases; its higher version can otherwise
mask an official update. Neither a version string nor successful
functional fixture tests constitute production approval. Retain the
production gate until safe mount lifecycle and tool acceptance are
complete. Do not reuse a vulnerable backend as a silent fallback.

References: [Ubuntu regression notice](https://ubuntu.com/security/notices/USN-8779-2),
[upstream advisory](https://github.com/containers/bubblewrap/security/advisories/GHSA-pxhw-h44j-8pfx),
[upstream releases](https://github.com/containers/bubblewrap/releases).

### Official ChatGPT and Codex

Preserve the official applications and account sign-in. For projects in
WSL2, the preferred official desktop Codex mode is the WSL agent
environment (distinct from the integrated terminal setting). The GUI
remains on Windows; the Codex agent and commands use Linux. A locally
hosted model and its separate native Windows agent remain independent.
Codex selects the first bwrap on its own PATH, according to its
[Linux sandbox documentation](https://learn.chatgpt.com/docs/sandboxing).
If a reviewed separate backend is used, verify that the supported
app/CLI launch configuration selects it before sandbox startup, not
just that an interactive terminal resolves it. Avoid nesting the
DevStack PHP sandbox around Codex; test Codex's own permissions and
seccomp and protect the application runtime separately. Do not rewrite
the user's global PATH or assume the CLI and desktop app share the same
config/auth directory. See [WSL](https://learn.chatgpt.com/docs/windows/wsl)
and the [Windows app settings](https://learn.chatgpt.com/docs/windows/windows-app). Use documented
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

## Descriptor-pinned project runtime sources (E1.3e)

The runtime pair now selects project directory sources as its own non-root
identity inside a fixed launcher, using no-follow directory descriptors and
Bubblewrap's `--bind-fd` / `--ro-bind-fd` (available since 0.10.0).
The project, its approved writable subdirectories, public and published
storage are not reopened by pathname for the bind operation. Directory
selection rejects symlinks, invalid components and unexpected mount IDs;
existing nested mounts are rejected by a mountinfo snapshot. A real fixture
renames the selected directory and replaces the old pathname with a symlink
before launching Bubblewrap. The write must reach the original directory,
not the replacement; source descriptors must not reach the application.
The existing FPM/HTTPS/lifecycle smoke exercises the fixed launcher on both
isolated accounts, and the backport workflow repeats this with 0.13.1.

This increment still accepts only disposable fixture paths and accounts.
It is not a production grant, persistent mount publication or a general
command executor. The source selector alone does not solve pre-existing
hardlinks, concurrent privileged mount/rename operations, parent/child
source coherence during enrollment, root-controlled socket publication,
or migration of an existing developer-owned project. Production activation
remains gated. Do not mount arbitrary directories or pass arbitrary commands
from an API. The preferred next step is a root-protected enrolled-project
layout with durable identity and stable publication, with the trusted
administrator coordinating source changes and the actual application/browser
compatibility tests before enabling a real project.

Reference: [Bubblewrap release history](https://github.com/containers/bubblewrap/releases/tag/v0.10.0).


## Registration identity and existing project lifecycle (E1.3f)

DevStack's existing project creation workflow remains the only supported
entry point for normal project registration. Both a manually prepared
project and the asynchronous official framework installer converge on
`create_environment`. New successful registrations receive a
server-generated random 128-bit `project_id` in the same protected JSON
registry transaction, never supplied by a client or derived from a slug.
It is an internal registration identifier, not a UID, credential or grant.
Updates and restarts retain it. Deleting and re-registering a slug must
create a fresh identity even when the previous source directory is retained.
Existing entries without it continue working unchanged; no mass rewrite
or automatic enrollment occurs. Preflight distinguishes registered,
legacy and invalid/duplicate identity metadata but still blocks activation.
Future runtime identity and AI assignments must bind the registration
identity, not reuse an old grant based only on the slug. Existing legacy
registrations require an explicit enrollment transaction.

### Preserve DevStack features as isolation is integrated

Isolation must be part of the existing project lifecycle, not require
manual project creation, manual PHP pools, a second panel, or a separate
framework installer. This is a compatibility requirement, not a claim
that the production sandbox is already connected.

| Existing mechanism | Required isolated-project behavior and acceptance |
| --- | --- |
| Create project, empty or existing directory, official Laravel installer and starter-kit options | Same form/API, supported choices, asynchronous job status/toasts, safe provisioning order; installer/build/migrations execute with scoped permissions and approved dependency access; failure preserves user files and cleans only reserved hosting resources |
| Project list, framework metadata, domain/hosts synchronization, certificates and HTTPS | Keep project registration, detected metadata, stable local domain and trusted TLS; shared ingress cannot resolve untrusted project paths; no additional global Windows changes |
| PHP, webroot, static assets, Livewire/Flux routes, Nginx managed rules and editor/rollback | Render validated configuration to appropriate isolated component; preserve supported custom webroots, tuning and asset rules with equivalent semantics; reject incompatible changes explicitly rather than silently reverting to shared FPM |
| SQLite, optional MariaDB, Redis, Mailpit, credentials and database test/backup/restore | Preserve data, ownership and existing database workflows; grant only project-scoped resources; absent/stopped optional services remain absent/stopped unless explicitly started; test actual service-specific access and denial |
| Artisan, Composer, npm, Vite builds/HMR, DevStack Tools and Playwright | Keep working developer workflow and central tools, dedicated project caches/browser sessions, approved dependency/network routes and HTTPS verification |
| Notifications, PWA, widgets, service controls and project counts | Preserve existing UI events and operation outcomes, history/read state, observed service state, Windows integration and no unintended WSL/model startup; no access to management endpoints from project code |
| DevStack update, restart, backup and rollback, delete/re-register | Preserve registry, credentials, project sources, DB, certificates and intended enabled state; use transactional cutover, verified service ownership and recovery; revoke project grants and stop isolated processes before removing enrollment; no silent legacy fallback |

Existing regression suites remain in place. The installed-stack test
extends the current create/upgrade/rollback, optional-service, framework
installer, mail, journal and widget scenarios to assert durable identity,
including a real delete/re-register of an existing SQLite application's
retained files. This is registration compatibility evidence only, not yet
the isolated equivalent of every mechanism. Before activating an isolated
real project, exercise these same supported workflows under the new
boundary and add representative negative tests; do not skip a supported
option merely to obtain a successful isolated demo. Native adapters such
as Codex can be installed and verified in their own integration stage,
not as a prerequisite to developing project isolation. Until their
individual acceptance passes, they must not be granted isolated-project
access based solely on a successful PHP sandbox test.


## Runtime account binding and concurrent agents (E1.3g)

The selected architecture separates three identities:

- The application runtime has one dedicated, non-login OS principal per
  project registration. PHP/HTTP do not run as the developer or an AI agent.
- An agent has a logical identity and independently revocable assignments.
  Operations execute in a project- and session-scoped worker or a verified
  native sandbox. Do not equate the agent's identity with a shared Linux
  account that can access all of its assigned projects at once.
- A model is an inference provider. Model changes do not change project
  grants; no per-model Linux user or direct filesystem access is required.

Multiple agents (including orchestrator/delegate sessions) may be assigned
to one project concurrently while the developer works normally in the IDE.
An assignment is many-to-many, not an exclusive project owner or a global
active-agent switch. Use separate session state, cancellation and scoped
tool/browser credentials. Check grants on every tool operation and terminate
or drain affected workers on revocation; other assignments and the application
must remain available. An orchestrator cannot delegate more permissions
than it has. Distinguish application writable data from code-edit privileges.
Use optional Git worktrees or explicit coordination for concurrent edits;
a shared checkout does not prevent write conflicts or guarantee transactional
edits. Separate worktrees require their own authorized path and Git metadata
handling; do not silently expose the developer's entire home or all worktrees.
Preserve the normal trusted developer workflow, paths, installer and IDE.
Do not require the developer to switch Linux users or run the native model
in WSL. These are acceptance requirements, not implemented agent adapters.

The current increment binds the two existing disposable runtime fixture
accounts to independent 128-bit registration IDs. A protected allocation
journal reserves the slot before account creation, records the full ID and
observed UID/GID, and remains blocked if incomplete. The fixed account
name is derived from the ID, not the slug or model. Existing names/groups
are never adopted. Check the actual passwd/group identity, locked password
as administrator, non-login shell, home, comment and absence of unrelated
supplementary groups; verify the binding on each lifecycle operation and
again under the actual runtime credentials before mounting. Tests exercise
these principals through the existing real PHP/HTTPS/SQLite lifecycle, and
reject altered numeric identity and wrong project association.

This is still disposable CI allocation only, not a production account
management endpoint or automatic migration. Root-controlled publication,
safe project ACLs/hardlinks, legacy enrollment, durable crash recovery,
UID/GID retirement/non-reuse, transactional activation and real tool/agent
acceptance remain required. Do not delete and reuse a production UID/GID
while files, ACLs, grants or processes might retain it. Do not automatically
create accounts for legacy projects or enable real isolation on upgrade.


## Owner-applied access preparation and normal developer credentials (E1.3h)

Avoid adding the trusted developer to a growing list of runtime groups:
existing IDE/terminal sessions should not need a new login every time a
project is created. The selected candidate uses named POSIX ACLs with
default inheritance, dedicated runtime accounts and read-only source
mounts. Source directories/files receive runtime read/traverse permission;
approved application data directories receive runtime write permission and
a default named developer entry for new runtime-owned files. The developer
retains ordinary ownership and edits files directly, without running the
IDE as the runtime user. Agent edit permissions remain a separate scoped
execution profile, not PHP-FPM's data-write permissions.

In the disposable fixture, preparation runs as the non-root owner over
descriptor-relative metadata with a bounded read-only scan before changing
permissions. Existing ACLs, non-private modes, foreign owners/groups,
hardlinks, mount crossings, special nodes and excessive trees are refused.
Symlinks are retained but never followed for ACL operations. Explicit ACL
plans are applied through pinned descriptors using the system libacl;
permissions on a replaced pathname's target are not modified. Revalidate
the snapshot during application and publish root traversal last. All
fixtures are new and quiescent; no root traversal of arbitrary project
trees and no group membership changes. This is not transactional enrollment
of an existing live project. If application fails, do not start a runtime
on the partially prepared fixture.

The existing actual PHP/HTTPS/SQLite lifecycle test now exercises
developer-created source and build files, inherited read-only permissions,
runtime-created nested data, actual writes to pre-provisioned SQLite,
new database creation and the neighboring project denial, using a
developer with no runtime group memberships. Test the behavior both on
the distro fixture backend and the LTS backport. The helper is not
installed or exposed as RPC.

**Creation-mode limitation.** POSIX ACL inheritance respects the mode
requested by the application. In a separate Linux experiment, a
runtime-owned SQLite database created with SQLite's usual mode 0644
had a read-only effective developer ACL (mode 0640). Its developer
transaction failed with a readonly-database error. The runtime smoke
records that result explicitly if reproduced, without artificially
chmod'ing the new database. Explicit private mode 0600 must not be
bypassed. Ordinary Laravel installer-created SQLite remains a separate
positive write test. Full developer access for approved runtime-created
data requires a production policy and further evaluation (e.g. idmapped
writable mounts where supported, or a narrow owner-side sharing operation
for explicitly authorized data). Do not blanket widen permissions or
require recurring manual chmod for normal development. This limitation
remains a blocker for claiming full developer compatibility.

Production still needs a protected publication/enrollment transaction,
coordination with existing public ACLs and arbitrary legacy ownership,
rollback/recovery, bounded handling of larger real dependency trees, and
tests for actual installer/build/editor behavior (including restrictive
creation modes or atomic replacement that can mask inherited ACLs).
A metadata scan alone cannot prevent new hardlinks/renames/mounts by a
concurrent trusted owner. Do not use this private-fixture preparation
routine as a bulk repair or silently replace a user's existing ACLs.


## ID-mapped writable data feasibility (E1.3i)

The ACL creation-mode limitation is confirmed on both tested Bubblewrap
builds. Prefer testing ID-mapped mounts before implementing permission
repair. Map exactly the trusted developer's on-disk UID/GID to the
project runtime's apparent UID/GID, only for explicitly shared project
data. PHP then creates files that remain owned by the developer on disk,
including an ordinary SQLite database, without adding runtime groups,
loosening umasks or changing application code. The owner-only mode of a
shared file (0600) is preserved: it belongs to the developer on disk and
to the runtime in its mapped view. This is an intentional shared-owner
policy for those directories, not a promise of privacy from the trusted
developer. Runtime-private endpoints, secrets outside shared data and
session state must remain outside mapped shared mounts. PHP source
remains read-only; agent edit sessions and authorization stay separate.

The new disposable test reuses the established project/runtime identities.
It creates two new data trees, maps them through protected sibling
mountpoints in a separate private mount namespace, verifies exact UID/GID,
unchanged inode/host ownership and nosuid,nodev,noexec, and launches PHP
CLI/PDO SQLite through the existing Bubblewrap descriptor-bind pattern.
It checks both directions of real data writes, restrictive modes, read-only
source and sibling denial, then unmounts and verifies persistent data.
No fallback if the mapping is unsupported, no production mount helper or
changes to the existing FPM/HTTP ACL test. The backport workflow repeats
this experiment. References: [Linux kernel ID mapping documentation](https://docs.kernel.org/filesystems/idmappings.html),
[mount_setattr(2)](https://man7.org/linux/man-pages/man2/mount_setattr.2.html),
[util-linux mount manual](https://github.com/util-linux/util-linux/blob/master/sys-utils/mount.8.adoc).

Success in hosted CI is not evidence that the actual WSL filesystem and
kernel support the same operation. Next verify on a disposable directory
on the target Linux filesystem; do not map Windows DrvFs or existing
projects. If supported, integrate a narrowly scoped, root-controlled
publication of mapped data with the real PHP/HTTP pair and Laravel
installer/build workflow. The production design must prevent arbitrary
source selection, unsafe nested mounts/hardlinks, replacement races and
UID reuse, preserve existing data/ACLs and recover cleanly after stop,
restart, failed activation and WSL restart. Never pass CAP_SYS_ADMIN to
an application or agent. Keep activation blocked until the actual
compatibility and security requirements are satisfied.


### Descriptor-based ID-mapped publication and managed runtime filters (E1.3j)

The next implementation step replaces the disposable IDMAP smoke's
path-based mount call with an internal detached mount primitive.
`open_tree(2)` clones the already pinned directory non-recursively,
`mount_setattr(2)` applies one exact non-root UID/GID mapping and
nosuid/nodev/noexec before publication, and `move_mount(2)` attaches
the clone to an already pinned, empty protected target. No checked
source pathname is reopened; the test deliberately replaces that name.
The primitive rejects a mismatched source owner and nonempty target.
It creates a dedicated mapping user namespace with only the exact
requested IDs; it never exposes a command interface to an agent.
Unavailability or mismatched attributes are errors, not a fallback
to a broader bind mount.

The same socket/syscall restrictions accepted on the workstation are
applied to the existing disposable systemd PHP/HTTP runtime pair:
native syscall architecture, allow AF_UNIX/INET/INET6/NETLINK, reject
other socket families, deny io_uring, and retain NoNewPrivileges.
The real FPM worker and a fixed child probe must demonstrate the
effective policy in the existing FPM/HTTPS smoke. The host gateway,
developer session, Windows interop outside the sandbox, local model,
and unrelated services are not changed.

This is not a production mount authorization API. The new primitive
is exercised only in the existing private CI mount namespace.
A protected publication registry, stable source/target and mount
identities, enrollment/hardlink policy, lifecycle cleanup and
recovery, and integration of mapped writable directories into the
real FPM/HTTP pair remain required before activating project
isolation. Existing ACL/FPM mode-limit assertions stay in place
until that integration passes.


### ID-mapped data integrated with the FPM/HTTP fixture lifecycle (E1.3k)

The existing two-project systemd/FPM/HTTPS test now exercises an additional
explicit `idmapped` profile on a fresh pair of disposable projects. The
original ACL-only profile (including its creation-mode limitation) remains
in the suite. This is not a switch for installed projects.

- The developer-owned source tree keeps the existing read-only runtime ACLs
  for code. In the new profile the three existing, explicitly selected data
  roots (`storage`, `bootstrap/cache`, `database`) retain their original
  private owner permissions, rather than receiving runtime write ACLs.
- `FixtureDataViews` publishes detached ID-mapped mounts under a fixed
  root-protected fixture sibling before either runtime unit starts. PID 1
  must see these mounts; unlike the previous CLI-only test, they are
  attached in the disposable runner's service mount namespace, under a
  dedicated bind holder made private before attaching the data. This is
  limited to the CI fixture and explicitly refuses WSL. The controller
  validates the registration/runtime binding, source metadata (including
  mount identity, owner, hardlinks and special entries) and publication
  identities. This scan assumes a quiescent controlled fixture.
- The non-root launchers pin the selected mapped descriptors, verify the
  original inode, mapped UID/GID and nosuid,nodev,noexec, and do not fall
  back to original writable directories if a mapped root is absent.
  PHP retains the existing absolute project paths and read-only code;
  HTTP uses only the approved published-storage subtree, read-only,
  through the same mapped storage view.
- `RuntimePair` starts with data publication, then PHP and HTTP. On stop
  or startup failure it verifies both processes/listeners stopped before
  unmounting only the recorded publications. A missing, foreign, or
  inconsistent publication is degraded, not a successful start/stop.
  No lazy detach and no recursive removal of mounted fixture data.
  Restart re-publishes the same data without changing physical ownership.

The expanded HTTPS smoke is required to exercise real FPM-created SQLite,
mode 0600 and nested files, actual developer transactions without ACL repair,
FPM read-back while running and after restart, static assets and FPM-created
published files, mount flags inside FPM, sibling denial, a partial publication
failure and a real HTTP startup failure with rollback. It must leave the
original data intact after both projects stop. Existing CI and backport
workflows run the same suite.

The publication references are deliberately in-memory and fixture-only.
Persistent production enrollment/publication records, reconciliation after
daemon/WSL restart, protection against concurrent hostile editors,
arbitrary framework layouts and actual existing project activation remain
separate gates. The installer/updater and deployed project RPCs are unchanged.
Existing MariaDB project accounts/grants, optional Redis, shared Mailpit
mailbox, panel, PWA, widgets and Windows integration remain unchanged.


### Registered project preview using private systemd mounts (E1.3l)

The first practical ASUS project is created through the normal DevStack
installer, not by the runtime fixture. The installed read-only preflight
confirmed the complete standard Laravel layout and registration identity.
The actual settings have local mail enabled. Do not rewrite those settings
or switch the ordinary domain to a runtime that cannot send SMTP.

The optional administrator-only `project_pilot.py` entry point is installed
by the normal install/update path but never starts automatically. It accepts
only a registered canonical project with a valid project ID, standard
`public` webroot, HTTPS, no MariaDB or custom Nginx rules. It requires the
reviewed Bubblewrap version floor (0.13.1), ordinary root-owned binary and
no file capabilities. The existing preflight continues to report blocked:
the preview does NOT constitute production enrollment or an agent grant.

- Reuse RuntimePlan, ProjectHttp, RuntimePair, the protected UID/GID
  allocation journal and FD-based IDMAP. The root-owned pilot identity
  journal lives in the existing DevStack state directory, keyed by the
  full registration ID. Never adopt an existing account or recycle a UID.
- Each of the two transient systemd services uses PrivateMounts and a
  systemd-owned RuntimeDirectory. The fixed ExecStart helper validates
  the registration and identity, makes propagation private and prepares
  its own mapped mounts. This must occur in ExecStart, not ExecStartPre
  (systemd does not share mount namespaces between separate commands).
  The full code view is read-only IDMAP; PHP has three separate writable
  data views. HTTP binds only public and published storage read-only.
  No original project ACLs are rewritten. Root is used only for the fixed
  preparation, then all UID/GID values and supplementary groups are dropped
  before Bubblewrap and the existing runtime seccomp policy is confirmed.
- The private mount namespace dies with the unit; systemd owns runtime
  directory cleanup. No host publication table or new privileged daemon.
  Normal stop/start reuses the durable registration and data. A preview
  does not persist across WSL shutdown. The administrator explicitly starts
  it again when required. An explicit stop-id remains available even after
  registry removal; the locked account journal is retained.
- The existing public domain, shared PHP pool, registry, certs, mail setting,
  installer, and all optional services are untouched. The preview exposes
  an HTTP Unix socket for trusted administrator testing, not an additional
  TCP port. It has no SMTP connectivity. Do not use the legacy application
  concurrently during the preview acceptance and do not grant any agent
  access while the legacy public runtime remains available.
- Unit tests and the Bubblewrap backport workflow exercise the installed
  entry point with a canonical /home developer project and protected
  registry on a disposable runner. The existing ACL and IDMAP fixture
  regression profiles are unchanged. The new smoke checks private mount
  cleanup, actual FPM SQLite and mode 0600, developer access, sibling
  denial, published storage, stable ownership/ACLs, restart, and stop-id.
  This is not yet a real Laravel/WSL acceptance.

After CI and standard DevStack update, the administrator can run:

```bash
sudo python3 -I /opt/devstack/daemon/project_pilot.py start isolation-test
sudo python3 -I /opt/devstack/daemon/project_pilot.py status isolation-test
sudo python3 -I /opt/devstack/daemon/project_pilot.py stop isolation-test
```

The start response gives the exact preview_socket and project_id. Use the
socket for acceptance requests with the original Host and HTTPS forwarding
headers, without changing DNS, certificates or the normal Nginx vhost.
If the registration was removed, stop-id accepts the full saved project_id.
Stop the preview before updating DevStack or deleting/recreating the
project. Do not run these commands against an installed project before
reviewing this change and its CI results.

The remaining production cutover must preserve existing mail/service access
and the existing project lifecycle. Do not silently fall back to shared
unrestricted PHP for agent-modified code. Agent authorization is a later,
separate boundary. This preview intentionally does not introduce a mount
broker, new service catalog or automatic migrations.

### Registered isolated HTTPS and optional Mailpit (E1.3m)

An administrator can opt an eligible registration into isolated HTTPS after
installing or updating DevStack. Use a standard HTTPS project with a `public`
webroot, the conventional writable `storage`, `bootstrap/cache`, and `database`
directories, and SQLite. Projects with managed MariaDB, custom Nginx rules,
an unrecognized configuration or an invalid registration are refused. The
reviewed Bubblewrap version floor and the existing filesystem prerequisites
apply. Agent authorization remains a separate future step.

```bash
sudo python3 -I /opt/devstack/daemon/project_pilot.py activate <slug>
sudo python3 -I /opt/devstack/daemon/project_pilot.py status <slug>
sudo python3 -I /opt/devstack/daemon/project_pilot.py restart <slug>
sudo python3 -I /opt/devstack/daemon/project_pilot.py deactivate <slug>
```

The generated vhost retains the existing domain, certificate and listener.
The host Nginx forwards to the registration's private Unix socket and
replaces client forwarding headers. Static and published storage files are
served inside the HTTP sandbox. The shared FPM pool is retired on cutover.
A stopped or failed isolated runtime returns a gateway error. An explicit
`stop` keeps the isolated route and activation intent. Daemon startup
reconciles that intent and can start the runtime again when WSL boots.
Deactivation restores the canonical ordinary vhost and pool when the project
has no agent grants. The panel displays the generated vhost read-only while
isolation is active. Managed deletion stops the runtime and retains the
developer's files. Re-creation has a new registration and starts in
ordinary mode.

The existing `local_mail` project opt-in enables SMTP access on activation.
Systemd's socket activation helper and socket proxy connect the sandbox's
loopback SMTP port to a single registration-private Unix socket and onward
to `127.0.0.1:1025` on the host. The host-side relay runs as the unprivileged
runtime identity and ends with the FPM unit. The Mailpit service is started
and stopped independently. Laravel keeps its existing SMTP settings and PHP
`mail()` uses the existing Mailpit binary when installed. Mailpit's UI and API
remain accessible via the existing host workflow. The isolated runtime has
no supported connection to the host HTTP API, MariaDB, Redis or other
host-network endpoints. There is no additional package or TCP listener on
the host.

The automated acceptance test runs through the existing Bubblewrap backport
workflow and its installed-project tests. The isolated application test
uses the official Laravel Livewire starter kit, Chromium, built Flux/Vite
assets, a Livewire POST, session cookies, a storage upload and SQLite
writes. Live Vite HMR, remote dependencies, additional writable paths and
agent access remain outside this activation profile.

For ASUS acceptance, use the registered SQLite project with the intended
local-mail setting and a clean canonical vhost. With trusted HTTPS and the
normal developer login, verify the Laravel page, a Livewire interaction,
published storage, SQLite and the normal CLI/build/test workflow. Send a
unique mail through the application and verify it in the existing DevStack
mail view, including the usual Windows notification if enabled. Restart
the isolated runtime, then verify recovery after the next normal WSL boot.
Repeat the application check after deactivation if you want to restore
ordinary routing.
