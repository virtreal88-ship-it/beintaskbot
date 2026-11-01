"""Tenant-isolated Beintaskbot Connect transport.

This module deliberately treats Connect as an external transport: the CRM only
stores an encrypted, tenant-owned API key and never reads Connect's database.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import base64
import os
import re
import secrets
import uuid
from contextlib import contextmanager
from urllib.parse import urlencode, urlsplit, urlunsplit
from tenant_ai_dialog_scope import lock_dialog
from datetime import datetime, timezone

import requests

from tenant_platform import TenantPlatformError, _connect, _ensure_schema, _fernet, _json
from tenant_member_store import member
from tenant_connect_direct_identity import (
    direct_identity as _direct_identity,
    canonical_conversation as _canonical_direct_conversation,
)

CONNECT_URL = str(os.environ.get("CONNECT_PUBLIC_URL") or "https://connect.crm.pro.az").rstrip("/")
CONNECT_WEBHOOK_EVENTS = ('message.inbound.received', 'message.outbound.observed')


class ConnectWebhookError(TenantPlatformError):
    """A safe, classified rejection of a signed Connect webhook request."""
    def __init__(self, code: str, message: str, status: int):
        super().__init__(message)
        self.code = code
        self.status = status


def _safe_label(value, limit=180):
    """Keep provider labels display-safe without manufacturing an identity."""
    if not isinstance(value, str):
        return ''
    value = value.strip()
    if not value or any(ord(char) < 32 for char in value):
        return ''
    return value[:limit]


def _conversation_metadata(data, conversation):
    """Normalize only signed Connect/Evolution identity facts.

    Evolution group ids end in ``@g.us``.  Older Connect payloads can omit the
    suffix, in which case its explicit ``isGroup``/``conversationType`` is the
    only safe source; a numeric id alone is never treated as a group title or
    a phone number.
    """
    jid = str(conversation or '').strip()
    explicit_type = str(data.get('conversationType') or data.get('chatType') or '').strip().casefold()
    if jid.endswith('@g.us') or data.get('isGroup') is True or explicit_type in {'group', 'groupchat'}:
        kind = 'group'
    elif jid.endswith(('@s.whatsapp.net', '@c.us', '@lid')) or explicit_type in {'contact', 'personal', 'direct'}:
        kind = 'contact'
    else:
        kind = ''
    if kind == 'group':
        # Evolution has used both groupName and groupSubject.  Connect normally
        # maps the latter to groupName, but accepting both here keeps a signed
        # delivery compatible while Connect is rolling forward.
        display_name = next((_safe_label(data.get(key)) for key in ('groupName', 'groupSubject', 'conversationName', 'chatName')
                             if _safe_label(data.get(key))), '')
        contact_name = False
    else:
        # A Connect contact label is the most useful durable provider fact.
        # Evolution often has only pushName for an inbound direct message;
        # retain it as a lower-priority fallback, but never use an outbound
        # echo's sender name as the customer's name.
        contact_name = any(_safe_label(data.get(key)) for key in ('contactName', 'conversationName'))
        contact_keys = ('contactName', 'conversationName')
        if data.get('fromMe') is not True:
            contact_keys += ('participantName', 'senderName', 'pushName')
        display_name = next((_safe_label(data.get(key)) for key in contact_keys if _safe_label(data.get(key))), '')
    participant_jid = next((_safe_label(data.get(key)) for key in ('participantJid', 'participantAlt', 'senderJid')
                            if _safe_label(data.get(key))), '')
    participant_name = next((_safe_label(data.get(key)) for key in ('participantName', 'senderName', 'pushName')
                             if _safe_label(data.get(key))), '')
    return {'kind': kind, 'display_name': display_name, 'contact_name': contact_name, 'participant_jid': participant_jid,
            'participant_name': participant_name}


def _phone_from_jid(jid):
    value = str(jid or '')
    if '@' in value and not value.endswith(('@s.whatsapp.net', '@c.us')):
        return ''
    candidate = value.split('@', 1)[0]
    return candidate if re.fullmatch(r'[1-9][0-9]{5,18}', candidate) else ''


def _pilot_test_client_role_override(cur, *, tenant, channel_id, conversation, employee,
                                     outbound, coordinated, identity_conflict, occurred_at):
    """Treat a verified employee as the pilot client only under live attestations."""
    if employee is None or outbound or coordinated or identity_conflict:
        return False
    from tenant_ai_pilot import configured_scope, test_client_override_allowed
    scope = configured_scope()
    if (not scope or str(tenant) != scope['tenant_id']
            or str(channel_id) != scope['channel_id']
            or str(conversation) != scope['conversation_id']):
        return False
    return test_client_override_allowed(cur, tenant, channel_id, conversation, occurred_at)


def _upsert_conversation(cur, tenant, channel_id, conversation, metadata):
    """Persist group metadata separately from an individual contact identity."""
    kind = metadata.get('kind') or ''
    display_name = metadata.get('display_name') or ''
    # Keep the established direct-chat phone fallback intact. A group must
    # never inherit the numeric prefix of its JID as a customer phone.
    phone = '' if kind == 'group' else _phone_from_jid(metadata.get('phone_jid') or metadata.get('provider_conversation_id') or conversation)
    contact_name = display_name if kind == 'contact' else ''
    cur.execute('''INSERT INTO saas_connect_conversations
      (tenant_id,connect_channel_id,conversation_id,contact_name,phone,display_name,conversation_kind,last_message_at)
      VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,%s,now())
      ON CONFLICT(tenant_id,connect_channel_id,conversation_id) DO UPDATE
      SET contact_name=CASE WHEN EXCLUDED.contact_name='' THEN saas_connect_conversations.contact_name
                            WHEN %s THEN EXCLUDED.contact_name
                            WHEN saas_connect_conversations.contact_name<>'' THEN saas_connect_conversations.contact_name
                            ELSE EXCLUDED.contact_name END,
          phone=CASE WHEN EXCLUDED.phone<>'' THEN EXCLUDED.phone ELSE saas_connect_conversations.phone END,
          display_name=CASE WHEN EXCLUDED.display_name<>'' THEN EXCLUDED.display_name ELSE saas_connect_conversations.display_name END,
          conversation_kind=CASE WHEN EXCLUDED.conversation_kind<>'' THEN EXCLUDED.conversation_kind ELSE saas_connect_conversations.conversation_kind END,
          last_message_at=now(),updated_at=now()
      RETURNING lead_id''', (tenant, channel_id, conversation, contact_name, phone, display_name, kind,
                             bool(metadata.get('contact_name'))))
    return int(cur.fetchone()['lead_id'])


def _is_ai_message(row):
    raw = (row or {}).get('raw') or {}
    return bool((row or {}).get('author_name') == 'AI Agent' or (isinstance(raw, dict) and raw.get('run_id')))


def _outbound_author(data: dict) -> str:
    """Use signed provider provenance to separate phone sends from CRM sends.

    Evolution's ``fromMe`` only establishes direction; it does not establish
    whether a person or an automated client sent the message. Connect can mark
    known linked-phone events explicitly. Unknown provenance remains an
    operator-compatible fallback for historical integrations.
    """
    if data.get('ai_authored') is True:
        return 'AI Agent'
    if data.get('source') == 'linked_phone':
        return 'Operator'
    if data.get('source') == 'unknown':
        return 'Unknown sender'
    return 'WhatsApp operator'


def _outbound_is_ai(data: dict, existing: dict | None = None) -> bool:
    """Classify AI only on explicit signed/provider or stored run provenance."""
    return (data.get('ai_authored') is True or _is_ai_message(existing))


def _schema(conn) -> None:
    _ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_connect_channels (
          tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
          connect_channel_id UUID NOT NULL, name TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '',
          active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,connect_channel_id))''')
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_connect_conversations (
          tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
          connect_channel_id UUID NOT NULL, conversation_id TEXT NOT NULL, lead_id BIGINT GENERATED BY DEFAULT AS IDENTITY,
          contact_name TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '', ai_enabled BOOLEAN NOT NULL DEFAULT TRUE,
          manual_hold BOOLEAN NOT NULL DEFAULT FALSE, last_message_at TIMESTAMPTZ NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,connect_channel_id,conversation_id), UNIQUE(lead_id))''')
        cur.execute("ALTER TABLE saas_connect_conversations ADD COLUMN IF NOT EXISTS display_name TEXT NOT NULL DEFAULT ''")
        cur.execute("ALTER TABLE saas_connect_conversations ADD COLUMN IF NOT EXISTS conversation_kind TEXT NOT NULL DEFAULT ''")
        # This is a local presentation override only.  It never changes the
        # WhatsApp group subject or an Evolution contact record.
        cur.execute("ALTER TABLE saas_connect_conversations ADD COLUMN IF NOT EXISTS manual_display_name TEXT NOT NULL DEFAULT ''")
        cur.execute("ALTER TABLE saas_connect_conversations ADD COLUMN IF NOT EXISTS avatar_status TEXT NOT NULL DEFAULT ''")
        cur.execute("ALTER TABLE saas_connect_conversations ADD COLUMN IF NOT EXISTS avatar_checked_at TIMESTAMPTZ")
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_connect_direct_identities (
          tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
          connect_channel_id UUID NOT NULL,
          identity_key TEXT NOT NULL,
          canonical_conversation_id TEXT NOT NULL,
          verified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,connect_channel_id,identity_key),
          FOREIGN KEY(tenant_id,connect_channel_id,canonical_conversation_id)
            REFERENCES saas_connect_conversations(tenant_id,connect_channel_id,conversation_id) DEFERRABLE INITIALLY DEFERRED
        )''')
        cur.execute('''CREATE INDEX IF NOT EXISTS saas_connect_direct_canonical_idx
          ON saas_connect_direct_identities(tenant_id,connect_channel_id,canonical_conversation_id)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_connect_participant_display_names (
          tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
          connect_channel_id UUID NOT NULL,
          conversation_id TEXT NOT NULL,
          participant_jid TEXT NOT NULL,
          display_name TEXT NOT NULL,
          updated_by BIGINT NOT NULL,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,connect_channel_id,conversation_id,participant_jid),
          FOREIGN KEY(tenant_id,connect_channel_id,conversation_id)
            REFERENCES saas_connect_conversations(tenant_id,connect_channel_id,conversation_id) ON DELETE CASCADE
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_connect_identity_conflicts (
          tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
          connect_channel_id UUID NOT NULL, conversation_id TEXT NOT NULL,
          event_id TEXT NOT NULL, evidence JSONB NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,connect_channel_id,conversation_id)
        )''')
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_connect_identity_conflict_events (
          tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
          connect_channel_id UUID NOT NULL, conversation_id TEXT NOT NULL,event_id TEXT NOT NULL,
          evidence JSONB NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,connect_channel_id,conversation_id,event_id))''')
        from tenant_connect_dialog_equivalence import schema as equivalence_schema
        equivalence_schema(cur)
        from tenant_connect_owner_identity import schema as owner_identity_schema
        owner_identity_schema(cur)
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_connect_webhook_events (
          tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE, event_id TEXT NOT NULL,
          received_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,event_id))''')


def _owner(profile: dict) -> None:
    if not profile or profile.get('role') not in {'owner', 'admin'}:
        raise TenantPlatformError('Connect bağlantısını yalnız administrator idarə edə bilər.')


@contextmanager
def _tenant_connect_lock(tenant: str):
    """Serialize connection changes across workers without holding a DB transaction."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT pg_try_advisory_lock(hashtextextended(%s, 0)) AS locked', ('beintaskbot-connect:' + tenant,))
            if not cur.fetchone()['locked']:
                conn.commit()
                raise TenantPlatformError('Connect dəyişiklikləri davam edir; bir az sonra yenidən yoxlayın.')
            conn.commit()
        try:
            yield
        finally:
            with conn.cursor() as cur:
                cur.execute('SELECT pg_advisory_unlock(hashtextextended(%s, 0))', ('beintaskbot-connect:' + tenant,))
            conn.commit()


