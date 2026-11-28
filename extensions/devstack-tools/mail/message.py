"""Read MIME and report links as data."""

from email import policy
from email.errors import MessageError
from email.message import EmailMessage
from email.parser import BytesParser
from html.parser import HTMLParser
import ipaddress
import re
from urllib.parse import urlsplit

from contract import Address, Content, DOMAIN, Envelope, MailError

MAX_LINKS = 256
MAX_URL_LENGTH = 8192
TEXT_URL = re.compile(r'https?://[^\s<>"\x00-\x1f\x7f]+', re.IGNORECASE | re.ASCII)
Origin = tuple[str, str, int]


def _addresses(message: EmailMessage, header: str) -> tuple[Address, ...]:
    return tuple(
        Address(item.display_name, item.addr_spec)
        for field in message.get_all(header, [])
        for item in field.addresses
        if item.username and item.domain
    )


def _body(message: EmailMessage, kind: str) -> str | None:
    part = message.get_body(preferencelist=(kind,))
    if part is None or part.get_filename() is not None:
        return None
    try:
        return part.get_content(errors='replace')
    except LookupError:  # A sender may specify an unknown charset.
        return part.get_payload(decode=True).decode('utf-8', errors='replace')


def parse_message(raw: bytes) -> Content:
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw)
        if not message.keys():
            raise ValueError('No message headers')
        senders = _addresses(message, 'from')
        envelope = Envelope(str(message.get('subject', '')), senders[0] if senders else None,
                            _addresses(message, 'to'), _addresses(message, 'cc'),
                            _addresses(message, 'bcc'))
        return Content(envelope, _body(message, 'plain'), _body(message, 'html'))
    except (IndexError, LookupError, TypeError, ValueError, RecursionError, MessageError):
        raise MailError('invalid_response') from None


def _split_http_url(value: str) -> tuple[Origin, str] | None:
    if (len(value) > MAX_URL_LENGTH or not value.isprintable() or '\\' in value
            or any(ch.isspace() for ch in value)):
        return None
    try:
        url = urlsplit(value)
        if (url.scheme not in ('http', 'https') or not url.hostname or not url.netloc
                or url.username is not None or url.netloc.endswith(':') or '%' in url.hostname):
            return None
        host = url.hostname
        # Validate the full authority too: urlsplit accepts junk after a closing bracket.
        if url.netloc.startswith('['):
            if not re.fullmatch(r'\[[0-9a-fA-F:.]+\](?::[0-9]+)?', url.netloc):
                return None
            host = ipaddress.IPv6Address(host).compressed
        elif len(host) > 253 or not DOMAIN.fullmatch(host):
            return None
        elif re.fullmatch(r'(?:[0-9]+|0x[0-9a-f]+)', host.rsplit('.', 1)[-1]):
            # Exclude alternate IPv4 spellings whose browser interpretation can differ.
            host = str(ipaddress.IPv4Address(host))
        port = url.port if url.port is not None else (443 if url.scheme == 'https' else 80)
        if port == 0:
            return None
        return (url.scheme, host.lower(), port), url.path
    except ValueError:
        return None


def parse_origin(value: str) -> Origin:
    result = _split_http_url(value)
    if result is None or result[1] not in ('', '/') or '?' in value or '#' in value:
        raise ValueError('Expected a bare HTTP(S) application origin')
    return result[0]


class _Anchors(HTMLParser):
    def __init__(self, add):
        super().__init__(convert_charrefs=True)
        self.add = add

    def handle_starttag(self, tag, attrs):
        if tag in ('a', 'area'):
            href = next((value for key, value in attrs if key == 'href'), None)
            if href:
                self.add(href)


def extract_links(content: Content, origin: Origin | None = None) -> list[str]:
    links: dict[str, None] = {}

    def add(value: str) -> None:
        resolved = _split_http_url(value)
        if resolved is None or (origin is not None and resolved[0] != origin):
            return
        links[value] = None
        if len(links) > MAX_LINKS:
            raise MailError('limit_exceeded')

    if content.html:
        parser = _Anchors(add)
        try:
            parser.feed(content.html)
            parser.close()
        except (AssertionError, ValueError):
            raise MailError('invalid_response') from None
    if content.text:
        for match in TEXT_URL.finditer(content.text):
            add(match.group())
    return list(links)
