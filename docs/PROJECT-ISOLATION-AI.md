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