def _public_channel(row: dict) -> dict:
    """Return JSON-safe channel metadata from a PostgreSQL row."""
    return {**row, 'connect_channel_id': str(row['connect_channel_id'])}


def _request(key: str, method: str, path: str, payload: dict | None = None) -> dict:
    # HTTP header values must be Latin-1 encodable. Connect-generated API keys
    # are ASCII; fail clearly if a copied key contains a look-alike character.
    if not key or not key.isascii() or any(ord(char) < 33 or ord(char) == 127 for char in key):
        raise TenantPlatformError('Connect API açarı düzgün deyil. Açarı Connect panelindən yenidən kopyalayın.')
    try:
        response = requests.request(method, CONNECT_URL + path, headers={'x-api-key': key, 'Accept': 'application/json'},
                                    json=payload, timeout=(5, 20), allow_redirects=False)
    except requests.RequestException as exc:
        raise TenantPlatformError('Connect xidməti əlçatan deyil.') from exc
    if response.status_code not in {200, 201, 202, 204}:
        raise TenantPlatformError('Connect API açarı və ya icazələri yoxlanılmadı.')
    if response.status_code == 204:
        return {}
    try:
        data = response.json()
    except ValueError as exc:
        raise TenantPlatformError('Connect düzgün cavab qaytarmadı.') from exc
    return data if isinstance(data, dict) else {}


def status(profile: dict) -> dict:
    tenant = str(profile['tenant_id'])
    with _connect() as conn:
        _schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT status,metadata,secrets,updated_at FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='connect'", (tenant,))
            row = cur.fetchone()
            cur.execute("SELECT connect_channel_id,name,status,active FROM saas_connect_channels WHERE tenant_id=%s::uuid AND status<>'disabled' ORDER BY name", (tenant,))
            channels = [_public_channel(channel) for channel in cur.fetchall()]
    return {'connected': bool(row and row['status'] == 'connected'), 'channels': channels,
            'updated_at': row['updated_at'].isoformat() if row and row.get('updated_at') else None,
            'metadata': (row or {}).get('metadata') or {}}


def _diagnostic_url(value: str) -> str:
    """Omit credentials and query values that might be embedded in a URL."""
    try:
        parsed = urlsplit(str(value or ''))
        host = parsed.hostname or ''
        if parsed.port:
            host += ':' + str(parsed.port)
        return urlunsplit((parsed.scheme, host, parsed.path, '', ''))
    except (TypeError, ValueError):
        return ''


def _diagnostic_error(value) -> str:
    """Expose only Connect's known safe status/code format, never raw errors."""
    match = re.fullmatch(r'subscriber returned ([1-5][0-9]{2})(?: \(([A-Za-z0-9_.-]{1,80})\))?', str(value or ''))
    if match:
        return 'HTTP ' + match.group(1) + (': ' + match.group(2) if match.group(2) else '')
    if 'timeout' in str(value or '').casefold():
        return 'Çatdırılma vaxtı bitdi'
    return 'Çatdırılma uğursuz oldu'


def diagnostics(profile: dict) -> dict:
    """Read tenant-owned Connect diagnostics without changing remote state."""
    _owner(profile)
    tenant = str(profile['tenant_id'])
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status,secrets FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='connect'", (tenant,))
            integration = cur.fetchone()
            cur.execute("SELECT name FROM saas_connect_channels WHERE tenant_id=%s::uuid AND active=TRUE ORDER BY name", (tenant,))
            channel_names = [str(row.get('name') or '') for row in cur.fetchall()]
    if not integration or integration.get('status') != 'connected' or not integration.get('secrets'):
        return {'connected': False, 'subscriptions': []}
    try:
        credentials = json.loads(_fernet().decrypt(bytes(integration['secrets'])).decode())
        key = str(credentials.get('api_key') or '')
        hooks_response = _request(key, 'GET', '/v1/webhooks')
    except Exception as exc:
        if isinstance(exc, TenantPlatformError):
            raise
        raise TenantPlatformError('Connect diaqnostikası oxunmadı.') from exc
    # The encrypted integration record is the only authoritative pointer to
    # the webhook whose HMAC secret Beintaskbot currently verifies.
    current_webhook_id = str(credentials.get('webhook_id') or '')
    if not re.fullmatch(r'[0-9a-fA-F-]{36}', current_webhook_id):
        current_webhook_id = ''
    hooks = hooks_response.get('data', [])
    if not isinstance(hooks, list):
        raise TenantPlatformError('Connect diaqnostikası oxunmadı.')
    subscriptions = []
    for hook in hooks:
        if not isinstance(hook, dict) or not re.fullmatch(r'[0-9a-fA-F-]{36}', str(hook.get('id') or '')):
            continue
        webhook_id = str(hook['id'])
        try:
            summary = _request(key, 'GET', '/v1/webhooks/' + webhook_id + '/diagnostics')
            recent = _request(key, 'GET', '/v1/webhooks/' + webhook_id + '/deliveries').get('data', [])
        except TenantPlatformError:
            summary, recent = {}, []
        recent = recent if isinstance(recent, list) else []
        deliveries = []
        for item in recent[:10]:
            if not isinstance(item, dict):
                continue
            error = item.get('lastError')
            deliveries.append({
                'status': str(item.get('status') or 'unknown'),
                'attempts': item.get('attempts') if isinstance(item.get('attempts'), int) else None,
                'createdAt': str(item.get('createdAt') or ''),
                'deliveredAt': str(item.get('deliveredAt') or ''),
                'error': _diagnostic_error(error) if error else '',
            })
        counts = summary.get('counts') if isinstance(summary.get('counts'), dict) else {}
        last_delivery = next((item.get('deliveredAt') or item.get('createdAt') for item in recent
                              if isinstance(item, dict) and (item.get('deliveredAt') or item.get('createdAt'))), '')
        subscriptions.append({
            'id': webhook_id,
            'isCurrent': bool(current_webhook_id and webhook_id == current_webhook_id),
            # A different ID is only a *candidate* for manual review:
            # other integrations may legitimately own their own hooks.
            'needsReview': bool(current_webhook_id and webhook_id != current_webhook_id
                                and hook.get('active')),
            'active': bool(hook.get('active')),
            'url': _diagnostic_url(hook.get('url') or ''),
            'channels': channel_names,
            'counts': {key: counts.get(key) if isinstance(counts.get(key), int) else None
                       for key in ('pending', 'retrying', 'delivered', 'failed')},
            'knownHttp401Failures': summary.get('knownHttp401Failures') if isinstance(summary.get('knownHttp401Failures'), int) else None,
            'http200Count': summary.get('http200Count') if isinstance(summary.get('http200Count'), int) else None,
            'lastDeliveryAt': str(last_delivery or ''),
            'recentDeliveries': deliveries,
        })
    return {'connected': True, 'subscriptions': subscriptions,
            'currentWebhookId': current_webhook_id,
            'hasVerifiedCurrentWebhook': bool(current_webhook_id and
                any(row['isCurrent'] and row['active'] for row in subscriptions))}



def deactivate_stale_webhooks(profile: dict, webhook_ids: list[str], *, confirmed: bool = False,
                              platform_admin: bool = False) -> dict:
    """Explicit, tenant-owned deactivation only; never delete delivery history.

    No automatic invocation. The owner must separately confirm exact IDs.
    """
    if not platform_admin:
        raise TenantPlatformError('Bu əməliyyat yalnız platform administratoru üçündür.')
    _owner(profile)
    ids = [str(value) for value in webhook_ids]
    if not confirmed or not ids or len(ids) > 20 or len(ids) != len(set(ids)):
        raise TenantPlatformError('Webhook deaktivasiya təsdiqi və unikal ID-lər tələb olunur.')
    if any(not re.fullmatch(r'[0-9a-fA-F-]{36}', value) for value in ids):
        raise TenantPlatformError('Webhook ID düzgün deyil.')
    tenant = str(profile['tenant_id'])
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status,secrets FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='connect'", (tenant,))
            integration = cur.fetchone()
    if not integration or integration.get('status') != 'connected' or not integration.get('secrets'):
        raise TenantPlatformError('Connect bağlantısı təsdiqlənmədi.')
    try:
        credentials = json.loads(_fernet().decrypt(bytes(integration['secrets'])).decode())
        key = str(credentials.get('api_key') or '')
        current_id = str(credentials.get('webhook_id') or '')
    except Exception:
        raise TenantPlatformError('Connect təhlükəsizlik məlumatı oxunmadı.') from None
    if not key or not re.fullmatch(r'[0-9a-fA-F-]{36}', current_id) or current_id in ids:
        raise TenantPlatformError('Cari webhook qorunur; deaktivasiya dayandırıldı.')
    hooks = _request(key, 'GET', '/v1/webhooks').get('data', [])
    if not isinstance(hooks, list):
        raise TenantPlatformError('Webhook siyahısı təsdiqlənmədi.')
    current = next((row for row in hooks if isinstance(row, dict) and str(row.get('id')) == current_id), None)
    if not current or not current.get('active'):
        raise TenantPlatformError('Cari webhook aktiv deyil; deaktivasiya dayandırıldı.')
    expected_url = str(current.get('url') or '').rstrip('/')
    if not expected_url:
        raise TenantPlatformError('Cari webhook ünvanı təsdiqlənmədi.')
    targets = []
    for webhook_id in ids:
        hook = next((row for row in hooks if isinstance(row, dict) and str(row.get('id')) == webhook_id), None)
        if not hook or not hook.get('active') or str(hook.get('url') or '').rstrip('/') != expected_url:
            raise TenantPlatformError('Webhook ünvanı və statusu uyğun gəlmir.')
        stats = _request(key, 'GET', '/v1/webhooks/' + webhook_id + '/diagnostics')
        counts = stats.get('counts') if isinstance(stats, dict) else None
        if not isinstance(counts, dict) or counts.get('pending') != 0 or counts.get('retrying') != 0:
            raise TenantPlatformError('Webhook gözləyən çatdırılmaları var və ya statistika məlum deyil.')
        targets.append(webhook_id)
    # All targets are validated before the first change. A partial failure is
    # reported; never retry silently or touch the authoritative subscription.
    disabled = []
    for webhook_id in targets:
        _request(key, 'PATCH', '/v1/webhooks/' + webhook_id, {'active': False})
        disabled.append(webhook_id)
    return {'disabledWebhookIds': disabled, 'preservedWebhookId': current_id}


