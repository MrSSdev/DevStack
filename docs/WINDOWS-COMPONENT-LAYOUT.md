# Windows component layout (implementation checkpoint)

**Not yet a workstation migration.** This is the first shared layout
bootstrap. The current Windows integration and upgrade paths are not switched
to it. Do not run Prepare on a working ASUS installation or copy Hosts files
manually. The bootstrap intentionally refuses a legacy user-owned root.

Target layout:

- C:\DevStack: owner BUILTIN\Administrators, protected DACL, Administrators
  and SYSTEM FullControl, intended Windows user ReadAndExecute on the root
  only (not inherited).
- Hosts: owner Administrators, protected DACL, Administrators and SYSTEM
  FullControl with inheritance. Preserve the existing privileged-code boundary;
  there is no direct user ACE.
- LocalNotifications: private user-owned directory; user, SYSTEM and
  Administrators FullControl with inheritance. The ordinary updater stages
  within the component and must not be elevated.
- Widgets: private user-owned directory; user and SYSTEM FullControl with
  inheritance. Token files must retain private ACLs; do not print their contents.

Only requested component directories are created. Creation uses final ACLs
and verifies the result. Existing unexpected directories, owners, ACEs and
reparse points are refused, not repaired or overwritten. C:\ ACL is never
modified. The check rejects destructive or ACL-changing rights on C:\ for
untrusted principals; a standard Windows create-directory permission is
not itself such a right.

The entry point is windows/initialize-windows-layout.ps1 (Windows PowerShell
5.1). Its default Inspect mode is read-only and reports the root state and
current user SID. On a **fresh test machine only**, an elevated Prepare
invocation requires the intended user's SID (recorded in the non-elevated
session first) and explicit component names. It creates no component files,
runs no Windows tasks or WSL processes, and makes no registry changes.
Repeating it with the same valid layout leaves existing ACLs untouched.

The shared policy is in windows/windows-layout.ps1. It validates directory
boundaries, not the contents of a component. In particular, no elevated
installer may execute anything from the private components.

## Remaining gates

- Implement a separately reviewed migration of the existing user-owned root
  using a newly provisioned administrator-owned root. Do not merely tighten
  the old ACL in place: existing handles may retain write/delete access.
  Preserve notification functionality and provide an explicit rollback.
- Integrate the common policy with Hosts installation and the scheduled task,
  backup path and rollback; publish privileged code only from a trusted
  source into the protected root.
- Integrate both widget installers and all readers, preserving current
  tokens, cache and logs without disclosing them; update packages and tests.
- Integrate notification fresh installation, update and uninstall with the
  shared root; normal updates must remain unelevated.
- Verify old/new installation, rollback, actual non-elevated UAC token,
  PWA/WinRT, widgets, hosts and no unintended WSL wakeup before deployment.

The new CI fixture exercises fresh NTFS ACLs, idempotence, selected
components, legacy root refusal, unexpected ACL refusal and a junction root.
This does not yet certify an actual workstation migration or a medium-token
UAC test.

## Root-only migration checkpoint

The migration engine is windows/windows-layout-migration.ps1. Do **not**
run it on the workstation yet: the future orchestrator must drain the
notification collector and stage the notification files as the ordinary
Windows user before publication, and coordinate the Hosts and Widgets
upgrades. This engine alone does not perform those operations.

Stage prepares a distinct administrator-owned root with the notification
directory only, under a uniquely named C:\DevStack-migration-<id> container.
Its administrator-owned journal records the original and replacement NTFS
directory identities. The old root remains untouched. The root-only engine
does not read, copy, execute, or delete legacy component files as admin.

Publish moves the old root to previous and the new root to C:\DevStack.
Each move is a no-replace rename of the verified directory object by handle,
not a recursive copy or in-place ownership change. There is a gap between
the two renames: this is not an atomic cross-directory transaction. A
competing path, identity change, reparse point or incompatible handle is
refused. Inspect recognizes the staged, between-renames and published
states by the protected journal and actual directory identities.
Rollback restores the original directory and retains the replacement in
next; on ordinary second-rename failure the engine attempts restoration.
If a name has been occupied or identity/policy changed, keep both trees and
inspect, rather than overwriting anything. The previous user-owned tree is
not an immutable backup against the signed-in user.

The root-only rollback is blocked once any additional root component is
present or the known elevated Hosts task is no longer in its legacy state.
The full integration must have its own task-aware rollback ordering and
retire this root-only transaction before activating new privileged code.
No production cleanup, notification installation, WSL wakeup, task change,
or registry change is performed by this engine.

CI uses disposable directories and fixture contents to verify object
identity, no-replacement behavior, interruption/collision recovery,
idempotence and retention of both trees. It does not prove native WinRT,
a real medium-token copy, or a complete Windows installation.

## Notification integration checkpoint

The notification installer no longer creates C:\DevStack or its component
implicitly. PrepareDirectory requires elevation, the intended user SID and
the shared protected-root policy. Existing legacy installations can still
update their existing private notification component until migration.

The root-only orchestrator migrate-notification-layout.ps1 runs from an
ordinary Windows PowerShell 5.1 session in a verified bundle, with Ubuntu
already deliberately stopped and no launcher running. It requests elevation
only for Stage and Publish (or a needed Rollback). The ordinary process
copies the two known files with no clobber, validates SHA-256 and file ACLs
and checks the published files again. No notification code is executed by
the elevated process, no registry identity/opt-in marker changes, and no WSL
startup. Publish refuses incomplete payloads by metadata. Keep Ubuntu
stopped throughout; the list-running guard is not an atomic maintenance lock.
The previous tree and protected journal are retained for inspection.

This is not yet the complete workstation migration: Hosts and Widgets,
package/release coordination and controlled retirement of the old tree
still need implementation and acceptance. Do not run the partial branch on
ASUS yet. The copy helper is tested with fixture files, not a real medium
token; production staging rejects elevation.

## Hosts integration checkpoint

New privileged scripts are published from the reviewed bundle, not from
legacy ProgramData, into a protected release directory under
C:\DevStack\Hosts. Both scripts are hashed, written with protected
Administrators/SYSTEM file ACLs and published as a pair; identical inputs
reuse a verified generation. Existing task upgrades change only the
action, retain the previous definition and release and verify the S4U,
Highest, no-trigger contract. On failure they attempt action rollback.
Fresh setup still requires an already-running, installed Ubuntu. An
existing known task can be migrated from a verified local Windows bundle
with Ubuntu stopped; it is not deliberately awakened. Its old task files
and historical backups are retained until device acceptance and cleanup.
The runtime hosts backup is now in C:\DevStack\Hosts.

The release subdirectory is intentional: it avoids replacing two scripts
while a task might still be executing them. Do not delete old releases
until task state and rollback needs are checked. CI checks real task
registration and action switch without running the task; actual S4U
execution and hosts reconciliation still require ASUS acceptance.
