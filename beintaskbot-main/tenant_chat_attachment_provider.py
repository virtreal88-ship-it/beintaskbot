"""Pinned HTTPS; bearer only on Drive metadata, never on a download link."""
import json
import re
import time
from uuid import UUID
from urllib.parse import urlsplit
from tenant_chat_media import PinnedHTTPS, public_addresses
from tenant_chat_ai_policy import media_address

MAX_BYTES = 20 * 1024 * 1024
TYPES = {'image/jpeg', 'image/png', 'image/gif', 'image/webp', 'audio/ogg', 'audio/mpeg',
         'audio/mp3', 'audio/mp4', 'audio/wav', 'audio/x-wav', 'audio/webm', 'audio/flac',
         'application/ogg', 'video/mp4', 'video/webm', 'application/pdf', 'application/octet-stream'}


def drive_host(value: object) -> str:
    parsed = urlsplit(str(value))
    if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.query
            or parsed.fragment or parsed.port not in {None, 443} or parsed.path not in {'', '/'}
            or not re.fullmatch(r'drive(?:-[a-z0-9]+)?\.kommo\.com', parsed.hostname or '')):
        raise ValueError('Invalid Drive address')
    return parsed.hostname


def file_id(row: dict) -> str | None:
    raw = row.get('raw') if isinstance(row.get('raw'), dict) else {}
    raw = raw.get('message') if isinstance(raw.get('message'), dict) else raw
    attachment = raw.get('attachment') if isinstance(raw.get('attachment'), dict) else {}
    value = attachment.get('drive_uuid') or attachment.get('file_uuid') or raw.get('file_uuid')
    if value:
        return str(UUID(str(value)))
    return None


def fetch(url: str, host: str, *, bearer: str | None = None, metadata: bool = False,
          deadline: float | None = None) -> tuple[bytes, str]:
    parsed = media_address(url, [host])
    if bearer and (not metadata or not re.fullmatch(r'/v1\.0/files/[0-9a-f-]{36}', parsed.path)):
        raise ValueError('Credential destination not approved')
    deadline = deadline if deadline is not None else time.monotonic() + 35
    addresses = public_addresses(host)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError('Attachment timeout')
    connection = PinnedHTTPS(host, addresses[0])
    connection.timeout = min(8, remaining)
    cap = 128 * 1024 if metadata else MAX_BYTES
    try:
        headers = {'Accept-Encoding': 'identity', 'Accept': 'application/json' if metadata else '*/*'}
        if bearer:
            headers['Authorization'] = 'Bearer ' + bearer
        connection.request('GET', parsed.path + ('?' + parsed.query if parsed.query else ''), headers=headers)
        response = connection.getresponse()
        if response.status != 200 or response.getheader('Content-Encoding', 'identity') != 'identity':
            raise ValueError('Attachment unavailable')
        content_type = response.getheader('Content-Type', '').split(';')[0].strip().lower()
        if content_type not in ({'application/json'} if metadata else TYPES):
            raise ValueError('Unsafe attachment format')
        length = response.getheader('Content-Length')
        if length and not 0 < int(length) <= cap:
            raise ValueError('Attachment too large')
        chunks, size = [], 0
        while True:
            if time.monotonic() > deadline:
                raise ValueError('Attachment timeout')
            part = response.read1(min(65536, cap + 1 - size))
            if not part:
                break
            size += len(part)
            if size > cap:
                raise ValueError('Attachment too large')
            chunks.append(part)
        if not size or (length and size != int(length)):
            raise ValueError('Incomplete attachment')
        return b''.join(chunks), content_type
    finally:
        connection.close()


def download(row: dict, drive: str, bearer: str) -> tuple[bytes, str]:
    host = drive_host(drive)
    deadline = time.monotonic() + 40
    identifier = file_id(row)
    url = str(row.get('media_url') or '')
    if identifier:
        body, _ = fetch(f'https://{host}/v1.0/files/{identifier}', host, bearer=bearer, metadata=True, deadline=deadline)
        metadata = json.loads(body)
        url = metadata.get('_links', {}).get('download', {}).get('href')
    # Only the current account's Drive host. Redirects and external CDN hosts fail closed.
    parsed = media_address(url, [host])
    if not parsed.path.startswith('/download/'):
        raise ValueError('Unapproved attachment path')
    return fetch(url, host, deadline=deadline)
