# Temporary Bubblewrap LTS backport experiment

This is a test-only CI workflow, not an installed DevStack component or a
distribution package repository. It tests a possible interim system package
for Ubuntu 26.04 LTS while CVE-2026-87766 is outstanding in the distribution
package. The pinned source is Ubuntu tenacious bubblewrap 0.13.1-1 (Debian
packaging), obtained through a separate authenticated source-only APT index.
Binary dependencies come only from the original LTS runner. The upstream
orig archive must also match the release SHA-256. No newer distribution
binary is installed and no persistent repository is added.

The local backport version is checked to be newer than the installed LTS
package and lower than the original source revision, using Debian version
ordering. The explicit dch --force-bad-version flag permits only this
intentional changelog version ordering; it does not skip build tests or
signature/checksum checks. The authenticated APT source index is the
required archive trust chain, independently of whether dpkg-source has
the Debian uploader key for the inline .dsc signature.

Ubuntu 26.04 ships debhelper 13.31; the newer source packaging requests
compat 14. The experiment adapts only debian/control to compat 13 and
explicitly restores the dependency substitution variables required by the
older helper. It checks the expected single-binary source layout, records
the actual packaging diff in the artifact, verifies generated libc6
dependencies and still runs dpkg-checkbuilddeps and the full test suite.
No Bubblewrap C source, namespace flags, system security settings or
test exclusions are changed. See the [Debian compat checklist](https://manpages.debian.org/unstable/debhelper/debhelper-compat-upgrade-checklist.7.en.html)
and [Debian packaging reference](https://manpages.debian.org/unstable/debhelper/debhelper.7.en.html).

The locally versioned package 0.13.1-1~ubuntu26.04.1+devstack1 is built as
an ordinary user, tested with the existing namespace and full isolated
FPM/HTTPS/lifecycle fixtures after APT installation on the disposable runner,
and the exact original distribution package is restored through APT.
Build/test failure blocks artifact publication. CI does not weaken AppArmor,
enable setuid, skip tests, or replace a workstation package.

Sources: [Ubuntu CVE status](https://ubuntu.com/security/CVE-2026-87766),
[Ubuntu regression notice](https://ubuntu.com/security/notices/USN-8779-2),
[Ubuntu source package](https://packages.ubuntu.com/source/tenacious/bubblewrap),
[upstream release](https://github.com/containers/bubblewrap/releases/tag/v0.13.1).

No production activation is implied by a successful build. Review the
packaging and dependencies, use evidence from a reviewed main workflow
rather than a PR artifact, and separately accept the target WSL kernel,
official Codex agent selection and Playwright behavior. Before any local
install, retain the exact official package for rollback and inspect APT's
transaction. The version is deliberately not pinned/held; a local newer
upstream version may outrank future LTS security updates until explicitly
reconciled. Return to a reviewed fixed official LTS package when available.
