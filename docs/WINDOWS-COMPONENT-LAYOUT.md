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
