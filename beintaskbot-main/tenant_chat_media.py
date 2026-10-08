"""Public HTTPS only, pinned DNS, exact approved host, no redirects or credentials."""
import http.client
import ipaddress
import socket
import ssl
import time
from tenant_chat_ai_policy import media_address

MAX_BYTES = 20 * 1024 * 1024
FORMATS = {'audio/ogg': 'ogg', 'audio/mpeg': 'mp3', 'audio/mp3': 'mp3', 'audio/wav': 'wav',
           'audio/x-wav': 'wav', 'audio/mp4': 'm4a', 'audio/webm': 'webm', 'video/mp4': 'mp4',
           'audio/flac': 'flac', 'audio/x-flac': 'flac', 'application/ogg': 'ogg'}


def public_addresses(host):
    addresses = list(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)))
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError('Non-public media address')
    return addresses


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address):
        super().__init__(host, timeout=15, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        sock = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise


def download(url, hosts):
    parsed = media_address(url, hosts)
    address = public_addresses(parsed.hostname)[0]
    connection = PinnedHTTPS(parsed.hostname, address)
    try:
        path = parsed.path or '/'
        if parsed.query:
            path += '?' + parsed.query
        connection.request('GET', path, headers={'Accept': 'audio/*,video/mp4', 'Accept-Encoding': 'identity'})
        response = connection.getresponse()
        if response.status != 200 or response.getheader('Content-Encoding', 'identity') != 'identity':
            raise ValueError('Media unavailable')
        content_type = response.getheader('Content-Type', '').split(';')[0].lower().strip()
        if content_type not in FORMATS:
            raise ValueError('Unsupported audio format')
        size = response.getheader('Content-Length')
        if size and (int(size) <= 0 or int(size) > MAX_BYTES):
            raise ValueError('Audio too large')
        deadline, chunks, total = time.monotonic() + 60, [], 0
        while True:
            if time.monotonic() > deadline:
                raise ValueError('Media download timed out')
            chunk = response.read1(min(65536, MAX_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_BYTES:
                raise ValueError('Audio too large')
        content = b''.join(chunks)
        if not content or len(content) > MAX_BYTES:
            raise ValueError('Audio too large')
        return content, FORMATS[content_type], content_type
    finally:
        connection.close()
