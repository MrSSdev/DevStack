# DevStack extensions

Core discovers approved extensions in local, administrator-managed release
directories. DevStack Tools is the first implementation. Install it in
Ubuntu with the terminal commands in the [Tools guide](../extensions/devstack-tools/README.md).
When a compatible release becomes active, the dashboard shows **Rozszerzenia
→ Tools** at `https://devstack.test/#/extensions/devstack-tools`. The regular
refresh button updates the menu. An open extension view checks for changes
every five seconds while visible.

## Contract 1

Core registers each approved extension ID, install root and ownership marker
in `daemon/extension_manifest.py`. The publisher supplies a root-owned
`.managed` marker and shared `.lock`, the `releases` directory, and an atomic
`current → releases/release-XXXXXXXX` pointer. Consumers use only the active
release. A candidate can be checked with the deployed validator:

```bash
python3 -IB /opt/devstack/daemon/extension_manifest.py devstack-tools PATH_TO_PROTECTED_RELEASE
```

The [Tools manifest](../extensions/devstack-tools/manifest.json) is the working
example. Each top-level field is required. Arrays may be empty when the
feature is unused.

| Fields | Meaning |
| --- | --- |
| `id`, `name`, `description`, `version` | Registered slug, plain text, and release version as `X.Y.Z`. |
| `contract`, `core_contract` | Integer format and required Core contract. This implementation requires `1` for both. |
| `menu` | Null for no sidebar entry, or a label, integer sort order, `wrench`/`package`/`book` icon and declared view ID. |
| `view` | Stable view ID, title and ordered sections with stable IDs, labels and a supported component kind. |
| `terminal` | Labeled commands presented as plain text for the user to copy. |
| `tools` | Tool IDs, name, description, version, protected relative installation markers, optional fixed Chromium readiness probe or known service, dependency labels, HTTPS documentation URL and copyable example. |
| `instructions` | Instruction IDs, title, description, format, related tools, eligibility facts, selection explanation and intended audience. |
| `legacy` | Retained Agent Skills IDs eligible for explicit migration from an exact manual symlink. |
| `automations` | Named `reconcile_instructions` rules connected to supported lifecycle events. |
| `operations` | Explicitly enabled `sync`, `set_automatic` and `migrate` panel operations. |

The reusable section kinds are `overview`, `instructions`, `projects` and
`automations`. Titles, ordering, sections, tool and instruction metadata
come from the active manifest. React renders text and generic controls.
Core rejects unknown shapes, operation names and component kinds. Documentation
links must use HTTPS. Instruction previews show escaped source text. The
authenticated API uses the existing CSRF, permission and notification paths.
Release changes remain terminal operations.

For a new approved extension, register a dedicated install path and marker,
implement protected publication with the same contract, add a compatible
manifest, and test installation, candidate rejection, upgrade and removal.
Changes that use existing components, rules, tools, and formats are delivered
by the extension alone. New renderer kinds, trusted probes, operations or
distribution formats require an accompanying Core capability and tests.

## Eligibility and project lifecycle

The format is independent of the client. Contract 1's adapter recognizes
`agent-skill` and reads `skills/<instruction-id>/SKILL.md` from the active
release. Its current audience is `trusted-developer`. Each selected
instruction is a symlink in `.agents/skills/` to the stable installed skill
alias. Contents update centrally. A future adapter can use the central
source with a different client format and permissions policy.

Core uses the configured developer's authoritative registry and requires
successful project/framework setup. Contract 1 facts are `web` (a registered
web environment), `local_mail` (project opted into local mail) and
`mailpit_installed` (the supported Mailpit release is installed). Each rule
also requires its declared tools. A stopped Mailpit affects readiness in
Overview while the mail instruction remains selected. Technology reporting
uses registered settings and the existing read-only Composer metadata
detector. Project code is never executed to select instructions.

The existing daemon observes committed project registrations, configuration
and removals, successful framework jobs, and protected extension activations.
It reconciles on startup while Ubuntu is running, after changes, and
periodically to repair drift. An extension update, rollback or removal
performed from the terminal is observed without restarting the panel.
The project registry lock precedes any shared release locks. Publication
uses the extension lock alone. A failed instruction operation is recorded
for retry and never participates in the project's hosting transaction.
Manual requests enter the same reconciliation path and the UI shows
acceptance and then the actual outcome by project.

## Ownership and removal

Core keeps durable settings, results and ownership receipts under
`/var/lib/devstack/extension-instructions.json`. It uses stable project
registration IDs, plus the opened directory identity to detect replacement.
A bounded fixed worker runs as the developer with restricted systemd
permissions, no network access and no access to the privileged socket. It
walks project directories by descriptor and rejects symlink parents,
different mounts and unexpected directory ownership. It stages a unique link,
records the inode before publication and installs it with an atomic
no-replace rename. On retry it recognizes staged or already published links
from the durable record. Cleanup checks both recorded link identity and
the exact destination. A developer with access to the project can still
modify their workspace, so this is an ownership and safety mechanism for
managed files. It does not enforce an agent's isolation.

Disabling automatic management for an extension or project detaches its
owned links. Re-enabling runs selection again. User files, foreign links and
pre-existing matching links are preserved. A matching manual `devstack-tools`
link suppresses redundant automatic attachment. **Migruj ręczne
dowiązanie** checks the recorded legacy link and removes it only after the
applicable replacements are ready. Conflicts can be resolved by the owner
and retried from the Projects section. DevStack leaves `AGENTS.md`, other
client configuration, and all source and database files intact. Retired
registrations are cleaned using ownership records even if the extension has
been uninstalled or the same directory name is registered again.