def configure(profile: dict, payload: dict, callback_url: str) -> dict:
    _owner(profile)
    with _tenant_connect_lock(str(profile['tenant_id'])):
        return _configure_locked(profile, payload, callback_url)


def _configure_locked(profile: dict, payload: dict, callback_url: str) -> dict:
    _owner(profile)
    tenant = str(profile['tenant_id'])
    key = str(payload.get('api_key') or '').strip()
    requested = {str(value) for value in (payload.get('channel_ids') or []) if str(value).strip()}
    if not key or not requested:
        raise TenantPlatformError('Connect API açarı və ən azı bir WhatsApp kanalı seçin.')
    remote = _request(key, 'GET', '/v1/channels').get('data', [])
    if not isinstance(remote, list):
        raise TenantPlatformError('Connect kanalları oxunmadı.')
    remote_by_id = {str(row.get('id')): row for row in remote if isinstance(row, dict) and row.get('id')}
    if not requested.issubset(remote_by_id):
        raise TenantPlatformError('Seçilmiş kanal bu Connect hesabına aid deyil.')
    with _connect() as conn:
        _schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT secrets FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='connect' AND status='connected'", (tenant,))
            row = cur.fetchone()
            cur.execute('SELECT connect_channel_id FROM saas_connect_channels WHERE tenant_id=%s::uuid AND active=TRUE', (tenant,))
            already_active = {str(item['connect_channel_id']) for item in cur.fetchall()}
            previous = None
            if row and row.get('secrets'):
                try:
                    previous = json.loads(_fernet().decrypt(bytes(row['secrets'])).decode())
                except Exception:
                    # Replacing an unreadable secret would strand an active
                    # Connect subscription and create a second permanently
                    # invalid delivery stream.
                    raise TenantPlatformError('Connect webhook saxlanmış açarı oxunmadı.') from None
    if not already_active.issubset(remote_by_id):
        raise TenantPlatformError('Mövcud aktiv kanal Connect hesabında təsdiqlənmədi; heç bir kanal dəyişdirilmədi.')
    # Saving an additional number is additive. Existing numbers are removed
    # only through the explicit per-channel disconnect action.
    selected = [remote_by_id[channel_id] for channel_id in sorted(requested | already_active)]

    # Reusing the existing tenant-owned subscription preserves the secret that
    # Connect already has. Creating a replacement on every settings save leaves
    # the old subscription active but its secret is no longer available here;
    # each event from that stale hook is then rejected with HTTP 401.
    hook = None
    webhook_secret = ''
    if previous and previous.get('api_key') == key and previous.get('webhook_id') and previous.get('webhook_secret'):
        try:
            hooks = _request(key, 'GET', '/v1/webhooks').get('data', [])
            current = next((row for row in hooks if isinstance(row, dict)
                            and str(row.get('id') or '') == str(previous['webhook_id'])
                            and str(row.get('url') or '').rstrip('/') == callback_url.rstrip('/')), None)
            if current:
                _request(key, 'PATCH', '/v1/webhooks/' + str(previous['webhook_id']),
                         {'active': True, 'events': list(CONNECT_WEBHOOK_EVENTS)})
                hook = current
                webhook_secret = str(previous['webhook_secret'])
        except Exception:
            # Do not silently create a second subscription when the existing
            # one cannot be safely inspected; surface the connection failure.
            raise TenantPlatformError('Connect webhook mövcud abunəliyi yoxlanmadı.') from None
    if hook is None:
        hook = _request(key, 'POST', '/v1/webhooks', {'url': callback_url, 'events': list(CONNECT_WEBHOOK_EVENTS)})
        # Connect creates the secret and returns it once. Use it for HMAC verification.
        webhook_secret = str(hook.get('secret') or '')
        if not webhook_secret:
            raise TenantPlatformError('Connect webhook təhlükəsizlik açarını qaytarmadı.')
    old_id = str((previous or {}).get('webhook_id') or '')
    old_key = str((previous or {}).get('api_key') or '')
    encrypted = _fernet().encrypt(_json({'api_key': key, 'webhook_secret': webhook_secret, 'webhook_id': hook.get('id'), 'local_nonce': secrets.token_urlsafe(32)}).encode())
    with _connect() as conn:
        _schema(conn)
        with conn.cursor() as cur:
            cur.execute('''INSERT INTO saas_tenant_integrations(tenant_id,provider,status,account_domain,secrets,metadata,connected_at)
              VALUES(%s::uuid,'connect','connected',%s,%s,%s::jsonb,now())
              ON CONFLICT(tenant_id,provider) DO UPDATE SET status='connected',account_domain=EXCLUDED.account_domain,secrets=EXCLUDED.secrets,metadata=EXCLUDED.metadata,connected_at=now(),updated_at=now()''',
                        (tenant, CONNECT_URL, encrypted, _json({'webhook_id': hook.get('id'), 'channel_ids': sorted(requested | already_active)})))
            for channel in selected:
                cur.execute('''INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name,status,active)
                  VALUES(%s::uuid,%s::uuid,%s,%s,TRUE) ON CONFLICT(tenant_id,connect_channel_id) DO UPDATE
                  SET name=EXCLUDED.name,status=EXCLUDED.status,active=TRUE,updated_at=now()''',
                            (tenant, str(channel['id']), str(channel.get('name') or ''), str(channel.get('status') or '')))
            # Reconnect/configuration is a durable retry point for old imported
            # conversations. This assigns only unique enabled in-scope agents;
            # the reconciler leaves every result paused and queues no AI work.
            cur.execute("SELECT to_regclass('saas_ai_agents') IS NOT NULL AS available")
            if (cur.fetchone() or {}).get('available'):
                from tenant_ai_agent_connect import reconcile_tenant_connect_assignments
                reconcile_tenant_connect_assignments(cur, tenant, requested | already_active)
        conn.commit()
    if old_id and old_id != str(hook.get('id') or '') and old_key:
        try:
            # The saved integration record proves ownership of this exact
            # previous subscription. Disable only that id after the replacement
            # is persisted; never enumerate or alter other subscribers.
            _request(old_key, 'PATCH', '/v1/webhooks/' + old_id, {'active': False})
        except Exception:
            pass
    return status(profile)


def channels(profile: dict, api_key: str) -> list[dict]:
    _owner(profile)
    key = str(api_key or '').strip()
    if not key:
        key = str(_stored_connect_credentials(profile)['api_key'])
    rows = _request(key, 'GET', '/v1/channels').get('data', [])
    if not isinstance(rows, list):
        raise TenantPlatformError('Connect kanalları oxunmadı.')
    tenant = str(profile['tenant_id'])
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT connect_channel_id FROM saas_connect_channels WHERE tenant_id=%s::uuid', (tenant,))
            linked_ids = {str(row['connect_channel_id']) for row in cur.fetchall()}
            cur.execute('SELECT connect_channel_id FROM saas_connect_channels WHERE tenant_id=%s::uuid AND active=TRUE', (tenant,))
            active_ids = {str(row['connect_channel_id']) for row in cur.fetchall()}
    return [{'id': str(row.get('id') or ''), 'name': str(row.get('name') or ''), 'status': str(row.get('status') or ''),
             'phone': str(row.get('phone') or ''), 'phoneConflict': bool(row.get('phoneConflict')),
             'duplicateChannelId': str(row.get('duplicateChannelId') or ''),
             'active': str(row.get('id') or '') in active_ids, 'linked': str(row.get('id') or '') in linked_ids}
            for row in rows if isinstance(row, dict) and row.get('id')]


def disconnect(profile: dict) -> dict:
    _owner(profile)
    raise TenantPlatformError('Hər WhatsApp nömrəsini ayrıca ayırın; şirkət bağlantısı və webhook saxlanılır.')


def _stored_connect_credentials(profile: dict) -> dict:
    tenant = str(profile['tenant_id'])
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status,secrets FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='connect'", (tenant,))
            row = cur.fetchone()
    if not row or row.get('status') != 'connected' or not row.get('secrets'):
        raise TenantPlatformError('Connect bağlantısı aktiv deyil.')
    try:
        credentials = json.loads(_fernet().decrypt(bytes(row['secrets'])).decode())
    except Exception:
        raise TenantPlatformError('Connect təhlükəsizlik məlumatı oxunmadı.') from None
    if not credentials.get('api_key'):
        raise TenantPlatformError('Connect API açarı saxlanmayıb.')
    return credentials


def disconnect_channel(profile: dict, channel_id: str) -> dict:
    _owner(profile)
    with _tenant_connect_lock(str(profile['tenant_id'])):
        return _disconnect_channel_locked(profile, channel_id)


