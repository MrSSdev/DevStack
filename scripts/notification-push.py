"""Unprivileged, optional Web Push sender; no arbitrary URL or redirect."""
from __future__ import annotations

import base64
import fcntl
import json
import os
from pathlib import Path
import re
import sys
import urllib.parse

ROOT = Path('/var/lib/devstack-web')
KEY = ROOT / 'notification-vapid.pem'


def valid_subscription(value):
    if not isinstance(value, dict):
        raise ValueError('Invalid subscription')
    endpoint = value.get('endpoint')
    if not isinstance(endpoint, str) or len(endpoint) > 4096 or re.search(r'[\x00-\x20\x7f]', endpoint):
        raise ValueError('Invalid endpoint')
    url = urllib.parse.urlsplit(endpoint)
    host = url.hostname or ''
    allowed = host in ('fcm.googleapis.com', 'updates.push.services.mozilla.com') or re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)*\.notify\.windows\.com', host)
    if not allowed or url.scheme != 'https' or url.username is not None or url.password is not None or url.fragment or url.port not in (None, 443):
        raise ValueError('Invalid endpoint')
    if host == 'fcm.googleapis.com' and not re.fullmatch(r'/(?:fcm/send|wp)/[A-Za-z0-9_:/.-]+', url.path):
        raise ValueError('Invalid FCM path')
    keys = value.get('keys')
    if not isinstance(keys, dict):
        raise ValueError('Invalid keys')
    for name, size in (('p256dh', 65), ('auth', 16)):
        v = keys.get(name)
        if not isinstance(v, str) or len(v) > 100 or not re.fullmatch(r'[A-Za-z0-9_-]+', v):
            raise ValueError('Invalid key')
        raw = base64.urlsafe_b64decode(v + '=' * (-len(v) % 4))
        if len(raw) != size or (name == 'p256dh' and raw[0] != 4):
            raise ValueError('Invalid key')
    return {'endpoint': endpoint, 'keys': {'p256dh': keys['p256dh'], 'auth': keys['auth']}}


def public_key():
    # Only the public key is returned to PHP. The private key stays in the
    # existing private devstackweb state directory and survives code updates.
    import pywebpush  # Check optional transport before generating any key.
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    os.umask(0o077)
    with (ROOT / 'notification-vapid.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not KEY.exists():
            key = ec.generate_private_key(ec.SECP256R1())
            temporary = ROOT / '.notification-vapid.tmp'
            with temporary.open('wb') as out:
                out.write(key.private_bytes(serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            os.replace(temporary, KEY)
        key = serialization.load_pem_private_key(KEY.read_bytes(), password=None)
    return base64.urlsafe_b64encode(key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)).decode('ascii').rstrip('=')


def send(value):
    from pywebpush import webpush, WebPushException
    import requests
    subscription = valid_subscription(value.get('subscription'))
    payload = value.get('payload')
    if (not isinstance(payload, dict) or set(payload) != {'id', 'category', 'kind'} or
        not isinstance(payload['id'], str) or not re.fullmatch(r'(?:op|job|batch|service)-[a-f0-9]{32}|mail-[A-Za-z0-9]{22}', payload['id']) or
        payload['category'] not in ('mail','project','installer','service','settings') or
        payload['kind'] not in ('success','error','info')):
        raise ValueError('Invalid payload')
    if not KEY.is_file():
        return {'ok': False}
    # The approved endpoint is revalidated here, and redirects/proxies are
    # disabled even if the remote service returns a 3xx response.
    class NoRedirectSession(requests.Session):
        def request(self, method, url, **kwargs):
            if url != subscription['endpoint']:
                raise ValueError('Unexpected push target')
            kwargs['allow_redirects'] = False
            return super().request(method, url, **kwargs)

    with NoRedirectSession() as session:
        session.trust_env = False
        host = urllib.parse.urlsplit(subscription['endpoint']).hostname or ''
        headers = {'X-WNS-Type': 'wns/raw'} if host.endswith('.notify.windows.com') else {}
        try:
            response = webpush(subscription, data=json.dumps(payload, separators=(',', ':')),
                vapid_private_key=str(KEY), vapid_claims={'sub': 'https://devstack.test'},
                ttl=30, timeout=8, headers=headers, requests_session=session)
            return {'ok': 200 <= response.status_code < 300}
        except WebPushException as error:
            response = error.response
            return {'ok': False, 'expired': response is not None and response.status_code in (404,410)}


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ('public', 'send') or os.geteuid() == 0:
        raise ValueError('Invalid invocation')
    raw = sys.stdin.buffer.read(16385)
    if len(raw) > 16384:
        raise ValueError('Request too large')
    value = json.loads(raw)
    return {'ok': True, 'key': public_key()} if sys.argv[1] == 'public' else send(value)


if __name__ == '__main__':
    try:
        result = main()
    except Exception:
        # Do not log endpoint tokens, cryptographic keys or push content.
        result = {'ok': False}
    print(json.dumps(result), flush=True)
