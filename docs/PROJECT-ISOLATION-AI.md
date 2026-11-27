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
