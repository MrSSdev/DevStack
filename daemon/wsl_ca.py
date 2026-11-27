"""Import one validated public CA into the invoking developer's NSS database.

This worker is always unprivileged. The privileged caller supplies the fixed
DevStack CA over stdin; neither a web request nor a project selects the PEM,
account, database path, command or trust flags.
"""
import os
import pathlib
import pwd
import re
import ssl
import subprocess
import sys
import tempfile

NICKNAME = 'DevStack Local CA'
PEM_PATTERN = re.compile(
    rb'-----BEGIN CERTIFICATE-----\r?\n(?:[A-Za-z0-9+/=]{1,76}\r?\n)+'
    rb'-----END CERTIFICATE-----\r?\n?'
)


def command(*args, check=True):
    try:
        result = subprocess.run(
            args, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            env={'PATH': '/usr/bin:/bin', 'HOME': os.environ['HOME'], 'LC_ALL': 'C'},
            timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError('Nie mozna wykonac operacji magazynu certyfikatow NSS.') from exc
    if check and result.returncode:
        # Never pass PEM, a filename chosen by NSS, or an arbitrary command
        # through a privileged API error.
        raise RuntimeError('Operacja NSS nie powiodla sie. Sprawdz uprawnienia lub haslo magazynu certyfikatow.')
    return result


def import_ca(pem: bytes, home: pathlib.Path) -> str:
    if not 100 <= len(pem) <= 16384 or not PEM_PATTERN.fullmatch(pem):
        raise RuntimeError('Nieprawidlowy publiczny certyfikat CA DevStack.')
    der = ssl.PEM_cert_to_DER_cert(pem.decode('ascii'))
    with tempfile.TemporaryDirectory(prefix='devstack-ca-') as work:
        cert = pathlib.Path(work) / 'ca.pem'
        cert.write_bytes(pem)
        constraints = command('/usr/bin/openssl', 'x509', '-in', str(cert),
                              '-noout', '-ext', 'basicConstraints', '-checkend', '0')
        if 'CA:TRUE' not in constraints.stdout:
            raise RuntimeError('Certyfikat nie jest waznym CA.')
        command('/usr/bin/openssl', 'verify', '-check_ss_sig', '-CAfile', str(cert), str(cert))
        # Chromium 146+ uses the new directory, but continues to use the
        # existing legacy NSS database when present.
        legacy = home / '.pki/nssdb'
        db = legacy if legacy.is_dir() else home / '.local/share/pki/nssdb'
        db.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = 'sql:' + str(db)
        if not (db / 'cert9.db').exists():
            if (db / 'key4.db').exists() or (db / 'cert8.db').exists():
                raise RuntimeError('Niekompletny lub starszy magazyn NSS. Napraw go przed importem.')
            command('/usr/bin/certutil', '-N', '-d', target, '--empty-password')

        def entries():
            listing = command('/usr/bin/certutil', '-L', '-d', target).stdout
            return [(m.group(1).strip(), m.group(2)) for line in listing.splitlines()
                    if (m := re.fullmatch(r'(.+?)\s+([A-Za-z]*,[A-Za-z]*,[A-Za-z]*)\s*', line))]

        def certificate(name):
            result = command('/usr/bin/certutil', '-L', '-a', '-n', name, '-d', target)
            return ssl.PEM_cert_to_DER_cert(result.stdout.strip())

        # Refuse to overwrite a different certificate at our existing name.
        # NSS may keep the original nickname when the same cert was imported
        # by mkcert or a previous manual setup. Preserve it and its other trust
        # categories; don't delete or rename entries.
        current = entries()
        named = next((name for name, _ in current if name == NICKNAME), None)
        if named and certificate(named) != der:
            raise RuntimeError('Nazwa DevStack Local CA nalezy do innego certyfikatu. Sprawdz stary wpis NSS.')
        matching = next(((name, flags) for name, flags in current
                         if certificate(name) == der), None)
        if matching and 'C' in matching[1].split(',')[0]:
            return 'Certyfikat CA DevStack jest juz zaufany w WSL.'
        if matching:
            name, flags = matching
            ssl_flags, mail_flags, object_flags = flags.split(',')
            # Remove explicit distrust of this CA for SSL, preserve other
            # trust categories. Only this exact public CA is affected.
            ssl_flags = ssl_flags.replace('p', '')
            trust = ','.join((ssl_flags + 'C', mail_flags, object_flags))
            command('/usr/bin/certutil', '-M', '-d', target, '-n', name, '-t', trust)
        else:
            name = NICKNAME
            command('/usr/bin/certutil', '-A', '-a', '-d', target, '-n', name,
                    '-t', 'C,,', '-i', str(cert))
        confirmed = dict(entries()).get(name, '')
        if certificate(name) != der or 'C' not in confirmed.split(',')[0]:
            raise RuntimeError('Nie udalo sie potwierdzic zaufania do CA w NSS.')
        return 'Certyfikat CA DevStack jest zaufany w WSL.'


def main():
    if os.geteuid() == 0:
        sys.exit('NSS worker must run as the developer, never root.')
    home = pathlib.Path(pwd.getpwuid(os.geteuid()).pw_dir)
    if os.environ.get('HOME') != str(home):
        sys.exit('Invalid developer HOME.')
    os.umask(0o077)
    try:
        print(import_ca(sys.stdin.buffer.read(16385), home))
    except (RuntimeError, ValueError, OSError) as exc:
        sys.exit(str(exc))


if __name__ == '__main__':
    main()
