"""Unit tests with realistic MIME and a loopback-only HTTP fixture."""

from contextlib import redirect_stderr, redirect_stdout
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from threading import Thread
from time import monotonic, sleep
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'mail'))

import cli
from contract import MailError, Query, address_key, parse_timestamp
import mailpit
from message import extract_links, parse_message, parse_origin

ID = 'A' * 22
RECEIVED = '2026-11-28T08:00:00.123Z'
LIST_PATH = f"/api/v1/messages?limit={mailpit.MAX_MESSAGES + 1}"
SIGNED = 'https://shop.test/verify/email/42/abc?expires=1799999999&signature=a%2Bb%2f%3D&return=%2Fpanel%3Fa%3D1%26b%3D2'


def item(id=ID, *, to='test@shop.test', cc=None, bcc=None, subject='Verify', created=RECEIVED):
    return {
        'ID': id, 'Created': created, 'Subject': subject,
        'From': {'Name': 'Developer', 'Address': 'dev@shop.test'},
        'To': [{'Name': 'test@shop.test', 'Address': to}],
        'Cc': [{'Name': '', 'Address': cc}] if cc else [],
        'Bcc': [{'Name': '', 'Address': bcc}] if bcc else None,
        'Snippet': 'not part of the public contract',
    }


def mime():
    message = EmailMessage()
    message['From'] = 'Łukasz <dev@shop.test>'
    message['To'] = 'Group: First <test@shop.test>, Other <other@shop.test>;'
    message['Cc'] = 'cc@shop.test'
    message['Bcc'] = 'bcc@shop.test'
    message['Subject'] = 'Potwierdź adres'
    message.set_content(f'Kliknij {SIGNED}', charset='iso-8859-2', cte='quoted-printable')
    escaped = SIGNED.replace('&', '&amp;')
    message.add_alternative(f"""
        <base href="https://wrong.test/"><a href="/relative">relative</a>
        <a href="{escaped}">Verify</a>
        <a href="https://SHOP.test:443/download?x=%2f">same origin</a>
        <a href="https://shop.test.evil.test/">suffix</a>
        <a href="https://other.test/?redirect=https://shop.test">other host</a>
        <a href="https://shop.test:8443/">other port</a>
        <a href="http://shop.test/">other scheme</a>
        <a href="https://shop.test@evil.test/">credentials</a>
        <a href="javascript:alert(1)">script</a>
        <img src="https://shop.test/tracker"><script>location = 'https://shop.test/script'</script>
    """, subtype='html', cte='quoted-printable')
    message.add_attachment('https://shop.test/attached', filename='secret.txt')
    return message.as_bytes()


