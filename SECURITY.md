# Security policy

**DevStack is a localhost-only development convenience tool, not a public control panel, production hosting system, or substitute for container isolation.** It deliberately runs a small privileged helper: security defects in that boundary can affect the host Linux distribution. Treat code changes as security-sensitive.

## Safeguards

- Nginx listens only on WSL loopback. Windows localhost forwarding is still available; host firewall/network policy must remain enabled.
- Authentication via Argon2id hash, secure HttpOnly SameSite Strict session cookies, 8-hour inactivity cutoff, CSRF token, origin checking and a 15-minute login throttle.
- PHP-FPM web process under a dedicated `devstackweb` user with no login shell, separate from app PHP pools. Web process cannot directly write Nginx, databases, secret store, certificate keys or service units.
- Root helper uses `SO_PEERCRED` UID verification and a fixed typed operation allowlist. No user-chosen command, Unix file path or raw Nginx directives are executed. Does not read environment-provided secrets into logs.
- Generated Nginx site binds `127.0.0.1` and executes only `index.php`, denying other PHP endpoints; dotfiles denied.
- MariaDB user has permissions only on its generated database. Credentials live under root-only `/var/lib/devstack/` and are never included in API status responses; revealing requires password reauthentication.
- mkcert CA private key is not copied to Windows or published. The Windows trust script requires explicit confirmation before importing the public root certificate.
- Systemd helper has additional sandboxing (restricted address families, private temp and devices, kernel tunable protections), but **it is still a root process**.
- CI uses `permissions: contents: read`, public GitHub-hosted runners, and no repository secrets or self-hosted agents.

## Known risk and safe-use boundaries

1. Per-project PHP-FPM pools run as the same local development user. Code from one project can potentially read another developer-owned project. Do not run untrusted PHP code.
2. Database passwords are root-only but recoverable plaintext at rest. Protect the virtual disk, Windows session and all backups; these are local development credentials only.
3. Webroot paths are within a developer-owned directory; filesystem races may exist if another untrusted local process can mutate it. DevStack is not an isolation boundary against a malicious local developer account. All project directory creation runs as the developer user, not root.
4. Trusting a new local CA enables it to sign certificates Windows trusts. Protect the CA key and verify the public CA fingerprint before import. Use no public domain.
5. Automatic certificate renewal, database backups, privilege separation per individual project, full server-config editor, recovery from abrupt disk failures, and audited upgrade/migration are future work.
6. A successful GitHub build does **not** prove the app is safe to run on the target WSL machine. Run isolated smoke tests and keep a WSL export before first deployment.

## Reporting

Please open a GitHub Security Advisory/private report for suspected vulnerabilities. Avoid publishing exploit details or live credentials in a public issue. Never submit real CA keys, `.env` files, WSL snapshots, database dumps or screenshots containing secrets.
