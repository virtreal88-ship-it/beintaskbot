"""Bounded public HTTPS fetches for chat AI; pinned DNS and safe redirects."""
import time
from urllib.parse import urljoin, urlsplit
from tenant_chat_ai_policy import media_address
from tenant_chat_media import PinnedHTTPS, public_addresses


def fetch_public(url: str, *, headers: dict | None = None, max_bytes: int = 5_000_000,
                 deadline: float | None = None) -> tuple[bytes, str]:
    deadline = deadline or time.monotonic() + 15
    origin = urlsplit(url).hostname
    for _ in range(5):
        parsed = media_address(url, {urlsplit(url).hostname})
        if time.monotonic() >= deadline:
            raise ValueError('Download timed out')
        address = public_addresses(parsed.hostname)[0]
        connection = PinnedHTTPS(parsed.hostname, address)
        connection.timeout = min(5, max(0.1, deadline - time.monotonic()))
        try:
            path = parsed.path or '/'
            if parsed.query:
                path += '?' + parsed.query
            request_headers = {'Accept-Encoding': 'identity', 'User-Agent': 'CRM-Chat-Context/1.0'}
            if parsed.hostname == origin:
                request_headers.update(headers or {})
            connection.request('GET', path, headers=request_headers)
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader('Location')
                if not location:
                    raise ValueError('Invalid redirect')
                url = urljoin(url, location)
                continue
            if response.status != 200 or response.getheader('Content-Encoding', 'identity') != 'identity':
                raise ValueError('Content unavailable')
            size = response.getheader('Content-Length')
            if size and int(size) > max_bytes:
                raise ValueError('Content too large')
            chunks, total = [], 0
            while True:
                if time.monotonic() >= deadline:
                    raise ValueError('Download timed out')
                chunk = response.read1(min(65536, max_bytes + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError('Content too large')
            if not total:
                raise ValueError('Empty content')
            return b''.join(chunks), response.getheader('Content-Type', '')
        finally:
            connection.close()
    raise ValueError('Too many redirects')