class ParserTests(unittest.TestCase):
    def test_mime_and_signed_links(self):
        message = parse_message(mime())
        self.assertEqual(message.envelope.subject, 'Potwierdź adres')
        self.assertEqual(message.envelope.sender.name, 'Łukasz')
        self.assertEqual([item.address for item in message.envelope.to],
                         ['test@shop.test', 'other@shop.test'])
        self.assertEqual(message.envelope.cc[0].address, 'cc@shop.test')
        self.assertEqual(message.envelope.bcc[0].address, 'bcc@shop.test')
        self.assertIn(SIGNED, message.text)
        self.assertIn('&amp;', message.html)
        self.assertEqual(extract_links(message, parse_origin('https://shop.test/')),
                         [SIGNED, 'https://SHOP.test:443/download?x=%2f'])
        links = extract_links(message)
        self.assertIn('https://shop.test.evil.test/', links)
        self.assertNotIn('https://shop.test/tracker', links)
        self.assertNotIn('https://shop.test/script', links)
        self.assertNotIn('https://shop.test/attached', links)
        self.assertNotIn('https://shop.test@evil.test/', links)
        self.assertEqual(links.count(SIGNED), 1)

    def test_html_only_and_plain_only(self):
        plain = parse_message(b'Subject: hi\r\n\r\nhttps://shop.test/without-rewrite?x=%2f')
        self.assertIsNone(plain.html)
        self.assertEqual(extract_links(plain), ['https://shop.test/without-rewrite?x=%2f'])
        html = parse_message(b'Subject: test\r\nContent-Type: text/html\r\n\r\n<a href="https://shop.test/">go</a>')
        self.assertIsNone(html.text)
        self.assertEqual(extract_links(html), ['https://shop.test/'])

    def test_origin_validation(self):
        self.assertEqual(parse_origin('HTTP://[0:0::1]:8080'), ('http', '::1', 8080))
        for value in ('file:///tmp', '//shop.test', 'https://shop.test/abc', 'https://shop.test?',
                      'https://shop.test#', 'https://user@shop.test', 'https://shop.test:',
                      'https://shop.test:0', 'https://shop.test:65536', 'https://shop%2Etest',
                      'http://127.1', 'http://[::1]foo', 'https://shop.test\t',
                      'https://evil.test\\@shop.test'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_origin(value)

    def test_invalid_mime_and_link_limit(self):
        with self.assertRaisesRegex(MailError, 'invalid response'):
            parse_message(b'No MIME headers')
        content = parse_message(b'Subject: hi\n\n' + b'\n'.join(
            f'https://shop.test/{n}'.encode() for n in range(257)))
        with self.assertRaises(MailError) as error:
            extract_links(content)
        self.assertEqual(error.exception.code, 'limit_exceeded')


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.paths = []
        body = json.dumps({'messages': [item()]}).encode()
        self.responses = {LIST_PATH: (200, 'application/json', body),
                          f'/api/v1/message/{ID}/raw': (200, 'text/plain', mime()),
                          '/readyz': (200, None, b'')}
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass

            def do_GET(self):
                fixture.paths.append(self.path)
                entry = fixture.responses.get(self.path, (404, None, b''))
                if callable(entry):
                    entry(self)
                    return
                status, media, data = entry
                self.send_response(status)
                if media:
                    self.send_header('Content-Type', media)
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.timeout = 0.1
        self.thread = Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        port = patch.object(mailpit, 'PORT', self.server.server_port)
        port.start()
        self.addCleanup(port.stop)
        installed = patch.object(mailpit, '_check_installation')
        installed.start()
        self.addCleanup(installed.stop)
        self.adapter = mailpit.Mailpit()
        self.query = Query(address_key('test@SHOP.test'), 'Verify', None)

    def deadline(self):
        return monotonic() + 1

    def use_messages(self, messages):
        self.responses[LIST_PATH] = (
            200, 'application/json', json.dumps({'messages': messages}).encode())

    def test_read_only_paths_and_ignored_environment_proxy(self):
        with patch.dict(os.environ, {'http_proxy': 'http://127.0.0.1:1',
                                     'HTTP_PROXY': 'http://127.0.0.1:1', 'NO_PROXY': ''}):
            self.adapter.status(self.deadline())
            results = self.adapter.search(self.query, self.deadline())
            self.assertEqual([entry.id for entry in results], [ID])
            self.assertNotIn('Snippet', results[0].to_json())
            self.assertEqual(self.adapter.get(ID, self.deadline()).envelope.subject, 'Potwierdź adres')
        self.assertEqual(self.paths, ['/readyz', LIST_PATH,
                                     f'/api/v1/message/{ID}/raw'])

    def test_exact_recipients_subject_timestamp_and_order(self):
        self.use_messages([
            item('B' * 22, to='test@shop.test.evil'),
            item('C' * 22, to='TEST@shop.test'),
            item('D' * 22, to='test+alias@shop.test'),
            item('E' * 22, to='not-test@shop.test'),
            item('F' * 22, subject='verify'),
            item('G' * 22, created='2026-11-28T08:00:00.122Z'),
            item('H' * 22, to='other@shop.test', cc='test@SHOP.test'),
            item('I' * 22, to='other@shop.test', bcc='test@shop.test'),
            item('J' * 22, created='2026-11-28T08:00:00.124Z'),
            item(),
        ])
        since = parse_timestamp('2026-11-28T09:00:00.122+01:00')
        result = self.adapter.search(Query(self.query.recipient, 'Verify', since), self.deadline())
        self.assertEqual([entry.id[0] for entry in result], ['J', 'I', 'H', 'A'])
        self.assertEqual(result[-1].to_json()['received_at'], RECEIVED)

    def test_invalid_payload_and_count_limit(self):
        for raw in (b'{"messages":null}', b'[]', b'not JSON',
                    json.dumps({'messages': [item(created='wrong')]}).encode(),
                    json.dumps({'messages': [item(), item()]}).encode()):
            path = LIST_PATH
            self.responses[path] = (200, 'application/json', raw)
            with self.subTest(raw=raw[:24]), self.assertRaises(MailError) as error:
                self.adapter.search(self.query, self.deadline())
            self.assertEqual(error.exception.code, 'invalid_response')
        self.use_messages([item()] * (mailpit.MAX_MESSAGES + 1))
        with self.assertRaises(MailError) as error:
            self.adapter.search(self.query, self.deadline())
        self.assertEqual(error.exception.code, 'limit_exceeded')

    def test_redirect_and_unrecognized_service_response(self):
        def redirect(handler):
            handler.send_response(302)
            handler.send_header('Location', '/fake')
            handler.end_headers()
        self.responses['/readyz'] = redirect
        with self.assertRaises(MailError) as error:
            self.adapter.status(self.deadline())
        self.assertEqual(error.exception.code, 'invalid_response')
        self.assertEqual(self.paths, ['/readyz'])
        self.responses['/readyz'] = (200, 'text/html', b'wrong service')
        with self.assertRaises(MailError) as error:
            self.adapter.status(self.deadline())
        self.assertEqual(error.exception.code, 'invalid_response')

    def test_limits_and_deadline_with_slow_response(self):
        self.responses['/readyz'] = (200, 'text/plain', b'x' * 17)
        with self.assertRaises(MailError) as error:
            self.adapter.status(self.deadline())
        self.assertEqual(error.exception.code, 'limit_exceeded')

        def slow(handler):
            handler.send_response(200)
            handler.end_headers()
            try:
                for part in range(25):
                    handler.wfile.write(b'a')
                    sleep(0.025)
            except OSError:
                pass
        self.responses['/readyz'] = slow
        start = monotonic()
        with self.assertRaises(MailError) as error:
            self.adapter.status(start + 0.12)
        self.assertEqual(error.exception.code, 'unavailable')
        self.assertLess(monotonic() - start, 0.35)

    def test_missing_message_unavailable_and_rejected_id(self):
        with self.assertRaises(MailError) as error:
            self.adapter.get('latest', self.deadline())
        self.assertEqual(error.exception.code, 'invalid_arguments')
        self.assertEqual(self.paths, [])
        self.responses.pop(f'/api/v1/message/{ID}/raw')
        with self.assertRaises(MailError) as error:
            self.adapter.get(ID, self.deadline())
        self.assertEqual(error.exception.code, 'not_found')
        self.responses['/readyz'] = (503, 'text/plain', b'backend detail')
        with self.assertRaises(MailError) as error:
            self.adapter.status(self.deadline())
        self.assertEqual(error.exception.code, 'unavailable')
        self.assertNotIn('backend detail', str(error.exception))


class CLITests(unittest.TestCase):
    def invoke(self, *argv, adapter=None):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors), patch.object(cli.os, 'geteuid', return_value=1000):
            code = cli.main(argv, adapter_factory=lambda: adapter)
        self.assertEqual(errors.getvalue(), '')
        return code, json.loads(output.getvalue())

    def test_status_search_and_get_contract(self):
        adapter = Mock()
        adapter.search.return_value = []
        adapter.get.return_value = parse_message(mime())
        self.assertEqual(self.invoke('--json', 'status', adapter=adapter),
                         (0, {'schema_version': 1, 'operation': 'status', 'ok': True, 'status': 'ready'}))
        self.assertEqual(self.invoke('search', '--to', 'demo@shop.test', adapter=adapter),
                         (0, {'schema_version': 1, 'operation': 'search', 'ok': True, 'messages': []}))
        code, result = self.invoke('get', ID, '--origin', 'https://shop.test', '--json', adapter=adapter)
        self.assertEqual(code, 0)
        self.assertEqual(set(result['message']), {'id', 'subject', 'sender', 'to', 'cc', 'bcc',
                                                  'text', 'html', 'links'})
        self.assertEqual(result['message']['links'][0], SIGNED)

    def test_bad_arguments_and_rfc3339(self):
        for argv in (('search', '--to', 'User <demo@shop.test>'), ('wait', '--to', 'demo@shop.test', '--timeout', '0'),
                     ('wait', '--to', 'demo@shop.test', '--timeout', '121'),
                     ('wait', '--to', 'demo@shop.test', '--timeout', 'inf'),
                     ('search', '--to', 'demo@shop.test', '--since', '2026-11-28'),
                     ('search', '--to', 'demo@shop.test', '--since', '2026-11-28T07:00:00+00:99'),
                     ('get', '../untrusted'), ('get', ID, '--origin', 'https://shop.test/x'),
                     ('status', '--raw-value-from-user')):
            with self.subTest(argv=argv):
                rc, result = self.invoke(*argv)
                self.assertEqual(rc, 2)
                self.assertEqual(result['error']['code'], 'invalid_arguments')
                self.assertNotIn('raw-value-from-user', str(result))

    def test_wait_timeout_and_retry_after_empty_result(self):
        adapter = Mock()
        adapter.search.return_value = []
        rc, payload = self.invoke('wait', '--to', 'demo@shop.test', '--timeout', '1', adapter=adapter)
        self.assertEqual(rc, 4)
        self.assertEqual(payload['error']['code'], 'timeout')
        self.assertGreaterEqual(adapter.search.call_count, 2)
        adapter.search.side_effect = [[], [mailpit._summary(item())]]
        with patch.object(cli, 'POLL_INTERVAL', 0):
            rc, payload = self.invoke('wait', '--to', 'demo@shop.test', adapter=adapter)
        self.assertEqual(rc, 0)
        self.assertEqual(payload['message']['id'], ID)

    def test_command_distinguishes_missing_service_and_mail(self):
        adapter = Mock()
        adapter.status.side_effect = MailError('unavailable')
        self.assertEqual(self.invoke('status', adapter=adapter)[0], 5)
        adapter.get.side_effect = MailError('not_found')
        self.assertEqual(self.invoke('get', ID, adapter=adapter)[0], 3)
        with tempfile.TemporaryDirectory() as directory, patch.object(
                mailpit, 'VERSION_FILE', Path(directory) / 'missing'):
            with self.assertRaises(MailError) as error:
                mailpit.Mailpit()
        self.assertEqual(error.exception.code, 'not_installed')


if __name__ == '__main__':
    unittest.main()