def _disconnect_channel_locked(profile: dict, channel_id: str) -> dict:
    _owner(profile)
    if not re.fullmatch(r'[0-9a-fA-F-]{36}', str(channel_id or '')):
        raise TenantPlatformError('WhatsApp kanal ID-si düzgün deyil.')
    tenant = str(profile['tenant_id'])
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT active FROM saas_connect_channels WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid', (tenant, channel_id))
            local = cur.fetchone()
    if not local:
        raise TenantPlatformError('WhatsApp kanalı bu şirkətə aid deyil.')
    if local.get('active'):
        credentials = _stored_connect_credentials(profile)
        _request(str(credentials['api_key']), 'POST', '/v1/channels/' + channel_id + '/disconnect')
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('''UPDATE saas_connect_channels SET active=FALSE,status='disconnected',updated_at=now()
                               WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid''', (tenant, channel_id))
            conn.commit()
    return status(profile)


def channel_status(profile: dict, channel_id: str, *, reconnect: bool = False,
                   callback_url: str = '') -> dict:
    _owner(profile)
    if not re.fullmatch(r'[0-9a-fA-F-]{36}', str(channel_id or '')):
        raise TenantPlatformError('WhatsApp kanal ID-si düzgün deyil.')
    tenant = str(profile['tenant_id'])
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT 1 FROM saas_connect_channels WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid', (tenant, channel_id))
            if not cur.fetchone():
                raise TenantPlatformError('WhatsApp kanalı bu şirkətə aid deyil.')
    credentials = _stored_connect_credentials(profile)
    if reconnect:
        configure(profile, {'api_key': str(credentials['api_key']), 'channel_ids': [channel_id]}, callback_url)
    current = _request(str(credentials['api_key']), 'GET', '/v1/channels/' + channel_id + '/status')
    result = {'id': channel_id, 'status': str(current.get('status') or 'unknown'),
              'phone': str(current.get('phone') or '')}
    if reconnect and result['status'] != 'connected':
        qr = _request(str(credentials['api_key']), 'GET', '/v1/channels/' + channel_id + '/qr')
        result['qr'] = str(qr.get('code') or '')
    return result


def sync_webhook_subscriptions() -> int:
    """Upgrade only Beintaskbot-owned Connect webhooks after deployment.

    This keeps existing company installations working without asking an owner
    to disconnect/reconnect a channel. The API key and webhook id remain
    tenant-local and are never logged or returned.
    """
    updated = 0
    with _connect() as conn:
        _schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT tenant_id,secrets FROM saas_tenant_integrations WHERE provider='connect' AND status='connected' AND secrets IS NOT NULL")
            rows = cur.fetchall()
    for row in rows:
        try:
            credentials = json.loads(_fernet().decrypt(bytes(row['secrets'])).decode())
            webhook_id = str(credentials.get('webhook_id') or '')
            api_key = str(credentials.get('api_key') or '')
            if not webhook_id or not api_key:
                continue
            _request(api_key, 'PATCH', '/v1/webhooks/' + webhook_id, {'events': list(CONNECT_WEBHOOK_EVENTS)})
            updated += 1
        except Exception:
            # A single stale external webhook must never prevent the HTTP API
            # or AI worker from starting for the other companies.
            continue
    return updated


def _credentials(cur, tenant: str) -> dict | None:
    cur.execute("SELECT secrets FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='connect' AND status='connected' FOR SHARE", (tenant,))
    row = cur.fetchone()
    if not row or not row.get('secrets'):
        return None
    try:
        data = json.loads(_fernet().decrypt(bytes(row['secrets'])).decode())
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _verify_signature(secret: str, raw: bytes, signature: str) -> bool:
    actual = 'sha256=' + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return bool(signature) and hmac.compare_digest(actual, signature)


def ingest(raw: bytes, signature: str, event: dict) -> bool:
    """Retry only transactions PostgreSQL confirms it rolled back.

    Ingestion persists observations/queues and never performs external sends or
    provider calls. An uncertain connection outcome is deliberately not retried.
    """
    from psycopg.errors import DeadlockDetected, SerializationFailure
    import time
    for attempt in range(3):
        try:
            return _ingest_once(raw, signature, event)
        except (DeadlockDetected, SerializationFailure):
            if attempt == 2:
                raise
            time.sleep(0.05 * (attempt + 1))


