"""Public DevStack Mail CLI. Keep provider details in the mailbox adapter."""

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import sys
from time import monotonic, sleep

if __name__ == '__main__':
    # -I excludes the current project and PYTHONPATH from import resolution.
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from contract import Mailbox, MailError, Query, address_key, parse_timestamp
from mailpit import Mailpit
from message import extract_links, parse_origin

VERSION = '1.0.0'
SCHEMA_VERSION = 1
DEFAULT_TIMEOUT = 30
MAX_TIMEOUT = 120
POLL_INTERVAL = 0.5


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse may include caller data in its own diagnostics.
        raise MailError('invalid_arguments')


def _parser() -> Parser:
    parser = Parser(prog='devstack-mail', description='Read local developer mail. JSON output by default.',
                    allow_abbrev=False)
    parser.add_argument('--version', action='version', version=f'%(prog)s {VERSION}')
    parser.add_argument('--json', action='store_true', help='Request JSON output (the default).')
    commands = parser.add_subparsers(dest='operation', required=True)
    status = commands.add_parser('status', help='Check local mailbox readiness.', allow_abbrev=False)
    search = commands.add_parser('search', help='List matching message summaries.', allow_abbrev=False)
    wait = commands.add_parser('wait', help='Wait for the newest matching message.', allow_abbrev=False)
    get = commands.add_parser('get', help='Read a message and its links.', allow_abbrev=False)
    for command in (status, search, wait, get):
        command.add_argument('--json', action='store_true', help='Request JSON output (the default).')
    for command in (search, wait):
        command.add_argument('--to', required=True, metavar='ADDRESS', help='Exact To, Cc or Bcc address.')
        command.add_argument('--subject', help='Exact subject, including case.')
        command.add_argument('--since', metavar='RFC3339', help='Only messages received after this timestamp.')
    wait.add_argument('--timeout', default=str(DEFAULT_TIMEOUT), metavar='SECONDS',
                      help=f'Wait 1 to {MAX_TIMEOUT} seconds (default: {DEFAULT_TIMEOUT}).')
    get.add_argument('id', help='Message ID returned by search or wait.')
    get.add_argument('--origin', help='Return only links with this HTTP(S) origin.')
    return parser


def _query(args: argparse.Namespace) -> Query:
    recipient = address_key(args.to)
    if recipient is None:
        raise MailError('invalid_arguments', 'Supply one bare email address with --to.')
    if args.subject is not None and (len(args.subject) > 998
                                      or re.search(r'[\x00-\x1f\x7f]', args.subject)):
        raise MailError('invalid_arguments', 'Invalid --subject value.')
    try:
        since = parse_timestamp(args.since) if args.since is not None else None
    except (ValueError, OverflowError):
        raise MailError('invalid_arguments', '--since requires an RFC 3339 timestamp with an offset.') from None
    return Query(recipient, args.subject, since)


def _wait(mailbox: Mailbox, query: Query, deadline: float) -> dict:
    while monotonic() < deadline:
        messages = mailbox.search(query, deadline)
        if messages:
            return {'message': messages[0].to_json()}
        sleep(min(POLL_INTERVAL, max(0, deadline - monotonic())))
    raise MailError('timeout')


def _execute(args: argparse.Namespace, adapter_factory) -> dict:
    if args.operation in ('search', 'wait'):
        query = _query(args)
        if args.operation == 'wait':
            if (not re.fullmatch(r'[0-9]{1,3}', args.timeout)
                    or not 1 <= int(args.timeout) <= MAX_TIMEOUT):
                raise MailError('invalid_arguments', f'--timeout must be an integer from 1 to {MAX_TIMEOUT}.')
            return _wait(adapter_factory(), query, monotonic() + int(args.timeout))
        messages = adapter_factory().search(query, monotonic() + 5)
        return {'messages': [message.to_json() for message in messages]}
    if args.operation == 'status':
        adapter_factory().status(monotonic() + 5)
        return {'status': 'ready'}

    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', args.id):
        raise MailError('invalid_arguments', 'Use a message ID returned by search or wait.')
    try:
        origin = parse_origin(args.origin) if args.origin is not None else None
    except ValueError:
        raise MailError('invalid_arguments', '--origin requires an HTTP(S) origin without a path or query.') from None
    content = adapter_factory().get(args.id, monotonic() + 5)
    return {'message': {'id': args.id, **asdict(content.envelope), 'text': content.text,
                        'html': content.html, 'links': extract_links(content, origin)}}


def main(argv=None, *, adapter_factory=Mailpit) -> int:
    operation = None
    try:
        args = _parser().parse_args(argv)
        operation = args.operation
        if os.geteuid() == 0:
            raise MailError('invalid_arguments', 'Run DevStack Mail as your regular developer account.')
        output = {'ok': True, **_execute(args, adapter_factory)}
        code = 0
    except (Exception, KeyboardInterrupt) as error:
        if isinstance(error, KeyboardInterrupt):
            error = MailError('interrupted')
        elif not isinstance(error, MailError):
            # A traceback from MIME/HTTP parsing could include sensitive mail data.
            error = MailError('internal_error')
        output = {'ok': False, 'error': {'code': error.code, 'message': str(error)}}
        code = error.exit_code
    try:
        print(json.dumps({'schema_version': SCHEMA_VERSION, 'operation': operation, **output}))
        sys.stdout.flush()
    except BrokenPipeError:
        # The caller closed its output pipe. Do not print message data or a traceback.
        sys.stdout = None
        return 1
    return code


if __name__ == '__main__':
    raise SystemExit(main())
