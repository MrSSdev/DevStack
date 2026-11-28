# Linux executor acceptance (E2.1a)

The executor runs a controlled command in either of two newly generated
fixture projects. A root test controller selects a stable project ID, a
logical agent ID, a random session ID, a profile and an argument vector.
Only the fixture controller stages the launcher and writes these records.
It accepts no existing project path or registry and exposes no endpoint.
The production installation and updater leave out the executor and both
test entry points. [Issue #51](https://github.com/MrSSdev/DevStack/issues/51)

## Execution boundary

- The fixture registration binds a project ID to the source device and
  inode in a protected parent. The launcher checks the registration and
  both identity journals, opens every component without following
  symlinks, and checks mount IDs. A bounded FD-relative walk rejects
  shared hardlinks, symlinks, foreign ownership, special files,
  group/world-writable entries, and Git/worktree metadata.
- The existing IDMAP primitive creates a nonrecursive view from the open
  FD. Bubblewrap 0.13.1 or later binds that view at `/workspace`, with
  `nosuid,nodev,noexec` in both profiles. The read profile requires a
  read-only view. The write/CLI profile allows writes to the synthetic
  tree. The developer UID/GID remain the physical owner of created files.
  Run interpreted project scripts via the trusted interpreter. Direct
  execution of project scripts and ELF files is denied. Native add-ons,
  package bin shims, native build tools and Vite HMR need further review.
- Each transient unit gets a separate locked, non-login OS identity from
  the existing allocation journal. The full session ID is bound to the
  account; an existing name is refused. Distinct sessions on the same
  project have their own UID/GID, cgroup and namespaces. The launcher
  drops all root IDs and supplementary groups before invoking Bubblewrap.
  The controlled test waits for the unit and its processes to disappear
  before removing its temporary account, and checks that no process
  retains the UID. Production UID retirement, stale-state reconciliation
  and handling UID/GID reuse remain separate work.
- The rootfs contains selected system tools and libraries mounted read-only,
  the selected workspace, a private PID view and devices, and fresh tmpfs
  for HOME, temporary files and caches. Mount, user, PID, network, UTS and
  IPC namespaces are independent. Systemd imposes NoNewPrivileges and
  filters address families, compatibility architectures, io_uring and
  other sensitive syscalls. Bubblewrap drops capabilities, disables nested
  user namespaces and installs a second filter that permits only AF_UNIX
  sockets after namespace setup and denies publishing named sockets. Host
  network endpoints, abstract sockets and filesystem sockets have no route
  into the sandbox. WSL's `/init`,
  interop sockets, Windows mounts, developer HOME, host credentials and
  host service sockets are outside the view. The launcher closes inherited
  FDs and clears the environment. Standard input is null and output goes
  to a protected regular fixture log.
- The proof uses a separate, root-generated read-only Git directory and
  index for each project, with explicit Git and work tree paths, hooks and
  fsmonitor disabled, no global or system config, and no optional index
  writes. It checks `git status --porcelain --untracked-files=no` and
  `git diff --no-ext-diff --no-textconv` for the tracked proof file.
  The controlled tools exercise PHP CLI and SQLite, a locally installed
  npm dependency, Composer autoload for a local package with plugins and
  scripts disabled, and Laravel's `artisan --version` with preinstalled
  vendor dependencies when included. PHP uses read-only system timezone data
  and a fixed set of distribution extensions from a private INI directory.
  No package download or remote Git
  operation occurs in the executor.
- The session unit name comes from the protected session record. Startup
  requires an empty systemd runtime directory and private mount propagation.
  Stop checks the exact transient unit identity. An independent cgroup,
  bounded runtime and control-group termination remove descendants and
  service-private mounts on stop, process failure or timeout. Nothing is
  restarted automatically. The controller also removes temporary accounts
  and fixtures after a successful suite. Failed or interrupted tests may
  leave root-protected synthetic diagnostics for inspection.

## Limits and next gate

The whole-tree scan is intentionally limited to newly generated fixtures
whose parent and Git metadata are root-controlled and whose synthetic owner
only runs the test's fixed preparation and verification commands. A
concurrent sandbox can introduce a new name after a scan. Descriptor-pinned
publication, the nonrecursive mount and the isolated rootfs keep that session
confined, and kernel hardlink rules prevent linking across the exposed
mounts. The scan is insufficient to authorize a mutable host checkout with
other editors, symlinks, mounts, Git worktrees, or pre-existing aliases.
The profiles give same-project writers shared data, so simultaneous edits
can conflict. Application/runtime enrollment and session revocation need to
be coordinated with the protected project lifecycle before any grant can be
issued. E2.1a leaves registered projects, their runtimes and agent access
outside its authority.

Files produced by an untrusted tool may include scripts, dependency
configuration, Git attributes or links that matter to a trusted process
later. Review changes before running them in a normal developer terminal,
building them outside the isolation boundary or activating an application
runtime. In particular the fixture does not use a host Git worktree or read
a real repository's hooks, linked worktrees or configuration. The fixture
is disposable after testing.

The model and local agent remain native Windows design targets. WSL provides
only the isolated Linux execution boundary. A Windows process can have its
own access to files over WSL UNC and to Windows processes and networking.
Those permissions need separate enforcement and validation. E2.1b covers
production registration/session control. The broker and grants, tool/CA
integration, Playwright, Mail, native Qwen/Codex, and AI settings are later
stages.

## CI and local ASUS acceptance

The existing [authenticated Bubblewrap backport workflow](../.github/workflows/bubblewrap-backport.yml)
keeps the real E1 and E2.0 regression tests. On its disposable hosted
runner, it prepares two fresh executor fixtures and copies just `vendor/`
from the trusted Laravel test into one of them for Artisan. The executor
proof checks namespace and identity separation across concurrently active
same-project and sibling-project sessions, private HOME, physical ownership,
RO/RW flags, interop and host access denials, descriptors, syscall/socket
filters, metadata and mount rejection, and cgroup cleanup. See the Actions
checks on [PR #53](https://github.com/MrSSdev/DevStack/pull/53).

For ASUS, run the WSL-specific mode in a spare Ubuntu 26.04 WSL2
distribution without DevStack, with systemd enabled and an ext4 Linux
workspace. Artificial nested host mounts are exercised only by CI. Use a reviewed
checkout of the exact PR revision and the already reviewed APT-managed
Bubblewrap package. Verify its version and provenance against the existing
[backend procedure](BUBBLEWRAP-BACKPORT-EXPERIMENT.md). The local smoke
does not configure APT or replace packages. Prepare distro binaries
`python3`, `git`, `nodejs`, `npm`, `composer`, `php8.5-cli`,
`php8.5-sqlite3`, `php8.5-mbstring`, `php8.5-xml`, `sudo`, and their
normal system dependencies in the spare
distribution. Store the checkout on the Linux filesystem.

1. From PowerShell, confirm the target and WSL version with
   `wsl --list --verbose`, then enter the spare distribution explicitly
   with `wsl -d <disposable-distribution>`. Confirm `ps -p 1 -o comm=`
   reports `systemd`. Leave the daily DevStack distribution running as usual.
2. From the reviewed checkout in that distribution, as the regular test
   operator, run `bash tests/project-sandbox-probe.sh`. Stop the acceptance
   if the probe fails.
3. Run `sudo /usr/bin/python3 -I tests/project-executor-systemd-smoke.py --local`.
   To include real Artisan, append `--laravel-vendor /path/to/vendor.tar`
   using a trusted archive containing only `vendor/` previously prepared
   from a throwaway Laravel installation. Never use an actual working
   project's files or credentials as input.
4. Verify the reported RO/RW, concurrent stop, crash/timeout and refusal
   checks. Confirm `systemctl list-units --all 'devstack-executor-*.service'`
   reports no live fixture units and that the fresh fixture path under
   `/var/lib/devstack-executor-probe-*` was removed after success. On failure,
   save the diagnostics and stop acceptance. If cleanup could not be
   confirmed, terminate only the spare distribution using
   `wsl --terminate <disposable-distribution>` from PowerShell.

Do not run the backport CI script on the ASUS. It installs and restores
packages and configures a disposable Linux runner for the E1/E2.0 tests.
