"""Strict, bounded decoder for signed, consented lab recordings. No network I/O."""
import hashlib
import io
import json
import re
import struct
import zipfile
from datetime import datetime, timedelta, timezone
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from tenant_linear_policy import TenantLinearError

MAX_AUDIO = 44 + 16000 * 4 * 120
MAX_BUNDLE = MAX_AUDIO + 16384


def denied():
    return TenantLinearError('Запись не прошла проверку тестового источника.', 400)


def timestamp(value):
    stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('timezone required')
    return stamp.astimezone(timezone.utc)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate field')
        result[key] = value
    return result


def decode(data, public_key, *, now=None, retention_hours=24):
    """Signature authenticates the exact manifest; its hash authenticates WAV.

    Caller pins the public key to one configured source and checks the route.
    A key embedded in an uploaded file is never trusted.
    """
    now = now or datetime.now(timezone.utc)
    try:
        # Selected by the trusted source binding, never by uploaded metadata.
        if type(retention_hours) is not int or retention_hours not in (24, 30*24):
            raise ValueError('retention policy')
        if not isinstance(data, bytes) or not 100 < len(data) <= MAX_BUNDLE:
            raise ValueError('size')
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            files = archive.infolist()
            limits = {'manifest.json': 8192, 'signature.ed25519': 64, 'recording.wav': MAX_AUDIO}
            if len(files) != 3 or {f.filename for f in files} != set(limits):
                raise ValueError('entries')
            for f in files:
                if (f.compress_type != zipfile.ZIP_STORED or f.flag_bits & 1
                        or f.file_size != f.compress_size or not 0 < f.file_size <= limits[f.filename]):
                    raise ValueError('archive format')
            raw = archive.read('manifest.json')
            signature = archive.read('signature.ed25519')
            Ed25519PublicKey.from_public_bytes(public_key).verify(signature, raw)
            manifest = json.loads(raw, object_pairs_hook=_unique_object)
            wav = archive.read('recording.wav')
        if not isinstance(manifest, dict) or type(manifest.get('version')) is not int or manifest.get('version') != 1 or manifest.get('consent_confirmed') is not True:
            raise ValueError('manifest')
        if hashlib.sha256(wav).hexdigest() != manifest.get('sha256'):
            raise ValueError('hash')
        for field in ('session_id', 'call_id'):
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', manifest.get(field, '')):
                raise ValueError('identifier')
        if not re.fullmatch(r'[0-9]{5,20}@s\.whatsapp\.net', manifest.get('account_jid', '')):
            raise ValueError('account')
        if not re.fullmatch(r'[0-9]{5,20}@(lid|s\.whatsapp\.net)', manifest.get('peer_jid', '')):
            raise ValueError('peer')
        start, end, expiry = [timestamp(manifest.get(k)) for k in ('started_at', 'ended_at', 'expires_at')]
        if not (now - timedelta(hours=24) < start <= end <= now + timedelta(seconds=30)
                and end-start <= timedelta(seconds=125) and now < expiry <= start + timedelta(hours=retention_hours)):
            raise ValueError('time bounds')
        duration = manifest.get('duration_ms')
        if type(duration) is not int or not 1 <= duration <= 120000:
            raise ValueError('duration')
        if manifest.get('sample_rate') != 16000 or manifest.get('channels') != ['employee', 'caller']:
            raise ValueError('format')
        if len(wav) < 48:
            raise ValueError('empty audio')
        header = struct.unpack('<4sI4s4sIHHIIHH4sI', wav[:44])
        size = len(wav)-44
        if header != (b'RIFF',len(wav)-8,b'WAVE',b'fmt ',16,1,2,16000,64000,4,16,b'data',size) or size%4:
            raise ValueError('wav layout')
        frames = size//4
        if abs(frames/16-duration) > 1:
            raise ValueError('duration mismatch')
        for key in ('samples_received','non_silent_samples','gaps'):
            values = manifest.get(key)
            if not isinstance(values,list) or len(values)!=2 or any(type(v) is not int or not 0 <= v <= frames for v in values):
                raise ValueError('counts')
        if any(n>s for n,s in zip(manifest['non_silent_samples'],manifest['samples_received'])):
            raise ValueError('counts')
        if type(manifest.get('complete')) is not bool or manifest.get('end_code') not in {'normal','interrupted','duration_limit','missing_direction','audio_gaps'}:
            raise ValueError('outcome')
        if manifest['complete'] and (manifest['end_code']!='normal' or min(manifest['samples_received'])<=0 or any(manifest['gaps'])):
            raise ValueError('completion')
        return manifest, wav
    except Exception:
        # ZIP, crypto and decoder exception strings may contain private input.
        raise denied() from None
