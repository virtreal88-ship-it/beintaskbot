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


def native_opus(content,mime):
    """Bounded local conversion of an already validated browser recording."""
    import subprocess
    import tempfile
    from pathlib import Path
    content,extension,_=validate_recording(content,mime)
    try:
        with tempfile.TemporaryDirectory(prefix='native-voice-') as folder:
            source=Path(folder)/('input.'+extension);target=Path(folder)/'output.ogg'
            source.write_bytes(content)
            result=subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-xerror',
              '-protocol_whitelist','file,pipe','-format_whitelist','ogg,mov,matroska,webm',
              '-i',str(source),'-map','0:a:0','-vn','-sn','-dn','-map_metadata','-1',
              # Browser timestamps can drift or jump between chunks. Build the
              # Ogg Opus timeline from the actual resampled audio (RFC 7845).
              '-af','aresample=48000,asetpts=N/SR/TB',
              '-threads','1','-ac','1','-c:a','libopus','-b:a','32k','-application','voip',
              '-t',str(MAX_RECORDING_SECONDS+1),'-fs',str(MAX_RECORDING_BYTES+1),'-f','ogg',str(target)],
              stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20,check=False)
            if result.returncode or not target.exists():raise VoiceError('voice_invalid_audio')
            if not 0<target.stat().st_size<=MAX_RECORDING_BYTES:raise VoiceError('voice_size_limit')
            output=target.read_bytes()
            validate_recording(output,'audio/ogg')
            return output
    except FileNotFoundError:raise VoiceError('voice_decoder_unavailable') from None
    except subprocess.TimeoutExpired:raise VoiceError('voice_decode_timeout') from None
