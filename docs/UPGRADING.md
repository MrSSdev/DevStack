# Updating an existing DevStack installation

These instructions are for a **previously installed** DevStack on Ubuntu 26.04 under WSL2. This is a manually verified workflow, **not yet an automated, transaction-safe upgrade installer**.

**Never run the first-install-only bootstrap or scripts/install.sh over an existing system.** Preserve copies of your WSL distribution and important MariaDB data before updating. Review repository changes before executing privileged commands.

## 1. Validate and build as your ordinary Ubuntu account

~~~bash
cd ~/projects/devstack-source
git status --short
git pull --ff-only

python3 -m unittest discover -s tests -v
php tests/php-security.php
bash scripts/build-frontend.sh
~~~

Stop if you have unexpected local changes, any test fails or the frontend build fails.

## 2. Update the installed application

The commands below do not change your projects, MariaDB credentials, local CA or Windows trust store.

~~~bash
sudo install -m 0644 -o root -g root daemon/devstackd.py /opt/devstack/daemon/devstackd.py
sudo install -m 0644 -o root -g root backend/security.php /opt/devstack/security.php
sudo install -m 0644 -o root -g root backend/public/index.php /opt/devstack/public/index.php
sudo install -m 0644 -o root -g root scripts/list-domains.py /opt/devstack/scripts/list-domains.py
sudo install -m 0644 -o root -g root scripts/watch-domains.py /opt/devstack/scripts/watch-domains.py
sudo install -m 0644 -o root -g root scripts/configure-auth.php /opt/devstack/scripts/configure-auth.php
sudo install -m 0644 -o root -g root scripts/set-password.php /opt/devstack/scripts/set-password.php
sudo cp -a backend/public/index.html backend/public/assets /opt/devstack/public/
sudo chown -R root:root /opt/devstack/public

sudo php-fpm8.5 -t
sudo nginx -t
sudo systemctl restart devstackd
sudo systemctl reload php8.5-fpm
systemctl is-active devstackd nginx php8.5-fpm mariadb redis-server
~~~

Refresh https://devstack.test with Ctrl+F5. Changes affecting Windows integration might require explicitly updating the installed scripts using the Windows integration section of INSTALL-WINDOWS-WSL.md; do not silently reinstall certificate trust.

## 3. Basic smoke checks

~~~bash
curl -ksS --noproxy '*' --resolve devstack.test:443:127.0.0.1 https://devstack.test/api/session
sudo journalctl -u devstackd -n 40 --no-pager
~~~

The curl -k form is for a local pre-trust diagnostic only. Use normal certificate verification in real applications.

A successful build or CI test is not equivalent to a working installation. Validate project creation, the database connection check, Nginx overrides, and the Windows hosts synchronization against a **disposable** project.

## Rollback

There is **no automated rollback** yet. Before changing installed files or service units, make an independent backup of the previous version and of important state. Restore affected application files from that copy if validation fails, then rerun Nginx/PHP syntax checks and inspect service logs. Do not restore old database state over live project data without a verified restoration procedure.

Reference:
- [Installation](INSTALL-WINDOWS-WSL.md)
- [Troubleshooting](TROUBLESHOOTING.md)
- [Security](../SECURITY.md)