def _ingest_once(raw: bytes, signature: str, event: dict) -> bool:
    event_id, data = str(event.get('id') or ''), event.get('data') or {}
    event_type = str(event.get('type') or '')
    if event_type not in CONNECT_WEBHOOK_EVENTS or not event_id or not isinstance(data, dict):
        raise ConnectWebhookError('invalid_event', 'Connect webhook formatı düzgün deyil.', 400)
    channel_id, conversation = str(data.get('channelId') or ''), str(data.get('conversationId') or '')
    if not channel_id or not conversation:
        raise ConnectWebhookError('missing_route', 'Connect webhook kanal məlumatı yoxdur.', 400)
    metadata = _conversation_metadata(data, conversation)
    accepted = False
    queued_payload = None
    with _connect() as conn:
        # AI schema initialization also prepares Connect tables. Never hold
        # Connect DDL locks while waiting for the AI schema's process lock.
        from tenant_ai_agents_schema import ensure_schema
        ensure_schema(conn)
        from tenant_knowledge_live_queue import create_schema as create_live_queue_schema
        create_live_queue_schema(conn)
        _schema(conn)
        # Usually installed by tenant_ai_agent_startup.prepare_database. The
        # HTTP listener starts before the background worker's startup task, so
        # close that first-request race as well. This is additive/idempotent;
        # if it fails, the CRM receipt path remains available and AI admission
        # below is isolated behind its own savepoint.
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('saas_ai_pilot_followups') IS NOT NULL AS ready")
            pilot_case_schema_ready=bool((cur.fetchone() or {}).get('ready'))
        if not pilot_case_schema_ready:
            with conn.cursor() as cur:
                cur.execute('SAVEPOINT pilot_case_schema_setup')
                try:
                    from tenant_ai_agent_startup import ensure_pilot_case_schema
                    ensure_pilot_case_schema(conn)
                except Exception:
                    cur.execute('ROLLBACK TO SAVEPOINT pilot_case_schema_setup')
                    import logging
                    logging.getLogger(__name__).exception(
                        'Pilot CASE schema setup failed; continuing with CRM-only ingress')
                finally:
                    cur.execute('RELEASE SAVEPOINT pilot_case_schema_setup')
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('saas_crm_notice_events') IS NOT NULL AND to_regclass('saas_crm_notice_deliveries') IS NOT NULL AS ready")
            notices_ready = (cur.fetchone() or {}).get('ready')
        if not notices_ready:
            from tenant_crm_notice_schema import ensure_schema as ensure_notice_schema
            ensure_notice_schema(conn)
        conn.commit()  # Release all DDL locks before waiting on a dialogue/group lock.
        with conn.cursor() as cur:
            cur.execute("SELECT tenant_id,secrets FROM saas_tenant_integrations WHERE provider='connect' AND status='connected' AND secrets IS NOT NULL")
            matches = []
            for row in cur.fetchall():
                try:
                    secret = json.loads(_fernet().decrypt(bytes(row['secrets'])).decode()).get('webhook_secret', '')
                except Exception:
                    continue
                if _verify_signature(secret, raw, signature):
                    matches.append(str(row['tenant_id']))
            if len(matches) != 1:
                raise ConnectWebhookError('invalid_signature', 'Connect webhook imzası etibarsızdır.', 401)
            tenant = matches[0]
            cur.execute('SELECT 1 FROM saas_connect_channels WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND active=TRUE', (tenant, channel_id))
            if not cur.fetchone():
                return False
            data={k:v for k,v in data.items() if k not in {
                '_crm_provider_conversation_id','_crm_owner_assertion_id','pilot_test_role_override'}}
            original_conversation = conversation
            conversation, identity_conflict = _canonical_direct_conversation(
                cur, tenant, channel_id, conversation, data, metadata)
            provider_conversation=conversation
            if not identity_conflict and metadata.get('kind')!='group' and _direct_identity(conversation):
                from tenant_connect_owner_identity import route as owner_route
                conversation,identity_conflict=owner_route(cur,tenant,channel_id,conversation,data,metadata)
                if metadata.get('owner_assertion_id'):
                    data={**data,'_crm_owner_assertion_id':metadata['owner_assertion_id']}
            if identity_conflict:
                # Keep the receipt readable, but never use an ambiguous pair
                # to redirect history or admit AI work.
                data = {**data, 'conversationIdentityConflict': True}
                from tenant_connect_direct_identity import record_conflict
                record_conflict(cur, tenant, channel_id, conversation, original_conversation, data, event_id)
            if conversation != original_conversation:
                # Preserve both signed provider facts for recovery/audit while
                # routing all new inbound and outbound receipts to one route.
                data = {**data, 'originalConversationId': data.get('originalConversationId') or original_conversation,
                        '_crm_provider_conversation_id':original_conversation,'conversationId': conversation}
            lock_dialog(cur, tenant, 'connect', channel_id, conversation)
            cur.execute('INSERT INTO saas_connect_webhook_events(tenant_id,event_id) VALUES(%s::uuid,%s) ON CONFLICT DO NOTHING RETURNING event_id', (tenant,event_id))
            inserted_event = bool(cur.fetchone())
            outbound = event_type == 'message.outbound.observed' or data.get('fromMe') is True
            independent_consultation_receipt=False
            # Only this verified ingestion boundary can attest provider receipts.
            data={k:v for k,v in data.items() if k!='_crm_signed_outbound_receipt'}
            if outbound and not identity_conflict and data.get('fromMe') is not False:
                data={**data,'_crm_signed_outbound_receipt':True}
                cur.execute('SAVEPOINT independent_consultation_receipt')
                try:
                    from tenant_dialog_consultations import observe_outbound_receipt
                    independent_consultation_receipt=observe_outbound_receipt(cur,tenant,channel_id,conversation,data)
                    cur.execute('RELEASE SAVEPOINT independent_consultation_receipt')
                except Exception:
                    cur.execute('ROLLBACK TO SAVEPOINT independent_consultation_receipt')
                    cur.execute('RELEASE SAVEPOINT independent_consultation_receipt')
            from tenant_ai_group_coordination import group_jid, known_ai_origin, verified_employee, stop as stop_group
            coordinated = bool(group_jid(conversation))
            ai_origin = coordinated and known_ai_origin(cur,tenant,data)
            consultation_origin=independent_consultation_receipt
            if coordinated and outbound and not ai_origin and not consultation_origin:
                from tenant_consultation_group import known_outbound
                consultation_origin=known_outbound(cur,tenant,channel_id,conversation,data)
            uncertain_origin = coordinated and data.get('authorshipUncertain') is True and not ai_origin and not consultation_origin
            employee = verified_employee(cur,tenant,data) if coordinated and not ai_origin and not consultation_origin and not uncertain_origin else None
            if not coordinated and not outbound and _direct_identity(conversation):
                from tenant_ai_group_coordination import EmployeeIdentityConflict
                try:
                    employee = verified_employee(cur,tenant,{**data,'participantJid':provider_conversation},
                        direct_aliases=metadata.get('direct_aliases', ()))
                except EmployeeIdentityConflict:
                    identity_conflict = True
                    data = {**data, 'conversationIdentityConflict': True}
                    from tenant_connect_direct_identity import record_conflict
                    record_conflict(cur, tenant, channel_id, conversation, original_conversation, data, event_id)
            role_override = _pilot_test_client_role_override(
                cur, tenant=tenant, channel_id=channel_id, conversation=conversation,
                employee=employee, outbound=outbound, coordinated=coordinated,
                identity_conflict=identity_conflict,
                occurred_at=event.get('occurred_at'))
            if role_override:
                # Keep the verified employee identity in the immutable source
                # record. The explicit pilot marker is an audited processing
                # role and never changes the signed inbound direction.
                data['pilot_test_role_override'] = {
                    'role': 'test_client_override',
                    'verified_employee_id': employee,
                    'scope': 'configured_scoped_live_dialog'}
                employee = None
            data = {key: value for key, value in data.items() if key != 'verified_employee_id'}
            if employee is not None:
                data['verified_employee_id'] = employee
            elif role_override:
                data['verified_employee_id'] = int(data['pilot_test_role_override']['verified_employee_id'])

            if coordinated and (ai_origin or employee is not None or uncertain_origin):
                # A repeated employee event must not undo a later explicit Resume.
                cur.execute('SELECT 1 FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id=%s AND channel=%s',
                            (tenant,'connect:'+str(data.get('id') or event_id),'connect:'+channel_id))
                if not inserted_event or cur.fetchone():
                    conn.commit()
                    return True
            control_replayed=False
            if coordinated and (employee is not None or uncertain_origin):
                from tenant_ai_group_coordination import claim_control_event
                if not claim_control_event(cur,tenant,channel_id,conversation,data.get('externalMessageId')):
                    employee=None
                    uncertain_origin=False
                    control_replayed=True  # Persist history without replaying control.
            if uncertain_origin:
                from tenant_ai_group_coordination import read as read_group
                if read_group(cur,tenant,conversation).get('state')!='human':
                    stop_group(cur,tenant,conversation,state='paused',reason='group_authorship_unresolved')

            if ai_origin:
                data={**data,'ai_authored':True}

            message_inserted=False
            if outbound:
                # Direction comes from the signed event/fromMe fact, never the
                # message text.  A known accepted AI row stays AI even when
                # Evolution echoes it back through the outbound webhook.
                if not inserted_event:
                    conn.commit()
                    return True
                lead_id = _upsert_conversation(cur, tenant, channel_id, conversation, metadata)
                occurred = str(event.get('occurred_at') or datetime.now(timezone.utc).isoformat())
                external = 'connect:' + str(data.get('id') or event_id)
                cur.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,author_name,body,message_type,media_url,happened_at,raw)
                  VALUES(%s,%s::uuid,%s,%s,'outgoing',%s,%s,%s,%s,'',%s::timestamptz,%s::jsonb)
                  ON CONFLICT(tenant_id,external_id) DO NOTHING
                  RETURNING id,author_name,raw,kommo_lead_id,channel''',
                            (uuid.uuid4(),tenant,lead_id,external,'connect:'+channel_id,_outbound_author(data),str(data.get('text') or ''),str(data.get('type') or 'text'),occurred,_json(data)))
                stored = cur.fetchone()
                message_inserted = stored is not None
                if not message_inserted:
                    cur.execute('''SELECT id,author_name,raw,kommo_lead_id,channel FROM saas_crm_messages
                      WHERE tenant_id=%s::uuid AND external_id=%s''', (tenant,external))
                    stored = cur.fetchone()
                from tenant_connect_dialog_equivalence import lead_members
                if stored and (stored.get('channel') != 'connect:' + channel_id or
                               stored.get('kommo_lead_id') not in lead_members(cur,tenant,channel_id,lead_id)):
                    raise ConnectWebhookError('message_scope_conflict', 'Connect message scope mismatch.', 409)
                # A signed provider echo can fill the canonical provider ID for
                # an earlier locally recorded Connect send. Preserve all local
                # authorship and delivery metadata; never infer IDs from text.
                if stored and not message_inserted:
                    facts={key:data[key] for key in ('externalMessageId','quotedExternalMessageId','id','originClientMessageId','clientMessageId')
                           if isinstance(data.get(key),str) and re.fullmatch(r'[A-Za-z0-9_-]{5,200}',data[key])}
                    if facts:
                        facts['_crm_signed_outbound_receipt']=data.get('_crm_signed_outbound_receipt') is True
                        if data.get('_crm_signed_outbound_receipt') is True:
                            facts['fromMe']=True
                        cur.execute("UPDATE saas_crm_messages SET raw=COALESCE(raw,'{}'::jsonb)||%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",
                                    (_json(facts),tenant,str(stored['id'])))
                if (stored and not coordinated and _direct_identity(conversation)
                        and not independent_consultation_receipt
                        and data.get('fromMe') is not False and not _outbound_is_ai(data, stored)
                        and data.get('conversationIdentityConflict') is not True):
                    # An outbound observation can be the first event that
                    # creates a direct route. Unknown authorship must not turn
                    # on replies, but it still gets its unique channel-scoped
                    # owner recorded in a paused placeholder.
                    from tenant_ai_agent_connect import bind_unassigned_direct_agent
                    bind_unassigned_direct_agent(cur,tenant,channel_id,conversation)
                # A malformed outbound event with ``fromMe:false`` is kept as
                # an observable outbound record, but cannot take over a chat.
                if message_inserted and not independent_consultation_receipt and data.get('fromMe') is not False and data.get('source') != 'unknown' and not _outbound_is_ai(data, stored) and not ai_origin and (not coordinated or employee is not None):
                    from tenant_ai_agent_connect import mark_operator_reply
                    mark_operator_reply(tenant, lead_id, channel_id, conversation, connection=conn, actor=employee)
                    if stored and stored.get('id') and data.get('conversationIdentityConflict') is not True:
                        from tenant_knowledge_live_queue import enqueue as enqueue_case_extraction
                        enqueue_case_extraction(cur,tenant,channel_id,conversation,lead_id,
                            str(stored['id']),actor_id=employee)
                conn.commit()
                return True
            # A legacy inbound delivery may have committed the durable CRM
            # copy just before a transient queue failure. Do not lose the AI
            # job merely because Connect retries the same signed event.
            if not inserted_event:
                cur.execute('''SELECT lead_id FROM saas_connect_conversations
                  WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s''',
                            (tenant, channel_id, conversation))
                previous = cur.fetchone()
                if not previous:
                    return False
                accepted = True
                queued_payload = (tenant, channel_id, conversation, int(previous['lead_id']),
                                  str(data.get('id') or event_id), event_id,
                                  str(data.get('text') or ''), str(data.get('type') or 'text'))
                # The common, idempotent enqueue below
                # restores a missing job without duplicating CRM history.
                continue_processing = False
            else:
                continue_processing = True
            if not continue_processing:
                pass
            else:
                lead_id = _upsert_conversation(cur, tenant, channel_id, conversation, metadata)
                # Create/attach the native CRM contact independently of AI.
                # Only a personal, provider-verified WhatsApp JID is an
                # identity; group JIDs, names, and unknown IDs never create a
                # person record.  This shares the webhook transaction so
                # retries cannot leave a contact without its message.
                if metadata.get('kind') == 'contact' and not identity_conflict:
                    from tenant_crm_native_contacts import upsert_whatsapp_contact
                    upsert_whatsapp_contact(cur, tenant, channel_id, provider_conversation,
                        _phone_from_jid(metadata.get('phone_jid') or provider_conversation), metadata.get('display_name') or '',
                        str(data.get('id') or event_id))
                occurred = str(event.get('occurred_at') or datetime.now(timezone.utc).isoformat())
                is_group = metadata.get('kind') == 'group'
                phone = '' if is_group else _phone_from_jid(metadata.get('phone_jid') or provider_conversation)
                author_name = (metadata.get('participant_name') or _phone_from_jid(metadata.get('participant_jid'))
                               or phone)
                deal_name = metadata.get('display_name') or author_name
                cur.execute('''INSERT INTO saas_crm_deals(tenant_id,kommo_lead_id,name,contact_name,phone,channel,last_message,last_message_at,source_updated_at,raw)
              VALUES(%s::uuid,%s,%s,%s,%s,%s,%s,%s::timestamptz,%s::timestamptz,%s::jsonb)
              ON CONFLICT(tenant_id,kommo_lead_id) DO UPDATE SET contact_name=EXCLUDED.contact_name,
              phone=CASE WHEN EXCLUDED.phone<>'' THEN EXCLUDED.phone ELSE saas_crm_deals.phone END,
              channel=EXCLUDED.channel,last_message=EXCLUDED.last_message,last_message_at=EXCLUDED.last_message_at,source_updated_at=EXCLUDED.source_updated_at,raw=EXCLUDED.raw,synced_at=now()''',
                            (tenant,lead_id,deal_name,deal_name,phone,'connect:'+channel_id,str(data.get('text') or ''),occurred,occurred,_json({'source':'connect','channel_id':channel_id,'conversation_id':conversation,'conversation_kind':metadata.get('kind') or ''})))
                external = 'connect:' + str(data.get('id') or event_id)
                cur.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,author_name,body,message_type,media_url,happened_at,raw)
              VALUES(%s,%s::uuid,%s,%s,'incoming',%s,%s,%s,%s,%s,%s::timestamptz,%s::jsonb) ON CONFLICT(tenant_id,external_id) DO NOTHING RETURNING id''',
                            # Connect does not currently provide a safe
                            # downloadable media URL in its signed event.  Do
                            # not store a fake non-empty placeholder: that
                            # would make every text look like an attachment and
                            # can incorrectly affect later AI context.
                            (uuid.uuid4(),tenant,lead_id,external,'connect:'+channel_id,author_name,str(data.get('text') or ''),str(data.get('type') or 'text'),'',occurred,_json(data)))
                message_inserted=bool(cur.fetchone())
                accepted = True
                queued_payload = (tenant, channel_id, conversation, lead_id, str(data.get('id') or event_id), event_id,
                                  str(data.get('text') or ''), str(data.get('type') or 'text'))
            # The CRM copy, dedup receipt and AI admission commit together.
            # A failed queue write rolls back the webhook, so Connect can retry.
            if accepted and queued_payload:
                from tenant_connect_dialog_equivalence import lead_members
                cur.execute("SELECT id,kommo_lead_id FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id=%s AND kommo_lead_id=ANY(%s::bigint[]) AND channel=%s AND direction='incoming'",
                            (tenant, 'connect:' + queued_payload[4], lead_members(cur,tenant,channel_id,queued_payload[3]), 'connect:' + channel_id))
                source_message=cur.fetchone()
                if not source_message:
                    raise ConnectWebhookError('message_scope_conflict', 'Connect message scope mismatch.', 409)
                if source_message.get('kommo_lead_id',queued_payload[3]) != queued_payload[3]:
                    # A pre-merge replay retains its original receipt and must
                    # not create new AI/case work on the canonical route.
                    conn.commit()
                    return accepted
            independent_consultation_reply=False
            if not outbound and not ai_origin and data.get('type') in {'text','image','video','audio','voice','document','ptt','voice_message','photo','file'}:
                cur.execute('SAVEPOINT dialog_consultation_reply')
                try:
                    from tenant_dialog_consultations import record_reply,known_reply
                    linked=record_reply(cur,tenant,channel_id,conversation,str(data.get('id') or event_id),
                        str(data.get('text') or data.get('caption') or ''),data.get('quotedExternalMessageId'),
                        str(metadata.get('participant_name') or _phone_from_jid(metadata.get('participant_jid')) or metadata.get('display_name') or metadata.get('participant_jid') or conversation),
                        event.get('occurred_at'),message_type=str(data.get('type') or 'text'),
                        source_external_id='connect:'+str(data.get('id') or event_id))
                    independent_consultation_reply=known_reply(cur,tenant,channel_id,conversation,str(data.get('id') or event_id))
                    cur.execute('RELEASE SAVEPOINT dialog_consultation_reply')
                except Exception:
                    cur.execute('ROLLBACK TO SAVEPOINT dialog_consultation_reply')
                    cur.execute('RELEASE SAVEPOINT dialog_consultation_reply')
                    import logging
                    logging.getLogger(__name__).warning('Dialogue consultation correlation deferred; original evidence preserved')
            if independent_consultation_reply:
                # The original CRM evidence and private reply notification are
                # durable. This is developer evidence, not a new customer turn
                # for destination AI, extraction, learning or simulation.
                conn.commit()
                return accepted
            if (not outbound and message_inserted and coordinated and not ai_origin and data.get('type')=='text'
                    and ('CASE:' in str(data.get('text') or '').upper() or data.get('quotedExternalMessageId'))):
                # Optional consultation correlation must never alter the normal
                # webhook admission or turn group text into an instruction.
                cur.execute('SAVEPOINT consultation_reply')
                try:
                    cur.execute("SELECT to_regclass('saas_consultation_group_deliveries') IS NOT NULL AS ready")
                    if (cur.fetchone() or {}).get('ready'):
                        from tenant_consultation_group import record_group_reply
                        consultation_id=record_group_reply(cur,tenant,channel_id,conversation,str(data.get('id') or event_id),
                          str(data.get('text') or ''),data.get('quotedExternalMessageId'))
                        if consultation_id:
                            from tenant_consultation_group import enqueue_pilot_customer_followup
                            enqueue_pilot_customer_followup(cur,tenant,channel_id,conversation,
                                str(data.get('id') or event_id))
                    cur.execute('RELEASE SAVEPOINT consultation_reply')
                except Exception:
                    cur.execute('ROLLBACK TO SAVEPOINT consultation_reply')
                    cur.execute('RELEASE SAVEPOINT consultation_reply')
                    import logging
                    logging.getLogger(__name__).warning('Consultation reply was not linked; original message was preserved')
            if (not outbound and message_inserted and not ai_origin and employee is None
                    and not uncertain_origin and not control_replayed):
                # Reuse the existing SaaS CRM notice outbox, push subscriptions and worker.
                # Its schema was prepared before the dialogue transaction;
                # webhook delivery remains idempotent by persisted message ID.
                from tenant_crm_notice_store import plan_connect_message
                plan_connect_message(cur,tenant,channel_id,conversation,queued_payload[3],
                                     'connect:'+queued_payload[4],data)
            if not coordinated and employee is not None:
                from tenant_ai_agent_connect import mark_operator_reply
                mark_operator_reply(tenant, queued_payload[3], channel_id, conversation, connection=conn, actor=employee)
                conn.commit()
                return accepted
            if coordinated and (ai_origin or employee is not None or uncertain_origin or control_replayed):
                if employee is not None:
                    stop_group(cur,tenant,conversation,reason='verified_employee_reply',actor=employee)
                    from tenant_knowledge_live_queue import enqueue as enqueue_case_extraction
                    enqueue_case_extraction(cur,tenant,channel_id,conversation,queued_payload[3],
                        str(source_message['id']),actor_id=employee)
                conn.commit()
                return True
            from tenant_connect_identity import conflicted
            if identity_conflict or conflicted(cur, tenant, channel_id, conversation):
                # Preserve the signed event and CRM message, but do not feed a
                # split/contradictory history into any paid AI path.
                conn.commit()
                return accepted
            from tenant_knowledge_live_queue import enqueue as enqueue_case_extraction
            if message_inserted:
                enqueue_case_extraction(cur,tenant,channel_id,conversation,queued_payload[3],str(source_message['id']))
            from tenant_ai_agent_connect import enqueue
            # AI admission is an optional hook on the durable Connect receipt.
            # A pilot-only schema/queue failure must not roll back the verified
            # provider event and its CRM message. Keep every AI side effect
            # inside a savepoint and fail closed if it cannot be committed.
            ai_admission_failed = False
            cur.execute('SAVEPOINT connect_ai_admission')
            try:
                enqueue(queued_payload[0], channel_id=queued_payload[1], conversation_id=queued_payload[2],
                        lead_id=queued_payload[3], message_id=queued_payload[4], event_id=queued_payload[5],
                        text=queued_payload[6], message_type=queued_payload[7],
                        is_group=metadata.get('kind') == 'group', provider_message_id=data.get('externalMessageId'),
                        quoted_message_id=data.get('quotedExternalMessageId'),
                        source_role_override='test_client_override' if role_override else None,
                        connection=conn)
            except Exception:
                cur.execute('ROLLBACK TO SAVEPOINT connect_ai_admission')
                ai_admission_failed = True
                import logging
                logging.getLogger(__name__).exception(
                    'Connect AI admission failed; durable CRM receipt preserved')
            finally:
                cur.execute('RELEASE SAVEPOINT connect_ai_admission')
            # Simulation admits only the first durable inbound delivery.
            # Webhook retries may repair the Active queue above but must
            # never backfill Simulation or replay a paid virtual run.
            from tenant_ai_agent_runtime_flags import enabled as runtime_flag
            if inserted_event and not ai_admission_failed and runtime_flag('AI_SIMULATION_ENABLED'):
                from tenant_ai_simulation_schema import ensure_schema as ensure_simulation_schema
                from tenant_ai_simulation_engine import enqueue_inbound
                ensure_simulation_schema(conn)
                enqueue_inbound(cur,tenant_id=tenant,channel_id=channel_id,
                    conversation_id=conversation,lead_id=queued_payload[3],
                    source_message_id=str(source_message['id']),
                    source_event_id=queued_payload[4],occurred_at=event.get('occurred_at'))
            conn.commit()
    return accepted


