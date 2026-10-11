"""Bounded new-file input; no remote URLs or client-provided Drive identifiers."""
import hashlib
import re
from tenant_chat_send_policy import command
from tenant_linear_policy import TenantLinearError

MAX_BYTES = 5 * 1024 * 1024
TYPES = {'image/jpeg': 'picture', 'image/png': 'picture', 'image/webp': 'picture',
         'application/pdf': 'file', 'audio/mpeg': 'audio', 'audio/ogg': 'audio',
         'video/mp4': 'video'}


def prepare(data, filename, mime, content):
    if (not isinstance(filename, str) or not 0 < len(filename) <= 160
            or re.search(r'[\x00-\x1f\x7f/\\]', filename) or filename in {'.', '..'}
            or mime not in TYPES or not isinstance(content, bytes) or not 0 < len(content) <= MAX_BYTES):
        raise TenantLinearError('Fayl düzgün deyil (JPEG, PNG, WebP, PDF, MP3, OGG, MP4; maksimum 5 MB).')
    signatures = {
        'image/jpeg': content.startswith(b'\xff\xd8\xff'),
        'image/png': content.startswith(b'\x89PNG\r\n\x1a\n'),
        'image/webp': content.startswith(b'RIFF') and content[8:12] == b'WEBP',
        'application/pdf': content.startswith(b'%PDF-'),
        'audio/mpeg': content.startswith(b'ID3') or (len(content)>1 and content[0]==255 and content[1]&224==224),
        'audio/ogg': content.startswith(b'OggS'),
        'video/mp4': len(content)>12 and content[4:8]==b'ftyp',
    }
    if not signatures[mime]:
        raise TenantLinearError('Faylın tipi məzmununa uyğun deyil.')
    text = data.get('text', '')
    if not isinstance(text, str):
        raise TenantLinearError('Mesaj mətni düzgün deyil.')
    # Reuse the original request/lead validation. Empty captions are allowed.
    value = command({**data, 'text': text if text.strip() else filename})
    value['text'] = text.strip()
    value['file'] = {'name': filename, 'mime': mime, 'size': len(content),
                     'sha256': hashlib.sha256(content).hexdigest(), 'type': TYPES[mime]}
    return value
