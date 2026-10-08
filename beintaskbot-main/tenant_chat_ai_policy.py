"""Bounded chat input; messages are evidence, never privileged instructions."""
import hashlib
import json
import re
from urllib.parse import urlsplit
from tenant_linear_policy import TenantLinearError, identifier


def configuration(data):
    result = {}
    for flag in ('enabled', 'consent', 'audio_enabled'):
        if type(data.get(flag)) is not bool:
            raise TenantLinearError('AI ayarları düzgün deyil.')
        result[flag] = data[flag]
    if result['enabled'] and not result['consent']:
        raise TenantLinearError('OpenAI-yə məlumat göndərilməsinə icazə verin.')
    for field in ('model', 'transcription_model'):
        value = data.get(field)
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', value):
            raise TenantLinearError('Modelin adını yazın.')
        result[field] = value
    limit = data.get('daily_limit')
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise TenantLinearError('Gündəlik limit 1–1000 olmalıdır.')
    result['daily_limit'] = limit
    hosts = data.get('media_hosts', [])
    if not isinstance(hosts, list) or len(hosts) > 20:
        raise TenantLinearError('Media domenləri düzgün deyil.')
    result['media_hosts'] = []
    for host in hosts:
        if not isinstance(host, str) or len(host) > 253 or not re.fullmatch(r'[a-zA-Z0-9]+(?:[.-][a-zA-Z0-9]+)*', host) or '.' not in host:
            raise TenantLinearError('Yalnız tam media domenlərini yazın.')
        result['media_hosts'].append(host.lower())
    return result


def command(data):
    mode = data.get('mode')
    if mode not in {'reply', 'summary', 'transcribe'}:
        raise TenantLinearError('AI əməliyyatı düzgün deyil.')
    lead = data.get('lead_id')
    if type(lead) is not int or lead <= 0:
        raise TenantLinearError('Sövdələşmə seçilməyib.')
    draft = data.get('draft', '')
    external = data.get('message_id', '')
    if not isinstance(draft, str) or len(draft) > 3500 or not isinstance(external, str) or len(external) > 500:
        raise TenantLinearError('Mətn düzgün deyil.')
    if mode == 'transcribe' and not external:
        raise TenantLinearError('Səs mesajını seçin.')
    return {'request_id': identifier(data.get('request_id')), 'lead_id': lead,
            'mode': mode, 'draft': draft, 'message_id': external}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def audio(row):
    return str(row.get('message_type') or '').lower() in {'voice', 'audio', 'voice_message', 'call', 'call_recording'}


def media_identity(row, model):
    return fingerprint({k: row.get(k) for k in ('external_id', 'media_url', 'happened_at')} | {'model': model})


def media_address(url, hosts):
    if not isinstance(url, str) or len(url) > 3000 or any(ord(c) < 33 for c in url) or '\\' in url:
        raise ValueError('Invalid media address')
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.fragment or parsed.port not in {None, 443}:
        raise ValueError('Invalid media address')
    if not parsed.hostname or parsed.hostname.lower() not in hosts:
        raise ValueError('Media host not approved')
    return parsed


def history(rows, transcripts):
    lines, missing = [], 0
    for row in rows[-30:]:
        role = {'incoming': 'CLIENT', 'outgoing': 'SALES MANAGER'}.get(row.get('direction'), 'UNKNOWN SPEAKER')
        text = str(row.get('body') or '')[:2000]
        if audio(row):
            transcript = transcripts.get(str(row['external_id']))
            if transcript:
                text += '\n[Audio transcript] ' + transcript[:6000]
            else:
                missing += 1
                text += '\n[Audio unavailable: do not infer its contents]'
        elif row.get('media_url'):
            text += '\n[Attachment not analyzed]'
        lines.append({'speaker': role, 'text': text, 'at': str(row.get('happened_at') or '')})
    return lines, missing