def connect_route(cur, tenant: str, lead_id: int, *, historical_read: bool = False) -> dict | None:
    active_gate=(" AND (ch.active=TRUE OR ch.status IN ('disconnected','disabled'))"
                 if historical_read else ''' AND ch.active=TRUE AND NOT EXISTS(SELECT 1 FROM saas_connect_dialog_links dl
                   WHERE dl.tenant_id=c.tenant_id AND dl.channel_id=c.connect_channel_id
                     AND dl.source_id=c.conversation_id AND dl.active)''')
    cur.execute('''SELECT c.connect_channel_id,c.conversation_id,i.secrets FROM saas_connect_conversations c
      JOIN saas_tenant_integrations i ON i.tenant_id=c.tenant_id AND i.provider='connect' AND i.status='connected'
      JOIN saas_connect_channels ch ON ch.tenant_id=c.tenant_id AND ch.connect_channel_id=c.connect_channel_id'''+active_gate+'''
      WHERE c.tenant_id=%s::uuid AND c.lead_id=%s FOR SHARE''', (tenant,lead_id))
    row = cur.fetchone()
    if not row: return None
    try: secret = json.loads(_fernet().decrypt(bytes(row['secrets'])).decode())
    except Exception as exc: raise TenantPlatformError('Connect açarı oxunmadı.') from exc
    return {'tenant_id':str(tenant),'provider':'connect','channel_id':str(row['connect_channel_id']),'conversation_id':str(row['conversation_id']),'api_key':str(secret.get('api_key') or '')}


QUOTED_ONCE_TENANT='e09a5c24-093f-4141-992d-532052e00c90'
QUOTED_ONCE_CHANNEL='96863d75-d272-4c93-a25e-ffb2b16ecae3'

def quoted_once_scope(tenant,channel):
    return str(tenant)==QUOTED_ONCE_TENANT and str(channel)==QUOTED_ONCE_CHANNEL


def send(route: dict, text: str, client_message_id: str, *, quoted_message_id=None) -> str:
    body = {'conversationId':route['conversation_id'],'type':'text','text':text,'clientMessageId':client_message_id}
    if quoted_message_id: body['quotedExternalMessageId']=quoted_message_id
    endpoint='/quoted-once' if quoted_message_id and quoted_once_scope(route.get('tenant_id'),route['channel_id']) else '/messages'
    result = _request(route['api_key'], 'POST', '/v1/channels/' + route['channel_id'] + endpoint, body)
    # A missing endpoint, timeout or malformed receipt never falls back to the old queue.
    if endpoint=='/quoted-once':
        _validate_quoted_once(result,route,client_message_id,quoted_message_id)
    return str(result.get('id') or client_message_id)


