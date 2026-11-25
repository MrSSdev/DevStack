# Security

DevStack is intended for trusted local development. Keep the dashboard on loopback, use password authentication and protect your Windows account, Ubuntu files and backups.

## Dashboard and services

The dashboard uses authenticated sessions, CSRF/origin checks and a restricted local privileged helper. Per-project PHP-FPM pools run as the developer account. Use trusted project code and keep database credentials and environment files private.

Choose password authentication during installation. Passwordless mode grants administration access to local clients that can reach the dashboard. To configure it in Ubuntu:

```bash
sudo php /opt/devstack/scripts/configure-auth.php --password
```

## Windows integration and widgets

Review privileged installation scripts. Verify the public mkcert CA fingerprint before importing it into Windows. Keep the private CA key inside Ubuntu. Install the widget MSIX using its matching public code-signing certificate and installer from the same trusted build.

Widget resource and control interfaces use authenticated loopback endpoints. Service restart and Ubuntu stop require explicit confirmation. Windows hosts synchronization is restricted to the managed block.

Protect `%LOCALAPPDATA%\DevStack\Widgets\telemetry.json` and `services.json`, as well as Linux credentials under `/etc/devstack-widgets`. Keep tokens and configuration files out of issue reports.

## Backups and reporting

Keep [verified backups](docs/BACKUP-RESTORE.md) in secure storage. Report suspected vulnerabilities privately through GitHub security reporting or contact the maintainer privately. Sanitize logs and avoid publishing credentials, private keys, project data or database exports.
