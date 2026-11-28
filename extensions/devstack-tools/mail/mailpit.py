"""Read-only adapter for the DevStack-managed Mailpit at a fixed loopback address."""

import http.client
import json
import os
from pathlib import Path
import re
import socket
import stat
from threading import Timer
from time import monotonic

from contract import Address, Envelope, MailError, Query, Summary, parse_timestamp
from message import parse_message

VERSION_FILE = Path('/opt/devstack-mailpit/VERSION')
SUPPORTED = b'v1.34.0\n'
HOST = '127.0.0.1'
PORT = 8025
MESSAGE_ID = re.compile(r'[A-Za-z0-9]{22}')
MAX_MESSAGES = 1000  # The configured retention is 500, pruned once a minute.
MAX_LIST_BYTES = 4 * 1024 * 1024
MAX_RAW_BYTES = 11 * 1024 * 1024  # 10 MiB SMTP limit plus headers added by Mailpit.
REQUEST_TIMEOUT = 3.0


def _check_installation() -> None:
    try:
        fd = os.open(VERSION_FILE, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise MailError('not_installed') from None
    except OSError:
        raise MailError('unsupported_service') from None
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                or stream.read(32) != SUPPORTED):
            raise MailError('unsupported_service')


def _interrupt(transport: socket.socket) -> None:
    try:
        transport.shutdown(socket.SHUT_RDWR)
    except OSError:
        # The owning request may have already closed its socket.
        pass


def _request(path: str, limit: int, deadline: float, *, media_type: str | None = None,
             can_be_missing: bool = False) -> bytes:
    end = min(deadline, monotonic() + REQUEST_TIMEOUT)
    remaining = end - monotonic()
    if remaining <= 0:
        raise MailError('unavailable')
    connection = http.client.HTTPConnection(HOST, PORT, timeout=remaining)
    timer = None
    try:
        connection.connect()
        # A socket timeout alone resets on each read. Shut down the
        # connection at the absolute deadline, including slow headers.
        timer = Timer(max(0, end - monotonic()), _interrupt, args=(connection.sock,))
        timer.daemon = True
        timer.start()
        connection.request('GET', path, headers={'Connection': 'close', 'Accept-Encoding': 'identity'})
        with connection.getresponse() as response:
            if response.status == 404 and can_be_missing:
                raise MailError('not_found')
            if 500 <= response.status <= 599:
                raise MailError('unavailable')
            if response.status != 200:
                raise MailError('invalid_response')
            kind = response.getheader('Content-Type', '').partition(';')[0].strip().lower()
            if ((media_type and kind != media_type)
                    or response.getheader('Content-Encoding', 'identity').lower() != 'identity'):
                raise MailError('invalid_response')
            length = response.getheader('Content-Length')
            if length is not None:
                if not re.fullmatch(r'[0-9]{1,20}', length):
                    raise MailError('invalid_response')
                if int(length) > limit:
                    raise MailError('limit_exceeded')
            body = response.read(limit + 1)
            if monotonic() >= end:
                raise MailError('unavailable')
            if len(body) > limit:
                raise MailError('limit_exceeded')
            if length is not None and len(body) != int(length):
                raise MailError('invalid_response')
            return body
    except OSError:
        raise MailError('unavailable') from None
    except http.client.HTTPException:
        if monotonic() >= end:
            raise MailError('unavailable') from None
        raise MailError('invalid_response') from None
    finally:
        if timer is not None:
            timer.cancel()
        connection.close()


def _address(value: object) -> Address:
    if not isinstance(value, dict) or not all(isinstance(value.get(key), str)
                                              for key in ('Name', 'Address')):
        raise ValueError('Invalid address')
    return Address(value['Name'], value['Address'])


def _recipients(value: object) -> tuple[Address, ...]:
    if value is None:  # Mailpit emits null for absent address lists.
        return ()
    if not isinstance(value, list):
        raise ValueError('Invalid address list')
    return tuple(_address(item) for item in value)


def _summary(value: object) -> Summary:
    if not isinstance(value, dict):
        raise ValueError('Invalid message summary')
    message_id = value['ID']
    subject = value['Subject']
    if not isinstance(message_id, str) or not MESSAGE_ID.fullmatch(message_id) or not isinstance(subject, str):
        raise ValueError('Invalid message metadata')
    sender = _address(value['From']) if value['From'] is not None else None
    if sender == Address('', ''):
        sender = None
    envelope = Envelope(subject, sender, _recipients(value['To']),
                        _recipients(value['Cc']), _recipients(value['Bcc']))
    return Summary(message_id, parse_timestamp(value['Created']), envelope)


class Mailpit:
    def __init__(self):
        _check_installation()

    def status(self, deadline: float) -> None:
        # In v1.34.0 this checks storage readiness and returns an empty 200.
        # /api/v1/info can cause a version lookup on other Mailpit installs.
        if _request('/readyz', 16, deadline) != b'':
            raise MailError('invalid_response')

    def search(self, query: Query, deadline: float) -> list[Summary]:
        # One read of the retained mailbox avoids offset races under SMTP
        # arrivals. The extra row detects a mailbox outside the supported bound.
        raw = _request(f'/api/v1/messages?limit={MAX_MESSAGES + 1}', MAX_LIST_BYTES, deadline,
                       media_type='application/json')
        try:
            page = json.loads(raw)['messages']
            if not isinstance(page, list):
                raise ValueError('Invalid message list')
            if len(page) > MAX_MESSAGES:
                raise MailError('limit_exceeded')
            messages = [_summary(entry) for entry in page]
            if len({message.id for message in messages}) != len(messages):
                raise ValueError('Duplicate message ID')
        except (KeyError, OverflowError, TypeError, ValueError, RecursionError):
            raise MailError('invalid_response') from None
        return sorted((message for message in messages if query.matches(message)),
                      key=lambda message: (message.received_at, message.id), reverse=True)

    def get(self, message_id: str, deadline: float):
        if not MESSAGE_ID.fullmatch(message_id):
            raise MailError('invalid_arguments', 'Use a message ID returned by search or wait.')
        # The JSON message endpoint marks mail read. In v1.34.0, DownloadRaw
        # reads the stored MIME from mailbox_data without updating read state.
        raw = _request(f'/api/v1/message/{message_id}/raw', MAX_RAW_BYTES, deadline,
                       media_type='text/plain', can_be_missing=True)
        return parse_message(raw)