def send_ai_reply(route: dict, text: str, run_id: str) -> str:
    """Use the run UUID directly as Connect's UUID-validated idempotency key."""
    try:
        client_message_id = str(uuid.UUID(str(run_id)))
    except (ValueError, TypeError, AttributeError):
        raise TenantPlatformError('AI cavabının sorğu ID-si düzgün deyil.') from None
    from tenant_ai_group_coordination import group_jid
    if group_jid(route.get('conversation_id')):
        result = _request(route['api_key'], 'POST', '/v1/channels/' + route['channel_id'] + '/group-messages',
                          {'conversationId':route['conversation_id'],'type':'text','text':text,'clientMessageId':client_message_id})
        if result.get('status') != 'sent' or not result.get('id'):
            raise TenantPlatformError('Connect group delivery was not confirmed.')
        return str(result['id'])
    return send(route, text, client_message_id)


def send_ai_reply_with_receipt(route: dict, text: str, request_id: str) -> str:
    """Send one direct reply and require a provider message ID receipt."""
    try:
        client_message_id=str(uuid.UUID(str(request_id)))
    except (ValueError,TypeError,AttributeError):
        raise TenantPlatformError('AI cavabının sorğu ID-si düzgün deyil.') from None
    result=_request(route['api_key'],'POST','/v1/channels/'+route['channel_id']+'/messages',
        {'conversationId':route['conversation_id'],'type':'text','text':text,'clientMessageId':client_message_id})
    if not isinstance(result,dict) or not isinstance(result.get('id'),str) or not result['id']:
        raise TenantPlatformError('Connect direct delivery receipt is unavailable.')
    return str(result['id'])


def send_group_with_receipt(route: dict, text: str, request_id: str, *, media: dict | None = None) -> dict:
    """One confirmed group delivery; expose provider reference only when valid.

    Callers must durably reserve the UUID before calling and quarantine unknown
    outcomes. This helper does not retry, store a receipt or grant permission.
    """
    from tenant_ai_group_coordination import group_jid
    try: key=str(uuid.UUID(str(request_id)))
    except (ValueError,TypeError,AttributeError): raise TenantPlatformError('Invalid group request ID.') from None
    if not group_jid(route.get('conversation_id')): raise TenantPlatformError('Group route required.')
    payload={'conversationId':route['conversation_id'],'type':str(media['type']) if media else 'text',
      'text':text,'clientMessageId':key}
    if media:
        payload['media']={k:media[k] for k in ('base64','mimeType','fileName') if k in media}
    result=_request(route['api_key'],'POST','/v1/channels/'+route['channel_id']+'/group-messages',payload)
    if result.get('status')!='sent' or not result.get('id'): raise TenantPlatformError('Connect group delivery was not confirmed.')
    external=result.get('externalMessageId')
    # A Connect row alone does not prove the upstream accepted this message.
    # Without a usable provider receipt the caller must quarantine the outcome.
    if not isinstance(external,str) or not re.fullmatch(r'[A-Za-z0-9_-]{5,200}',external):
        raise TenantPlatformError('Connect group provider receipt is unavailable.')
    return {'message_id':str(result['id']),'external_message_id':external}


def send_consultation_with_receipt(route, text, request_id):
    """Accept Connect's durable queue receipt without claiming provider delivery."""
    from tenant_ai_group_coordination import group_jid
    key=str(uuid.UUID(str(request_id)))
    endpoint='/group-messages' if group_jid(route.get('conversation_id')) else '/messages'
    result=_request(route['api_key'],'POST','/v1/channels/'+route['channel_id']+endpoint,
        {'conversationId':route['conversation_id'],'type':'text','text':text,'clientMessageId':key})
    message_id=result.get('id') if isinstance(result,dict) else None
    external=result.get('externalMessageId') if isinstance(result,dict) else None
    if not isinstance(message_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',message_id):
        raise TenantPlatformError('Connect consultation queue receipt is unavailable.')
    if result.get('status') in {'failed','rejected','cancelled','canceled','error'}:
        raise TenantPlatformError('Connect consultation was not delivered.')
    if isinstance(external,str) and re.fullmatch(r'[A-Za-z0-9_-]{5,200}',external):
        return {'state':'sent','message_id':message_id,'external_message_id':external}
    if result.get('status') in {None,'pending','queued','accepted','sent'}:
        return {'state':'pending','message_id':message_id,'external_message_id':None}
    raise TenantPlatformError('Connect consultation outcome is unavailable.')


def download_connect_message_media(route: dict, message_id: str, expected_type: str,
                                   conversation_id: str) -> dict:
    """Fetch one tenant/channel-scoped Connect attachment without exposing its URL."""
    limits={'image':8*1024*1024,'audio':8*1024*1024,'video':8*1024*1024,'document':5*1024*1024}
    allowed={
      'image':{'image/jpeg','image/png','image/webp','image/gif'},
      'audio':{'audio/ogg','application/ogg','audio/mpeg','audio/mp3','audio/mp4','audio/wav','audio/x-wav','audio/webm','audio/flac','audio/x-flac','audio/aac'},
      'video':{'video/mp4'},
      'document':{'application/pdf','text/plain','application/vnd.openxmlformats-officedocument.wordprocessingml.document'},
    }
    if (expected_type not in limits or not isinstance(message_id,str) or not message_id
            or not re.fullmatch(r'[A-Za-z0-9_-]{5,200}',message_id)
            or not isinstance(conversation_id,str) or not conversation_id or len(conversation_id)>200
            or any(ord(char)<33 for char in conversation_id)
            or route.get('conversation_id')!=conversation_id):
        raise TenantPlatformError('Connect əlavəsi düzgün deyil.')
    try:
        url=CONNECT_URL+'/v1/channels/'+str(route['channel_id'])+'/messages/'+message_id+'/media?'+urlencode({'conversationId':conversation_id})
        response=requests.get(url,
          headers={'x-api-key':route['api_key'],'Accept':'application/octet-stream','Accept-Encoding':'identity'},
          timeout=(5,20),allow_redirects=False,stream=True)
    except requests.RequestException as exc:
        raise TenantPlatformError('Connect əlavəsi əldə edilmədi.') from exc
    try:
        if response.status_code!=200 or response.is_redirect:
            raise TenantPlatformError('Connect əlavəsi əldə edilmədi.')
        mime=str(response.headers.get('Content-Type') or '').split(';',1)[0].strip().lower()
        if mime not in allowed[expected_type]:raise TenantPlatformError('Connect əlavəsinin formatı uyğun deyil.')
        length=response.headers.get('Content-Length')
        if length and (not length.isdigit() or int(length)>limits[expected_type]):raise TenantPlatformError('Connect əlavəsi limitdən böyükdür.')
        chunks=[];total=0
        for chunk in response.iter_content(64*1024):
            if not chunk:continue
            total+=len(chunk)
            if total>limits[expected_type]:raise TenantPlatformError('Connect əlavəsi limitdən böyükdür.')
            chunks.append(chunk)
        content=b''.join(chunks)
        if not content:raise TenantPlatformError('Connect əlavəsi boşdur.')
        return {'type':expected_type,'base64':base64.b64encode(content).decode('ascii'),
          'mimeType':mime,'fileName':'case-attachment'}
    finally:
        response.close()


def send_ai_reply_if_active(tenant: str, lead_id: int, channel_id: str, conversation_id: str,
                            text: str, run_id: str, expected_revision: int, *, handoff_fingerprint: str = '',
                            pilot_scope_fenced: bool = False, stop_after_reply: bool = False) -> str | None:
    """Serialize Connect send with an observed human takeover.

    The row lock intentionally spans the short Connect request. If the human
    event was committed first, this returns ``None`` and no AI request is made.
    If the AI request wins the lock, the later human event still switches the
    dialogue to Human Mode immediately after acceptance. A provider event that
    arrives only after an already accepted AI request cannot be recalled; that
    ordering is recorded rather than hidden.
    """
    from tenant_ai_group_coordination import group_jid, reserve_delivery, delivery_source, job_allowed
    coordinated = bool(group_jid(conversation_id))
    if coordinated and not reserve_delivery(tenant, channel_id, conversation_id, run_id):
        return None
    ticket = None
    send_source = None
    with _connect() as conn:
        _schema(conn)
        conn.commit()  # Release DDL locks before waiting on a dialogue/group lock.
        with conn.cursor() as cur:
            lock_dialog(cur, tenant, "connect", channel_id, conversation_id)
            if coordinated:
                # Lock candidate settings before choosing the scheduled speaker.
                cur.execute('SELECT id FROM saas_ai_agents WHERE tenant_id=%s::uuid ORDER BY id FOR SHARE',(tenant,))
                cur.fetchall()
                source = delivery_source(cur, tenant, run_id)
                send_source = source
                if not source or not job_allowed(cur, {**source, 'tenant_id':tenant, 'provider':'connect', 'channel_id':channel_id, 'conversation_id':conversation_id}):
                    return None
                if source['source_kind']=='run':
                    from tenant_ai_agent_runtime_flags import enabled as runtime_flag
                    from tenant_ai_pilot import manual_live_job
                    cur.execute('SELECT a.mode,s.auto_send_enabled FROM saas_ai_agents a JOIN saas_ai_agent_settings s ON s.tenant_id=a.tenant_id WHERE a.tenant_id=%s::uuid AND a.id=%s::uuid FOR SHARE OF s',(tenant,source['agent_id']))
                    send_policy=cur.fetchone() or {}
                    scoped_override=bool(pilot_scope_fenced and manual_live_job({**source,
                        'provider':'connect','tenant_id':tenant,'channel_id':channel_id,
                        'conversation_id':conversation_id}))
                    if (not scoped_override and (send_policy.get('mode')!='automatic'
                            or send_policy.get('auto_send_enabled') is not True
                            or not runtime_flag('AI_AGENTS_AUTO_SEND_ENABLED'))):
                        return None
                from tenant_ai_agent_connect import allowed
                if not allowed({**source,'tenant_id':tenant,'provider':'connect','channel_id':channel_id,'conversation_id':conversation_id,'lead_id':lead_id},connection=conn):
                    return None

            cur.execute('''SELECT c.connect_channel_id,c.conversation_id,i.secrets,cs.revision,cs.state
              FROM saas_connect_conversations c
              JOIN saas_tenant_integrations i ON i.tenant_id=c.tenant_id AND i.provider='connect' AND i.status='connected'
              JOIN saas_connect_channels ch ON ch.tenant_id=c.tenant_id AND ch.connect_channel_id=c.connect_channel_id AND ch.active=TRUE
              JOIN saas_ai_conversation_state cs ON cs.tenant_id=c.tenant_id AND cs.provider='connect' AND cs.conversation_id=c.conversation_id AND cs.channel_id=c.connect_channel_id::text
              WHERE c.tenant_id=%s::uuid AND c.lead_id=%s AND c.connect_channel_id=%s::uuid AND c.conversation_id=%s
                AND NOT ai_legacy_blocked(c.tenant_id,c.connect_channel_id::text,c.conversation_id,COALESCE((SELECT event_fingerprint FROM saas_ai_agent_runs WHERE tenant_id=c.tenant_id AND id=%s::uuid),''))
              FOR UPDATE OF c,cs''', (tenant, int(lead_id), channel_id, conversation_id,run_id))
            row = cur.fetchone()
            if (not row or row.get('state') != 'active' or int(row.get('revision') or 0) != int(expected_revision)):
                conn.rollback()
                return None
            try:
                secret = json.loads(_fernet().decrypt(bytes(row['secrets'])).decode())
            except Exception as exc:
                raise TenantPlatformError('Connect açarı oxunmadı.') from exc
            route = {'provider':'connect','channel_id':str(row['connect_channel_id']),'conversation_id':str(row['conversation_id']),
                     'api_key':str(secret.get('api_key') or '')}
            # Revalidate the single-dialog pilot confirmation at the final
            # provider boundary for every automated send.
            from tenant_ai_pilot import automatic_send_allowed
            if send_source is None:
                try:
                    send_source = delivery_source(cur, tenant, run_id)
                except Exception:
                    # No source fingerprint is a pilot denial; unconfigured
                    # deployments retain their existing send policy.
                    send_source = None
            gate_context = {'provider':'connect', 'tenant_id':tenant, 'channel_id':channel_id,
                'conversation_id':conversation_id,
                'event_fingerprint':str((send_source or {}).get('event_fingerprint') or ''),
                'agent_id':(send_source or {}).get('agent_id'),
                '_pilot_scope_fenced':pilot_scope_fenced is True}
            if not automatic_send_allowed(gate_context, connection=conn):
                conn.rollback()
                return None
            message_id = send_ai_reply(route, text, run_id)
            cur.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,author_name,body,message_type,media_url,happened_at,raw)
              VALUES(%s,%s::uuid,%s,%s,'outgoing',%s,'AI Agent',%s,'text','',now(),%s::jsonb) ON CONFLICT(tenant_id,external_id) DO NOTHING''',
                        (uuid.uuid4(),tenant,int(lead_id),'connect:'+message_id,'connect:'+channel_id,text,_json({'source':'connect','status':'accepted','run_id':str(run_id)})))
            if (isinstance((send_source or {}).get('payload'),dict) and
                    ((send_source or {}).get('payload',{}).get('pilot_developer_followup') is True or
                     (send_source or {}).get('payload',{}).get('pilot_customer_case_reply') is True)):
                from tenant_consultation_group import record_customer_send_receipt
                record_customer_send_receipt(cur,tenant,str(send_source.get('event_fingerprint') or ''),message_id)
            cur.execute('''UPDATE saas_ai_conversation_state SET last_agent_message_id=%s,revision=revision+1,updated_at=now()
              WHERE tenant_id=%s::uuid AND provider='connect' AND conversation_id=%s AND channel_id=%s AND state='active' AND revision=%s''',
                        (str(message_id),tenant,conversation_id,channel_id,int(expected_revision)))
            from tenant_ai_legacy_permit import renew
            renew(cur, tenant, channel_id, conversation_id)
            if handoff_fingerprint:
                from tenant_ai_group_coordination import stop
                stop(cur, tenant, conversation_id, reason='trigger_reply_sent_then_handoff')
                from tenant_support_inbox_store import handoff_ticket
                ticket = handoff_ticket(cur, {'tenant_id':tenant,'provider':'connect',
                    'channel_id':channel_id,'conversation_id':conversation_id,
                    'event_fingerprint':handoff_fingerprint}, 'trigger_reply_sent_then_handoff')
                cur.execute("UPDATE saas_ai_conversation_state SET state='human',reason='trigger_reply_sent_then_handoff',revision=revision+1,updated_at=now() WHERE tenant_id=%s::uuid AND provider='connect' AND conversation_id=%s AND channel_id=%s",
                            (tenant,conversation_id,channel_id))
            elif stop_after_reply:
                if coordinated:
                    from tenant_ai_group_coordination import stop
                    stop(cur,tenant,conversation_id,state='paused',reason='ai_no_answer')
                cur.execute("UPDATE saas_ai_conversation_state SET state='paused',reason='ai_no_answer',revision=revision+1,updated_at=now() WHERE tenant_id=%s::uuid AND provider='connect' AND conversation_id=%s AND channel_id=%s AND state='active'",
                            (tenant,conversation_id,channel_id))
        conn.commit()
    if ticket:
        from tenant_support_inbox_store import summarize_created
        summarize_created(tenant,ticket)
    return message_id


def record_outbound(tenant: str, lead_id: int, route: dict, text: str, external_id: str, author: str = 'Operator', *, actor=None, quoted_message_id=None) -> None:
    with _connect() as conn:
        if author != 'AI Agent':
            from tenant_ai_agents_schema import ensure_schema
            ensure_schema(conn)
            from tenant_knowledge_live_queue import create_schema as create_live_queue_schema
            create_live_queue_schema(conn)
        _schema(conn)
        conn.commit()  # Release DDL locks before waiting on a dialogue/group lock.
        with conn.cursor() as cur:
            lock_dialog(cur, tenant, "connect", route["channel_id"], route["conversation_id"])
            cur.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,author_name,body,message_type,media_url,happened_at,raw)
              VALUES(%s,%s::uuid,%s,%s,'outgoing',%s,%s,%s,'text','',now(),%s::jsonb) ON CONFLICT(tenant_id,external_id) DO NOTHING RETURNING id''',
                        (uuid.uuid4(),tenant,lead_id,'connect:'+external_id,'connect:'+route['channel_id'],author,text,_json({'source':'connect','status':'pending',**({'quotedExternalMessageId':quoted_message_id} if quoted_message_id else {})})))
            saved=cur.fetchone()
            if author != 'AI Agent' and saved:
                from tenant_ai_agent_connect import mark_operator_reply
                mark_operator_reply(tenant, lead_id, str(route['channel_id']), str(route['conversation_id']), connection=conn, actor=actor)
                if saved:
                    from tenant_knowledge_live_queue import enqueue as enqueue_case_extraction
                    enqueue_case_extraction(cur,tenant,str(route['channel_id']),str(route['conversation_id']),
                        lead_id,str(saved['id']),actor_id=actor)
        conn.commit()



