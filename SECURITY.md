# Security policy

**DevStack is a local development tool for trusted code, not production hosting, a public control panel or a project-isolation boundary.** It deliberately includes privileged Linux and Windows components. Review changes and protect the Windows session, WSL filesystem and backups.

## Dashboard and helper

- Nginx listens on WSL loopback; Windows localhost forwarding remains part of the trust boundary. Keep normal firewall policy and do not expose the dashboard to a LAN.
- Password authentication uses an Argon2id hash, secure HttpOnly SameSite Strict cookies, session expiry, CSRF/origin checks and login throttling. Missing authentication configuration fails closed.
- The PHP manager runs as the non-login `devstackweb` account. A local Unix socket and peer UID check gate the privileged helper's typed operation allowlist.
- Project Nginx configuration and paths are validated. Syntax checks precede reloads; invalid edits are rolled back. Per-project PHP-FPM pools run as the trusted developer, not separate security tenants.
- Database credentials and the project registry are protected in Linux. Credential reveal requires dashboard authentication/reauthentication. Never expose these files as widget or HTTP status content.
- The mkcert private CA key stays in Ubuntu. Windows trust of the public CA requires deliberate fingerprint review and confirmation.

## Windows integration

The hosts task is installed explicitly by a Windows administrator under the WSL-owning account. Scripts in `C:\ProgramData\DevStack` use protected ACLs. Only a validated managed hosts block is reconciled; unrelated lines are preserved. The Linux helper triggers a fixed task name rather than passing arbitrary commands, domains or secret values to Windows.

Widget MSIX signing is separate from HTTPS CA trust. The installer checks package identity/version, the matching MSIX/CER signer and signature trust before deployment. Its development CER is imported into TrustedPeople, not the TLS Root store. CI removes the private signing PFX and distributes only the signed MSIX and public CER. Review the exact run/revision before trusting it. Test signing is not independent security review or a Store release.

## Widget actions and credentials

The Windows provider validates widget identity, session, native handshake, authenticated context, sequence, active state and freshness. Stop/restart confirmations are one-use and expire; hiding/cancel invalidates unused capabilities. Read-only fallback data cannot authorize commands. WSL and DevStack use separate page origins and handlers.

The Ubuntu resource endpoint is read-only. DevStack service reads and controls use separate tokens and loopback sockets. The reader is a dedicated non-login account with filesystem/process restrictions. The control helper is root but accepts only four fixed unit names and one bounded restart operation. Host/Origin/framing checks, request-ID consumption and locking restrict unauthenticated, browser-origin, duplicate and concurrent requests. No arbitrary shell or WSL lifecycle endpoint is exposed.

A confirmed request may finish after the panel closes. An ambiguous timeout is not automatically retried. Later service availability does not prove an unconfirmed command executed. No passive widget read starts a stopped distribution.

`%LOCALAPPDATA%\DevStack\Widgets\telemetry.json` and `services.json` contain access credentials. Linux counterparts are under `/etc/devstack-widgets`. Do not print, commit or attach them. Windows diagnostic logs intentionally omit tokens and raw message bodies, but all logs/screenshots should still be reviewed before publication. Full project data used locally by the reader is never returned to the widget; only the count is returned.

## Passwordless mode

The deliberate command `sudo php /opt/devstack/scripts/configure-auth.php --no-password --i-understand-local-risk` disables the dashboard's login/reauthentication gate. **Anyone able to connect locally can then administer DevStack and reveal credentials.** It is unsuitable for untrusted programs, shared user sessions or network exposure. Restore password mode with `sudo php /opt/devstack/scripts/configure-auth.php --password`. The web UI cannot change this mode itself.

## Limits

1. Trusted developer-owned projects can read one another's files. Do not run untrusted PHP code.
2. Local database/widget credentials are recoverable secrets at rest. Protect the disk and backups; a localhost address is not authentication.
3. The root helpers and elevated hosts task are privileged attack surfaces despite allowlists and sandboxing. No independent security audit is claimed.
4. Trusting a local CA grants certificate-signing trust. Never publish its private key or copy it into repository artifacts.
5. Code rollback is best-effort; it does not restore databases or all system configuration. Keep verified SQL and WSL backups. Automatic certificate renewal and scheduled backups are not implemented.
6. CI and functional device acceptance do not prove safety across every Windows/WSL build, battery/sleep state or untrusted workload. Test isolated environments before deploying changes to important workstations.

## Reporting

Use a private security report/GitHub advisory for suspected vulnerabilities. Do not put live credentials, exploit details, CA keys, project registries, `.env` files, database dumps or WSL exports in a public issue. Public examples should use fictional placeholders rather than real project or workstation data.
