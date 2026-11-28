"""Read-only CLI acceptance against a disposable, checksum-pinned Mailpit."""

from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime
import json
import os
from pathlib import Path
import secrets
import smtplib
import subprocess
import sys
from time import monotonic, sleep
from urllib.request import ProxyHandler, build_opener

CLI = Path(__file__).resolve().parents[1] / 'mail/cli.py'
COMMAND = [sys.executable, '-IB', str(CLI)]
API = build_opener(ProxyHandler({}))
TOKEN = secrets.token_hex(8)
A = f'alpha-{TOKEN}@example.test'
B = f'beta-{TOKEN}@example.test'
C = f'blind-{TOKEN}@example.test'
SUBJECT = f'Verify {TOKEN}'
SIGNED = 'https://shop.test/verify/email/42/abc?expires=1799999999&signature=ab%2Bc%2f%3D&return=%2Fhome%3Fx%3D1%26y%3D2'


def cli(*args, code=0):
    result = subprocess.run([*COMMAND, *args], capture_output=True, text=True, timeout=15)
    assert result.returncode == code and not result.stderr, (args, result.returncode, result.stderr)
    report = json.loads(result.stdout)
    assert report['schema_version'] == 1 and report['ok'] == (code == 0), report
    return report


def send(recipient, subject, *, blind=False):
    message = EmailMessage()
    message['From'] = 'Developer <dev@shop.test>'
    message['To'] = 'other@example.test' if blind else recipient
    message['Subject'] = subject
    # The sender's Date must not affect the receive-time filter.
    message['Date'] = format_datetime(datetime(1999, 1, 1, tzinfo=timezone.utc))
    message.set_content(f'Use this signed link {SIGNED}', cte='quoted-printable')
    escaped = SIGNED.replace('&', '&amp;')
    message.add_alternative(
        f'<a href="{escaped}">Verify</a><a href="https://shop.test.evil.test/">Other</a>',
        subtype='html', cte='quoted-printable')
    with smtplib.SMTP('127.0.0.1', 1025, timeout=4) as smtp:
        smtp.send_message(message, to_addrs=[recipient])


def read_flags():
    with API.open('http://127.0.0.1:8025/api/v1/messages?limit=1000', timeout=4) as response:
        messages = json.load(response)['messages']
    return {item['ID']: item['Read'] for item in messages if TOKEN in item['Subject']}


def start_wait(recipient, since):
    return subprocess.Popen([*COMMAND, 'wait', '--to', recipient, '--since', since,
                             '--subject', SUBJECT, '--timeout', '8', '--json'],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def finish_wait(process):
    output, errors = process.communicate(timeout=12)
    assert process.returncode == 0 and not errors, (process.returncode, errors)
    return json.loads(output)['message']


def exercise():
    assert cli('status')['status'] == 'ready'
    send(A, SUBJECT)
    sleep(0.03)
    since = datetime.now(timezone.utc).isoformat()
    first = start_wait(A, since)
    second = start_wait(B, since)
    try:
        send(f'prefix-{A}', SUBJECT)
        send(A, f'{SUBJECT} extra')
        send(B, SUBJECT)
        send(A, SUBJECT)
        alpha, beta = finish_wait(first), finish_wait(second)
    finally:
        for process in (first, second):
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=3)
    assert alpha['id'] != beta['id']
    assert alpha['to'][0]['address'] == A and beta['to'][0]['address'] == B
    sleep(0.03)
    send(A, SUBJECT)
    send(C, f'{SUBJECT} blind', blind=True)
    before = read_flags()
    assert len(before) == 7 and all(flag is False for flag in before.values()), before

    result = cli('search', '--to', A, '--subject', SUBJECT, '--since', since)['messages']
    assert len(result) == 2 and result[1]['id'] == alpha['id'], result
    all_alpha = cli('search', '--to', A, '--subject', SUBJECT)['messages']
    assert len(all_alpha) == 3
    blind = cli('search', '--to', C)['messages']
    assert len(blind) == 1 and blind[0]['bcc'][0]['address'] == C
    content = cli('get', result[0]['id'], '--origin', 'https://shop.test')['message']
    assert content['subject'] == SUBJECT and content['sender']['address'] == 'dev@shop.test'
    assert content['to'][0]['address'] == A
    assert SIGNED in content['text'] and '&amp;' in content['html'] and content['links'] == [SIGNED]
    assert cli('get', blind[0]['id'])['message']['bcc'][0]['address'] == C
    assert cli('search', '--to', f'unknown-{TOKEN}@example.test')['messages'] == []
    begin = monotonic()
    assert cli('wait', '--to', A, '--subject', f'Absent {TOKEN}', '--timeout', '1',
               code=4)['error']['code'] == 'timeout'
    assert 0.9 <= monotonic() - begin < 4
    assert cli('get', '0' * 22, code=3)['error']['code'] == 'not_found'
    assert read_flags() == before
    print('PASS: pinned Mailpit, parallel wait, exact recipient and receive time, MIME, signed links and unchanged read state.')


if __name__ == '__main__':
    if os.environ.get('DEVSTACK_MAIL_TEST_DISPOSABLE') != '1' or os.geteuid() == 0:
        raise SystemExit('Run only as an unprivileged developer against a disposable Mailpit.')
    if sys.argv[1:] == ['missing']:
        assert cli('status', code=5)['error']['code'] == 'not_installed'
    elif sys.argv[1:] == ['stopped']:
        assert cli('status', code=5)['error']['code'] == 'unavailable'
    elif sys.argv[1:] == ['ready']:
        exercise()
    else:
        raise SystemExit('Expected missing, ready or stopped')
