"""Bounded Connect audio resolution and durable, tenant-local transcription receipts.

No caller-supplied media URL is ever opened. Bytes live only in memory and a
private temporary decoder directory. A committed claim is never automatically
reissued after a crash/timeout: an uncertain paid request must not run twice.
"""
import io
import os
import subprocess
import tempfile
import time
import uuid
import wave
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from tenant_chat_media import PinnedHTTPS, public_addresses, FORMATS
from tenant_chat_ai_policy import media_address
from tenant_chat_ai_provider import transcribe, ChatAIProviderError
from tenant_ai_agents_schema import ensure_schema
from tenant_platform import _connect, _json
from tenant_connect_transport import CONNECT_URL, connect_route
from tenant_ai_agent_key_policy import platform_token_limit
from tenant_ai_agent_run_store import record_token_usage

MAX_BYTES = 10 * 1024 * 1024
MAX_SECONDS = 300
FORMATS = {**FORMATS, 'audio/aac': 'aac'}
FORMATS.pop('video/mp4', None)


class VoiceError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def download_audio(job, message_id):
    """Resolve local tenant ownership before using its encrypted Connect key."""
    try:
        channel = str(uuid.UUID(str(job['channel_id'])))
        message = str(uuid.UUID(str(message_id)))
    except (ValueError, TypeError, KeyError):
        raise VoiceError('voice_invalid_reference') from None
    with _connect() as conn:
        with conn.cursor() as cur:
            route = connect_route(cur, str(job['tenant_id']), int(job['lead_id']))
    if (not route or route['channel_id'] != channel or
            route['conversation_id'] != str(job['conversation_id'])):
        raise VoiceError('voice_scope_mismatch')
    host = urlsplit(CONNECT_URL).hostname
    url = (CONNECT_URL + '/v1/channels/' + channel + '/messages/' + message +
           '/audio?' + urlencode({'conversationId': route['conversation_id']}))
    connection = None
    try:
        parsed = media_address(url, [host])
        connection = PinnedHTTPS(parsed.hostname, public_addresses(parsed.hostname)[0])
        connection.timeout = 10
        connection.request('GET', parsed.path + '?' + parsed.query, headers={
            'x-api-key': route['api_key'], 'Accept': 'audio/*', 'Accept-Encoding': 'identity'})
        response = connection.getresponse()
        if response.status in {410, 413, 415}:
            raise VoiceError({410: 'voice_expired', 413: 'voice_size_limit', 415: 'voice_unsupported'}[response.status])
        if response.status != 200 or response.getheader('Content-Encoding', 'identity') != 'identity':
            raise VoiceError('voice_media_unavailable')
        if (response.getheader('X-Connect-Channel-Id') != channel or
                response.getheader('X-Connect-Message-Id') != message):
            raise VoiceError('voice_scope_mismatch')
        mime = response.getheader('Content-Type', '').split(';')[0].strip().lower()
        if mime not in FORMATS:
            raise VoiceError('voice_unsupported')
        length = response.getheader('Content-Length')
        if length and not 0 < int(length) <= MAX_BYTES:
            raise VoiceError('voice_size_limit')
        deadline, chunks, total = time.monotonic() + 25, [], 0
        while True:
            if time.monotonic() > deadline:
                raise VoiceError('voice_download_timeout')
            chunk = response.read1(min(65536, MAX_BYTES + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_BYTES:
                raise VoiceError('voice_size_limit')
            chunks.append(chunk)
        if not total:
            raise VoiceError('voice_invalid_audio')
        return b''.join(chunks), FORMATS[mime], mime
    except VoiceError:
        raise
    except Exception:
        raise VoiceError('voice_media_unavailable') from None
    finally:
        if connection:
            connection.close()


def validate_audio(content, extension, mime):
    """Decode locally to measure real duration, rejecting corruption/playlists.

    OpenAI accepts OGG/Opus directly. Only raw AAC is converted to WAV.
    ffmpeg cannot access network protocols or secondary external resources.
    """
    if mime not in FORMATS or extension != FORMATS[mime]:
        raise VoiceError('voice_unsupported')
    if not content or len(content) > MAX_BYTES:
        raise VoiceError('voice_size_limit')
    try:
        with tempfile.TemporaryDirectory(prefix='connect-voice-') as directory:
            path = Path(directory) / ('audio.' + extension)
            path.write_bytes(content)
            decoded = subprocess.run([
                'ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-xerror',
                '-protocol_whitelist', 'file,pipe',
                '-format_whitelist', 'ogg,mp3,wav,mov,matroska,webm,flac,aac',
                '-i', str(path), '-map', '0:a:0', '-vn', '-sn', '-dn',
                '-t', str(MAX_SECONDS + 1), '-ac', '1', '-ar', '16000',
                '-f', 's16le', 'pipe:1'], capture_output=True, timeout=20, check=False)
        if decoded.returncode or not decoded.stdout:
            raise VoiceError('voice_invalid_audio')
        seconds = len(decoded.stdout) / 32000
        if seconds > MAX_SECONDS:
            raise VoiceError('voice_duration_limit')
        if extension == 'aac':
            output = io.BytesIO()
            with wave.open(output, 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(decoded.stdout)
            return output.getvalue(), 'wav', 'audio/wav'
        return content, extension, mime
    except FileNotFoundError:
        raise VoiceError('voice_decoder_unavailable') from None
    except subprocess.TimeoutExpired:
        raise VoiceError('voice_decode_timeout') from None


def active(job, reservation):
    """Recheck lease and takeover revision at each expensive voice boundary."""
    from tenant_ai_agent_test_mode import allowed_job
    from tenant_ai_agent_run_store import load_agent_config
    if not allowed_job(job) or not load_agent_config(job):
        return False
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT 1 FROM saas_ai_agent_jobs j
              JOIN saas_ai_conversation_state cs ON cs.tenant_id=j.tenant_id
                AND cs.provider=j.provider AND cs.conversation_id=j.conversation_id
              WHERE j.tenant_id=%s::uuid AND j.agent_id=%s::uuid AND j.provider='connect'
                AND j.channel_id=%s AND j.conversation_id=%s AND j.state='processing'
                AND j.lease_token=%s AND j.revision=%s AND j.lease_until>now()
                AND cs.state='active' AND cs.revision=%s""",
                        (job['tenant_id'], job['agent_id'], job['channel_id'], job['conversation_id'],
                         job['lease_token'], job['revision'], reservation['conversation_revision']))
            return bool(cur.fetchone())


def _claim(job, reservation, source, model):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO saas_connect_voice_receipts
              (tenant_id,channel_id,external_id,run_id,model,state)
              SELECT tenant_id,%s::uuid,external_id,%s::uuid,%s,'processing'
              FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id=%s
                AND channel=%s AND external_id=%s AND direction='incoming'
                AND message_type IN ('audio','voice','voice_message')
              ON CONFLICT(tenant_id,channel_id,external_id) DO NOTHING RETURNING external_id""",
                        (job['channel_id'], reservation['run_id'], model, job['tenant_id'],
                         int(job['lead_id']), 'connect:' + job['channel_id'], 'connect:' + source['external_id']))
            claimed = bool(cur.fetchone())
            if not claimed:
                cur.execute("""SELECT v.state,v.error_code,m.body FROM saas_connect_voice_receipts v
                  JOIN saas_crm_messages m ON m.tenant_id=v.tenant_id AND m.external_id=v.external_id
                  WHERE v.tenant_id=%s::uuid AND v.channel_id=%s::uuid AND v.external_id=%s
                    AND m.kommo_lead_id=%s AND m.channel=%s""",
                            (job['tenant_id'], job['channel_id'], 'connect:' + source['external_id'],
                             int(job['lead_id']), 'connect:' + job['channel_id']))
                previous = cur.fetchone() or {}
            else:
                previous = {}
        conn.commit()
    if claimed:
        return None
    if previous.get('state') == 'completed' and previous.get('body'):
        return str(previous['body'])
    raise VoiceError('voice_already_claimed')


def _finish(job, reservation, source, *, text='', code=''):
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""UPDATE saas_connect_voice_receipts SET state=%s,error_code=%s,updated_at=now()
              WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND external_id=%s
                AND run_id=%s::uuid AND state='processing' RETURNING external_id""",
                        ('failed' if code else 'completed', code, job['tenant_id'], job['channel_id'],
                         'connect:' + source['external_id'], reservation['run_id']))
            if cur.fetchone():
                cur.execute("""UPDATE saas_crm_messages SET body=%s,raw=raw || %s::jsonb
                  WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND channel=%s AND external_id=%s""",
                            (text, _json({'voice_transcription': {'status': 'failed' if code else 'completed',
                             'error_code': code, 'source': 'whatsapp_audio'}}), job['tenant_id'],
                             int(job['lead_id']), 'connect:' + job['channel_id'], 'connect:' + source['external_id']))
        conn.commit()


def transcription_model(reservation):
    if reservation.get('credential_source') == 'platform':
        return os.environ.get('AI_AGENTS_TRANSCRIPTION_MODEL', 'gpt-4o-mini-transcribe').strip()
    config = reservation.get('config') or {}
    if config.get('audio_enabled') is not True:
        raise VoiceError('voice_disabled')
    return str(config.get('transcription_model') or '').strip()


def transcribe_message(job, reservation, source):
    model = transcription_model(reservation)
    if not model:
        raise VoiceError('voice_model_unavailable')
    cached = _claim(job, reservation, source, model)
    if cached is not None:
        return cached
    try:
        if not active(job, reservation):
            raise VoiceError('voice_conversation_changed')
        media = validate_audio(*download_audio(job, source['external_id']))
        if not active(job, reservation):
            raise VoiceError('voice_conversation_changed')
        text, usage = transcribe(reservation['api_key'], model, *media, with_usage=True)
        # Persist paid usage before any later generation can fail.
        previous_usage = reservation.get('voice_usage') or {}
        reservation['voice_usage'] = {key: int(previous_usage.get(key) or 0) + int(usage.get(key) or 0)
                                      for key in ('input_tokens', 'output_tokens', 'total_tokens')}
        record_token_usage(job, reservation, reservation['voice_usage'], cumulative=True)
        _finish(job, reservation, source, text=text)
        return text
    except ChatAIProviderError as error:
        code = 'voice_http_' + str(error.status_code) + '_' + error.category
        _finish(job, reservation, source, code=code)
        raise VoiceError(code) from None
    except VoiceError as error:
        _finish(job, reservation, source, code=error.code)
        raise
    except Exception:
        _finish(job, reservation, source, code='voice_transcription_failed')
        raise VoiceError('voice_transcription_failed') from None


def generation_budget_available(job, reservation):
    cap = platform_token_limit(str(job['tenant_id'])) if reservation.get('credential_source') == 'platform' else 0
    if not cap:
        return True
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT COALESCE(sum(total_tokens),0) AS tokens FROM saas_ai_agent_token_usage
              WHERE tenant_id=%s::uuid AND created_at>=(now() AT TIME ZONE 'UTC')::date AT TIME ZONE 'UTC'""",
                        (job['tenant_id'],))
            return int((cur.fetchone() or {}).get('tokens') or 0) < cap