def _validate_quoted_once(result,route,request_id,quoted_message_id,message_id=None):
    try:valid_id=str(uuid.UUID(str(result.get('id'))))
    except (ValueError,TypeError,AttributeError):raise TenantPlatformError('Quoted delivery receipt unavailable.') from None
    if (result.get('channelId')!=route['channel_id'] or result.get('conversationId')!=route['conversation_id']
        or result.get('clientMessageId')!=request_id
        or not isinstance(result.get('quotedExternalMessageId'),str) or re.fullmatch(r'[A-Za-z0-9_-]{5,200}',result['quotedExternalMessageId']) is None
        or (quoted_message_id is not None and result.get('quotedExternalMessageId')!=quoted_message_id)
        or (message_id and valid_id!=message_id) or result.get('protocol') not in {'quoted-once-v1','legacy-read-only'}):
        raise TenantPlatformError('Quoted delivery receipt scope mismatch.')
    return valid_id


def quoted_delivery_status(route,message_id,request_id,quoted_message_id):
    """Exact-scope request lookup recovers timed-out responses without another POST."""
    if not quoted_once_scope(route.get('tenant_id'),route['channel_id']):
        raise TenantPlatformError('Quoted receipt scope unavailable.')
    if not message_id and not quoted_message_id:raise TenantPlatformError('Exact quoted receipt identity required.')
    request_id=str(uuid.UUID(str(request_id)))
    if message_id:message_id=str(uuid.UUID(str(message_id)))
    result=_request(route['api_key'],'GET','/v1/channels/'+route['channel_id']+'/quoted-once/requests/'+request_id+'?'+
        urlencode({'conversationId':route['conversation_id'],**({'quotedExternalMessageId':quoted_message_id} if quoted_message_id else {}),**({'messageId':message_id} if message_id else {})}))
    found_id=_validate_quoted_once(result,route,request_id,quoted_message_id,message_id)
    state=result.get('status')
    if state not in {'pending','retrying','sent','delivered','read','failed','unknown'}:
        raise TenantPlatformError('Quoted delivery receipt unavailable.')
    provider_id=result.get('externalMessageId')
    valid_provider_id=isinstance(provider_id,str) and re.fullmatch(r'[A-Za-z0-9_-]{5,200}',provider_id) is not None
    accepted=result.get('providerAccepted') is True and valid_provider_id and state in {'sent','delivered','read'}
    if state in {'sent','delivered','read'} and not accepted:state='unknown'
    if state in {'delivered','read'} and result.get('delivered') is not True:state='sent' if accepted else 'unknown'
    return {'message_id':found_id,'quote_protocol':True,'delivery_state':state,'provider_accepted':accepted,
        'delivery_phase':result.get('phase') if result.get('phase') in {'reserved','unknown','accepted','not_sent','legacy'} else 'unavailable',
        **({'error_code':result['errorCode']} if result.get('errorCode') in {'quoted_message_unavailable','quoted_source_scope_conflict','quoted_source_lookup_unavailable','quoted_provider_unavailable'} else {}),
        'delivered':accepted and state in {'delivered','read'} and result.get('delivered') is True,
        'retry_safe':state=='failed' and result.get('protocol')=='quoted-once-v1' and result.get('retrySafe') is True and not provider_id
            and result.get('errorCode') in {'quoted_message_unavailable','quoted_source_scope_conflict','quoted_source_lookup_unavailable','quoted_provider_unavailable'},
        **({'provider_message_id':provider_id} if valid_provider_id else {})}
