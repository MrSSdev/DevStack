# Local HTTPS certificate trust on Windows

DevStack uses mkcert for local HTTPS. Trust the public root CA certificate from your own Ubuntu instance and keep its private signing key inside Ubuntu.

For initial setup, use [Windows integration](INSTALL-WINDOWS-WSL.md#3-windows-integration).

## Import from the dashboard

1. Open **Ustawienia → Certyfikat CA w Windows** and download the public certificate `devstack-rootCA.cer`.
2. Compare its SHA-256 with the dashboard fingerprint and the local Ubuntu CA:

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath "$env:USERPROFILE\Downloads\devstack-rootCA.cer"
wsl -d Ubuntu-26.04 -u root -- sha256sum /var/lib/devstack/ca/rootCA.pem
```

3. Press **Win + R**, run `certmgr.msc` and open **Trusted Root Certification Authorities → Certificates**.
4. Import the verified public certificate into that store. For machine-wide trust, use `certlm.msc` with administrator access.
5. Reopen the browser and visit https://devstack.test.

If you need to establish trust before opening the dashboard, run the reviewed `windows/setup-windows.ps1` procedure in the [installation guide](INSTALL-WINDOWS-WSL.md).

The MSIX code-signing certificate belongs in TrustedPeople and is handled separately by the widget installer.

See [security](../SECURITY.md).
