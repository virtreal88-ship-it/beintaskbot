"""Validate a customer voice recording locally, without sending or invoking AI."""
from tenant_connect_voice import VoiceError, validate_audio

MAX_RECORDING_BYTES = 5 * 1024 * 1024
MAX_RECORDING_SECONDS = 120


def validate_recording(content, mime):
    """Apply a small format allowlist, then use the existing bounded decoder.

    MIME and magic bytes only select a decoder; they do not prove duration or
    that the data is valid audio. The existing decoder establishes both. No
    client-supplied path, URL or filename is ever opened.
    """
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_RECORDING_BYTES:
        raise VoiceError('voice_size_limit')
    if not isinstance(mime, str):
        raise VoiceError('voice_unsupported')
    mime = mime.split(';', 1)[0].strip().lower()
    signatures = {
        'audio/ogg': ('ogg', content.startswith(b'OggS')),
        'audio/webm': ('webm', content.startswith(b'\x1a\x45\xdf\xa3')),
        'audio/mp4': ('m4a', len(content) >= 12 and content[4:8] == b'ftyp'),
    }
    extension, matched = signatures.get(mime, ('', False))
    if not matched:
        raise VoiceError('voice_unsupported')
    return validate_audio(content, extension, mime, max_seconds=MAX_RECORDING_SECONDS)
