"""Pinned account Drive uploads, bounded parts/deadline; never resend a part."""
import json
import time
import uuid
from urllib.parse import urlencode
from tenant_chat_attachment_provider import drive_host
from tenant_chat_media import PinnedHTTPS, public_addresses
from tenant_chat_ai_policy import media_address
from tenant_chat_upload_policy import MAX_BYTES


def call(host, path, body, mime, deadline, *, bearer=None, method='POST'):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError('Upload timeout')
    connection = PinnedHTTPS(host, public_addresses(host)[0])
    connection.timeout = min(10, remaining)
    try:
        headers = {'Accept': 'application/json', 'Content-Type': mime, 'Accept-Encoding': 'identity'}
        if bearer:
            headers['Authorization'] = 'Bearer ' + bearer
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        if response.status not in {200, 201} or response.getheader('Content-Encoding', 'identity') != 'identity':
            raise ValueError('Drive upload unavailable')
        if response.getheader('Content-Type', '').split(';')[0] != 'application/json':
            raise ValueError('Invalid Drive result')
        parts, size = [], 0
        while True:
            if time.monotonic() > deadline:
                raise ValueError('Upload timeout')
            part = response.read1(min(65536, 131073-size))
            if not part:
                break
            size += len(part)
            if size > 131072:
                raise ValueError('Drive response too large')
            parts.append(part)
        result = json.loads(b''.join(parts))
        if not isinstance(result, dict):
            raise ValueError('Invalid Drive result')
        return result
    finally:
        connection.close()


def upload_path(url, host):
    parsed = media_address(url, [host])
    if not parsed.path.startswith('/upload/') or len(parsed.path) <= len('/upload/'):
        raise ValueError('Invalid upload destination')
    return parsed.path + ('?' + parsed.query if parsed.query else '')


def upload(drive, bearer, file, content):
    host = drive_host(drive)
    if not 0 < len(content) <= MAX_BYTES or len(content) != file['size']:
        raise ValueError('Invalid file size')
    deadline = time.monotonic() + 90
    session = call(host, '/v1.0/sessions', json.dumps({'file_name': file['name'],
        'file_size': len(content), 'content_type': file['mime']}).encode(),
        'application/json', deadline, bearer=bearer)
    maximum, part_size = session.get('max_file_size'), session.get('max_part_size')
    if type(maximum) is not int or maximum < len(content) or type(part_size) is not int or not 32768 <= part_size <= MAX_BYTES:
        raise ValueError('Invalid upload limits')
    part_size = min(part_size, 1024*1024)
    url = session.get('upload_url')
    seen = set()
    final = {}
    for offset in range(0, len(content), part_size):
        path = upload_path(url, host)
        if path in seen:
            raise ValueError('Upload made no progress')
        seen.add(path)
        final = call(host, path, content[offset:offset+part_size], 'application/octet-stream', deadline)
        url = final.get('next_url')
        if offset+part_size < len(content) and not url:
            raise ValueError('Incomplete upload')
    if url:
        raise ValueError('Upload not finalized')
    # A version UUID alone must never be guessed to be a file UUID.
    version = str(uuid.UUID(str(final.get('version_uuid') or final.get('uuid'))))
    file_uuid = final.get('file_uuid') or (final.get('uuid') if final.get('version_uuid') else None)
    if not file_uuid:
        # The prose documents a version-only response; the official example
        # includes both UUIDs. Resolve the former by exact version, not filename.
        query = urlencode({'filter[term]': final.get('name') or file['name']})
        metadata = call(host, '/v1.0/files?' + query, None, 'application/json', deadline,
                        bearer=bearer, method='GET')
        rows = (metadata.get('_embedded') or {}).get('files', [])
        if not isinstance(rows, list) or len(rows) > 50:
            raise ValueError('File metadata unavailable')
        matches = [row for row in rows if isinstance(row, dict)
                   and str(row.get('version_uuid')) == version
                   and not row.get('deleted_at') and not row.get('is_trashed')]
        if len(matches) != 1:
            raise ValueError('File/version identity not confirmed')
        file_uuid = matches[0].get('uuid')
    return {'drive_uuid': str(uuid.UUID(str(file_uuid))),
            'drive_version_uuid': version, 'type': file['type']}
