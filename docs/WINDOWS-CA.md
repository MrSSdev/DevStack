# DevStack local CA on Windows

DevStack uses `mkcert` to issue locally trusted HTTPS certificates for `*.test` development domains. The **root CA certificate is public**; its **private signing key must remain inside Ubuntu**.

## Recover or install trust from the dashboard

When `https://devstack.test` is accessible:

1. Open **Ustawienia → Certyfikat CA w Windows**.
2. Click **Pobierz publiczny certyfikat CA (.cer)**. The file is served by an authenticated, loopback-only DevStack endpoint and is named `devstack-rootCA.cer`.
3. Compare the SHA-256 of the downloaded file with the hash displayed in DevStack settings:

   ~~~powershell
   Get-FileHash -Algorithm SHA256 -LiteralPath "$env:USERPROFILE\Downloads\devstack-rootCA.cer"
   ~~~

4. **For first-time trust, verify it against the CA file in your own Ubuntu instance through a separate trusted local terminal**:

   ~~~powershell
   wsl -d Ubuntu-26.04 -u root -- sha256sum /var/lib/devstack/ca/rootCA.pem
   ~~~

5. In Windows press **Win+R**, run `certmgr.msc`, and open **Zaufane główne urzędy certyfikacji → Certyfikaty**. Choose import, select `devstack-rootCA.cer`, explicitly select **Trusted Root Certification Authorities**, inspect the presented certificate, and approve only if it is your CA. The `CurrentUser` store covers the logged-in account; a machine-wide import through `certlm.msc` / elevated PowerShell requires administrator privileges.
6. Restart the browser if HTTPS trust has not updated automatically.

Windows PowerShell also supports `Import-Certificate` for an explicitly chosen store, but **do not run imports before verifying the fingerprint and deciding that you trust this CA**.

## When the browser refuses to open the dashboard

A missing trusted CA can block access to `https://devstack.test` **before** the dashboard can offer a download. On a fresh PC use the normal one-time Windows setup from [INSTALL-WINDOWS-WSL.md](INSTALL-WINDOWS-WSL.md): `windows/setup-windows.ps1`. It prints the fingerprint and asks for explicit approval before trusting the local CA.

For an existing installation that only lost trust, the manually reviewed `windows/sync-hosts.ps1` can reinstall the public CA but **also synchronizes Windows hosts**. Avoid rerunning the first-install-only Linux setup; it could endanger the local CA and project state.

## Security boundaries

- A web page **cannot silently install** a Windows trusted root CA. The final trust decision always belongs to the Windows user/administrator.
- The API returns only `/var/lib/devstack/ca/rootCA.pem`, not `rootCA-key.pem`, arbitrary paths or private keys. It requires an authenticated local DevStack session.
- Neither downloading the file nor viewing DevStack settings changes the Windows trust store.
- The dashboard **cannot determine the Windows certificate store's actual trust status**; the button is offered regardless of current trust.
- If the local CA was **regenerated**, remove trust in the *old* CA only after comparing its identity and confirming which certificates depend on it.
- Browser access while a certificate is untrusted cannot on its own establish CA authenticity. Verify the hash against the locally trusted Ubuntu copy before importing it.

Microsoft references:
- https://learn.microsoft.com/en-us/powershell/module/pki/import-certificate
- https://learn.microsoft.com/en-us/windows-server/identity/ad-cs/inspect-certificates-manage-certificate-stores
