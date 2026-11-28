"""Mailbox data and operations shared with future adapters."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import re
from typing import Protocol

ERRORS = {
    'internal_error': (1, 'DevStack Mail could not complete the operation.'),
    'invalid_arguments': (2, 'Invalid command arguments. See devstack-mail --help.'),
    'not_found': (3, 'The message is no longer in the mailbox.'),
    'timeout': (4, 'No matching message arrived before the timeout.'),
    'not_installed': (5, 'Local Mailpit is not installed. Install it separately through DevStack.'),
    'unavailable': (5, 'Local Mailpit is unavailable. Check the local mail service in DevStack.'),
    'unsupported_service': (6, 'This local Mailpit installation is unsupported. See the DevStack Tools README.'),
    'invalid_response': (6, 'The local mailbox returned an invalid response.'),
    'limit_exceeded': (6, 'The mailbox response exceeds a DevStack Mail limit.'),
    'interrupted': (130, 'The mail operation was interrupted.'),
}


class MailError(Exception):
    def __init__(self, code: str, message: str | None = None):
        self.code = code
        self.exit_code, default_message = ERRORS[code]
        super().__init__(message if message is not None else default_message)


ATOM = r"[A-Za-z0-9!#$%&'*+\-/=?^_`{|}~]+"
DOMAIN = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*',
                    re.ASCII | re.IGNORECASE)
ADDRESS = re.compile(rf'{ATOM}(?:\.{ATOM})*', re.ASCII)
TIMESTAMP = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}'
                       r'(?:\.[0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])')


def address_key(value: str) -> tuple[str, str] | None:
    local, sep, domain = value.rpartition('@')
    if (not sep or len(value) > 254 or len(local) > 64
            or not ADDRESS.fullmatch(local) or not DOMAIN.fullmatch(domain)):
        return None
    return local, domain.lower()


def parse_timestamp(value: str) -> datetime:
    if not TIMESTAMP.fullmatch(value):
        raise ValueError('Expected an RFC 3339 timestamp with an explicit offset')
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)


@dataclass(frozen=True)
class Address:
    name: str
    address: str


@dataclass(frozen=True)
class Envelope:
    subject: str
    sender: Address | None
    to: tuple[Address, ...]
    cc: tuple[Address, ...]
    bcc: tuple[Address, ...]


@dataclass(frozen=True)
class Summary:
    id: str
    received_at: datetime
    envelope: Envelope

    def to_json(self) -> dict:
        return {
            'id': self.id,
            'received_at': self.received_at.isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
            **asdict(self.envelope),
        }


@dataclass(frozen=True)
class Content:
    envelope: Envelope
    text: str | None
    html: str | None


@dataclass(frozen=True)
class Query:
    recipient: tuple[str, str]
    subject: str | None
    since: datetime | None

    def matches(self, message: Summary) -> bool:
        envelope = message.envelope
        return (
            (self.since is None or message.received_at > self.since)
            and (self.subject is None or envelope.subject == self.subject)
            and any(address_key(item.address) == self.recipient
                    for item in (*envelope.to, *envelope.cc, *envelope.bcc))
        )


class Mailbox(Protocol):
    """A broker adapter must enforce its own trusted session scope on every operation."""

    def status(self, deadline: float) -> None: ...

    def search(self, query: Query, deadline: float) -> list[Summary]: ...

    def get(self, message_id: str, deadline: float) -> Content: ...
