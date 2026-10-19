"""Tenant-isolated Beintaskbot Connect transport.

This module deliberately treats Connect as an external transport: the CRM only
stores an encrypted, tenant-owned API key and never reads Connect's database.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import uuid
from tenant_ai_dialog_scope import lock_dialog
from datetime import datetime, timezone

import requests

from tenant_platform import TenantPlatformError, _connect, _ensure_schema, _fernet, _json
from tenant_member_store import member

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
    elif jid.endswith('@s.whatsapp.net') or explicit_type in {'contact', 'personal', 'direct'}:
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
    if '@' in value and not value.endswith('@s.whatsapp.net'):
        return ''
    candidate = value.split('@', 1)[0]
    return candidate if re.fullmatch(r'[1-9][0-9]{5,18}', candidate) else ''


def _upsert_conversation(cur, tenant, channel_id, conversation, metadata):
    """Persist group metadata separately from an individual contact identity."""
    kind = metadata.get('kind') or ''
    display_name = metadata.get('display_name') or ''
    # Keep the established direct-chat phone fallback intact. A group must
    # never inherit the numeric prefix of its JID as a customer phone.
    phone = '' if kind == 'group' else _phone_from_jid(conversation)
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
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_connect_webhook_events (
          tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE, event_id TEXT NOT NULL,
          received_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,event_id))''')


def _owner(profile: dict) -> None:
    if not profile or profile.get('role') not in {'owner', 'admin'}:
        raise TenantPlatformError('Connect bağlantısını yalnız administrator idarə edə bilər.')


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
            cur.execute("SELECT connect_channel_id,name,status,active FROM saas_connect_channels WHERE tenant_id=%s::uuid ORDER BY name", (tenant,))
            channels = [_public_channel(channel) for channel in cur.fetchall()]
    # Existing Connect installations predate observed outbound events. The
    # stored subscription belongs to this tenant alone, so an owner/admin
    # opening its settings safely upgrades just that webhook without touching
    # another CRM subscriber or another tenant.
    if row and row.get('status') == 'connected' and profile.get('role') in {'owner', 'admin'}:
        try:
            credentials = json.loads(_fernet().decrypt(bytes(row['secrets'])).decode()) if row.get('secrets') else {}
            webhook_id = str(credentials.get('webhook_id') or '')
            if webhook_id:
                _request(str(credentials.get('api_key') or ''), 'PATCH', '/v1/webhooks/' + webhook_id,
                         {'events': list(CONNECT_WEBHOOK_EVENTS)})
        except Exception:
            # Do not make a read-only settings page fail during a temporary
            # Connect outage. configure() always applies the complete list.
            pass
    return {'connected': bool(row and row['status'] == 'connected'), 'channels': channels,
            'updated_at': row['updated_at'].isoformat() if row and row.get('updated_at') else None,
            'metadata': (row or {}).get('metadata') or {}}


def configure(profile: dict, payload: dict, callback_url: str) -> dict:
    _owner(profile)
    tenant = str(profile['tenant_id'])
    key = str(payload.get('api_key') or '').strip()
    requested = {str(value) for value in (payload.get('channel_ids') or []) if str(value).strip()}
    if not key or not requested:
        raise TenantPlatformError('Connect API açarı və ən azı bir WhatsApp kanalı seçin.')
    remote = _request(key, 'GET', '/v1/channels').get('data', [])
    if not isinstance(remote, list):
        raise TenantPlatformError('Connect kanalları oxunmadı.')
    selected = [row for row in remote if isinstance(row, dict) and str(row.get('id')) in requested]
    if len(selected) != len(requested):
        raise TenantPlatformError('Seçilmiş kanal bu Connect hesabına aid deyil.')
    secret = secrets.token_urlsafe(32)
    hook = _request(key, 'POST', '/v1/webhooks', {'url': callback_url, 'events': list(CONNECT_WEBHOOK_EVENTS)})
    # Connect creates the secret and returns it once. Use it for HMAC verification.
    webhook_secret = str(hook.get('secret') or '')
    if not webhook_secret:
        raise TenantPlatformError('Connect webhook təhlükəsizlik açarını qaytarmadı.')
    encrypted = _fernet().encrypt(_json({'api_key': key, 'webhook_secret': webhook_secret, 'webhook_id': hook.get('id'), 'local_nonce': secret}).encode())
    with _connect() as conn:
        _schema(conn)
        with conn.cursor() as cur:
            cur.execute('''INSERT INTO saas_tenant_integrations(tenant_id,provider,status,account_domain,secrets,metadata,connected_at)
              VALUES(%s::uuid,'connect','connected',%s,%s,%s::jsonb,now())
              ON CONFLICT(tenant_id,provider) DO UPDATE SET status='connected',account_domain=EXCLUDED.account_domain,secrets=EXCLUDED.secrets,metadata=EXCLUDED.metadata,connected_at=now(),updated_at=now()''',
                        (tenant, CONNECT_URL, encrypted, _json({'webhook_id': hook.get('id'), 'channel_ids': sorted(requested)})))
            cur.execute('UPDATE saas_connect_channels SET active=FALSE,updated_at=now() WHERE tenant_id=%s::uuid', (tenant,))
            for channel in selected:
                cur.execute('''INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name,status,active)
                  VALUES(%s::uuid,%s::uuid,%s,%s,TRUE) ON CONFLICT(tenant_id,connect_channel_id) DO UPDATE
                  SET name=EXCLUDED.name,status=EXCLUDED.status,active=TRUE,updated_at=now()''',
                            (tenant, str(channel['id']), str(channel.get('name') or ''), str(channel.get('status') or '')))
        conn.commit()
    return status(profile)


def channels(profile: dict, api_key: str) -> list[dict]:
    _owner(profile)
    rows = _request(str(api_key or '').strip(), 'GET', '/v1/channels').get('data', [])
    if not isinstance(rows, list):
        raise TenantPlatformError('Connect kanalları oxunmadı.')
    return [{'id': str(row.get('id') or ''), 'name': str(row.get('name') or ''), 'status': str(row.get('status') or '')}
            for row in rows if isinstance(row, dict) and row.get('id')]


def disconnect(profile: dict) -> dict:
    _owner(profile)
    tenant = str(profile['tenant_id'])
    with _connect() as conn:
        _schema(conn)
        with conn.cursor() as cur:
            cur.execute("UPDATE saas_tenant_integrations SET status='not_connected',secrets=NULL,metadata='{}'::jsonb,updated_at=now() WHERE tenant_id=%s::uuid AND provider='connect'", (tenant,))
            cur.execute('UPDATE saas_connect_channels SET active=FALSE,updated_at=now() WHERE tenant_id=%s::uuid', (tenant,))
        conn.commit()
    return status(profile)


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
        conn.commit()  # Release DDL locks before waiting on a dialogue/group lock.
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
            lock_dialog(cur, tenant, 'connect', channel_id, conversation)
            cur.execute('INSERT INTO saas_connect_webhook_events(tenant_id,event_id) VALUES(%s::uuid,%s) ON CONFLICT DO NOTHING RETURNING event_id', (tenant,event_id))
            inserted_event = bool(cur.fetchone())
            outbound = event_type == 'message.outbound.observed' or data.get('fromMe') is True
            from tenant_ai_group_coordination import group_jid, known_ai_origin, verified_employee, stop as stop_group
            coordinated = bool(group_jid(conversation))
            ai_origin = coordinated and known_ai_origin(cur,tenant,data)
            uncertain_origin = coordinated and data.get('authorshipUncertain') is True and not ai_origin
            employee = verified_employee(cur,tenant,data) if coordinated and not ai_origin and not uncertain_origin else None
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
                  ON CONFLICT(tenant_id,external_id) DO UPDATE SET external_id=saas_crm_messages.external_id
                  RETURNING id,author_name,raw,kommo_lead_id,channel''',
                            (uuid.uuid4(),tenant,lead_id,external,'connect:'+channel_id,_outbound_author(data),str(data.get('text') or ''),str(data.get('type') or 'text'),occurred,_json(data)))
                stored = cur.fetchone()
                if stored and (stored.get('kommo_lead_id') != lead_id or stored.get('channel') != 'connect:' + channel_id):
                    raise ConnectWebhookError('message_scope_conflict', 'Connect message scope mismatch.', 409)
                # A malformed outbound event with ``fromMe:false`` is kept as
                # an observable outbound record, but cannot take over a chat.
                if data.get('fromMe') is not False and not _outbound_is_ai(data, stored) and not ai_origin and (not coordinated or employee is not None):
                    from tenant_ai_agent_connect import mark_operator_reply
                    mark_operator_reply(tenant, lead_id, channel_id, conversation, connection=conn, actor=employee)
                    if stored and stored.get('id'):
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
                occurred = str(event.get('occurred_at') or datetime.now(timezone.utc).isoformat())
                is_group = metadata.get('kind') == 'group'
                phone = '' if is_group else _phone_from_jid(conversation)
                author_name = (metadata.get('participant_name') or _phone_from_jid(metadata.get('participant_jid'))
                               or phone)
                deal_name = metadata.get('display_name') or author_name
                cur.execute('''INSERT INTO saas_crm_deals(tenant_id,kommo_lead_id,name,contact_name,phone,channel,last_message,last_message_at,source_updated_at,raw)
              VALUES(%s::uuid,%s,%s,%s,%s,%s,%s,%s::timestamptz,%s::timestamptz,%s::jsonb)
              ON CONFLICT(tenant_id,kommo_lead_id) DO UPDATE SET contact_name=EXCLUDED.contact_name,phone=EXCLUDED.phone,channel=EXCLUDED.channel,last_message=EXCLUDED.last_message,last_message_at=EXCLUDED.last_message_at,source_updated_at=EXCLUDED.source_updated_at,raw=EXCLUDED.raw,synced_at=now()''',
                            (tenant,lead_id,deal_name,deal_name,phone,'connect:'+channel_id,str(data.get('text') or ''),occurred,occurred,_json({'source':'connect','channel_id':channel_id,'conversation_id':conversation,'conversation_kind':metadata.get('kind') or ''})))
                external = 'connect:' + str(data.get('id') or event_id)
                cur.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,author_name,body,message_type,media_url,happened_at,raw)
              VALUES(%s,%s::uuid,%s,%s,'incoming',%s,%s,%s,%s,%s,%s::timestamptz,%s::jsonb) ON CONFLICT(tenant_id,external_id) DO NOTHING''',
                            # Connect does not currently provide a safe
                            # downloadable media URL in its signed event.  Do
                            # not store a fake non-empty placeholder: that
                            # would make every text look like an attachment and
                            # can incorrectly affect later AI context.
                            (uuid.uuid4(),tenant,lead_id,external,'connect:'+channel_id,author_name,str(data.get('text') or ''),str(data.get('type') or 'text'),'',occurred,_json(data)))
                accepted = True
                queued_payload = (tenant, channel_id, conversation, lead_id, str(data.get('id') or event_id), event_id,
                                  str(data.get('text') or ''), str(data.get('type') or 'text'))
            # The CRM copy, dedup receipt and AI admission commit together.
            # A failed queue write rolls back the webhook, so Connect can retry.
            if accepted and queued_payload:
                cur.execute("SELECT id FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id=%s AND kommo_lead_id=%s AND channel=%s AND direction='incoming'",
                            (tenant, 'connect:' + queued_payload[4], queued_payload[3], 'connect:' + channel_id))
                source_message=cur.fetchone()
                if not source_message:
                    raise ConnectWebhookError('message_scope_conflict', 'Connect message scope mismatch.', 409)
                if coordinated and (ai_origin or employee is not None or uncertain_origin or control_replayed):
                    if employee is not None:
                        stop_group(cur,tenant,conversation,reason='verified_employee_reply',actor=employee)
                        from tenant_knowledge_live_queue import enqueue as enqueue_case_extraction
                        enqueue_case_extraction(cur,tenant,channel_id,conversation,queued_payload[3],
                            str(source_message['id']),actor_id=employee)
                    conn.commit()
                    return True
                from tenant_knowledge_live_queue import enqueue as enqueue_case_extraction
                enqueue_case_extraction(cur,tenant,channel_id,conversation,queued_payload[3],str(source_message['id']))
                from tenant_ai_agent_connect import enqueue
                enqueue(queued_payload[0], channel_id=queued_payload[1], conversation_id=queued_payload[2],
                        lead_id=queued_payload[3], message_id=queued_payload[4], event_id=queued_payload[5],
                        text=queued_payload[6], message_type=queued_payload[7],
                        is_group=metadata.get('kind') == 'group', provider_message_id=data.get('externalMessageId'), connection=conn)
            conn.commit()
    return accepted


def connect_route(cur, tenant: str, lead_id: int) -> dict | None:
    cur.execute('''SELECT c.connect_channel_id,c.conversation_id,i.secrets FROM saas_connect_conversations c
      JOIN saas_tenant_integrations i ON i.tenant_id=c.tenant_id AND i.provider='connect' AND i.status='connected'
      JOIN saas_connect_channels ch ON ch.tenant_id=c.tenant_id AND ch.connect_channel_id=c.connect_channel_id AND ch.active=TRUE
      WHERE c.tenant_id=%s::uuid AND c.lead_id=%s FOR SHARE''', (tenant,lead_id))
    row = cur.fetchone()
    if not row: return None
    try: secret = json.loads(_fernet().decrypt(bytes(row['secrets'])).decode())
    except Exception as exc: raise TenantPlatformError('Connect açarı oxunmadı.') from exc
    return {'provider':'connect','channel_id':str(row['connect_channel_id']),'conversation_id':str(row['conversation_id']),'api_key':str(secret.get('api_key') or '')}


def send(route: dict, text: str, client_message_id: str) -> str:
    body = {'conversationId':route['conversation_id'],'type':'text','text':text,'clientMessageId':client_message_id}
    result = _request(route['api_key'], 'POST', '/v1/channels/' + route['channel_id'] + '/messages', body)
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


def send_ai_reply_if_active(tenant: str, lead_id: int, channel_id: str, conversation_id: str,
                            text: str, run_id: str, expected_revision: int, *, handoff_fingerprint: str = '', draft_review=None) -> str | None:
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
    with _connect() as conn:
        _schema(conn)
        conn.commit()  # Release DDL locks before waiting on a dialogue/group lock.
        with conn.cursor() as cur:
            lock_dialog(cur, tenant, "connect", channel_id, conversation_id)
            if draft_review is not None:
                from tenant_ai_draft_review import snapshot
                view,d,_,_=snapshot(cur,draft_review['profile'],run_id,locked=True)
                if (d['state']!='sending' or d['mode']!='manual' or not d['enabled'] or d['role']!='primary'
                    or not d['company_enabled'] or d['emergency_stop'] or d['operating_mode']!='active' or d['tenant_status']!='active'
                    or d['version']!=draft_review['expected_version'] or d['draft']!=text
                    or view['context_token']!=draft_review['expected_context_token']):
                    return None
            if coordinated:
                # Lock candidate settings before choosing the scheduled speaker.
                cur.execute('SELECT id FROM saas_ai_agents WHERE tenant_id=%s::uuid ORDER BY id FOR SHARE',(tenant,))
                cur.fetchall()
                source = delivery_source(cur, tenant, run_id)
                if not source or not job_allowed(cur, {**source, 'tenant_id':tenant, 'provider':'connect', 'channel_id':channel_id, 'conversation_id':conversation_id}):
                    return None
                if source['source_kind']=='run':
                    from tenant_ai_agent_runtime_flags import enabled as runtime_flag
                    cur.execute('SELECT a.mode,s.auto_send_enabled FROM saas_ai_agents a JOIN saas_ai_agent_settings s ON s.tenant_id=a.tenant_id WHERE a.tenant_id=%s::uuid AND a.id=%s::uuid FOR SHARE OF s',(tenant,source['agent_id']))
                    send_policy=cur.fetchone() or {}
                    if send_policy.get('mode')!='automatic' or send_policy.get('auto_send_enabled') is not True or not runtime_flag('AI_AGENTS_AUTO_SEND_ENABLED'):
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
                AND NOT ai_legacy_blocked(c.tenant_id,c.connect_channel_id::text,c.conversation_id,COALESCE((SELECT event_fingerprint FROM saas_ai_agent_runs WHERE tenant_id=c.tenant_id AND id=%s::uuid),(SELECT source_fingerprint FROM saas_ai_agent_drafts WHERE tenant_id=c.tenant_id AND id=%s::uuid),''))
              FOR UPDATE OF c,cs''', (tenant, int(lead_id), channel_id, conversation_id,run_id,run_id))
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
            message_id = send_ai_reply(route, text, run_id)
            cur.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,author_name,body,message_type,media_url,happened_at,raw)
              VALUES(%s,%s::uuid,%s,%s,'outgoing',%s,'AI Agent',%s,'text','',now(),%s::jsonb) ON CONFLICT(tenant_id,external_id) DO NOTHING''',
                        (uuid.uuid4(),tenant,int(lead_id),'connect:'+message_id,'connect:'+channel_id,text,_json({'source':'connect','status':'accepted','run_id':str(run_id)})))
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
        conn.commit()
    if ticket:
        from tenant_support_inbox_store import summarize_created
        summarize_created(tenant,ticket)
    return message_id


def record_outbound(tenant: str, lead_id: int, route: dict, text: str, external_id: str, author: str = 'Operator', *, actor=None) -> None:
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
                        (uuid.uuid4(),tenant,lead_id,'connect:'+external_id,'connect:'+route['channel_id'],author,text,_json({'source':'connect','status':'pending'})))
            saved=cur.fetchone()
            if author != 'AI Agent':
                from tenant_ai_agent_connect import mark_operator_reply
                mark_operator_reply(tenant, lead_id, str(route['channel_id']), str(route['conversation_id']), connection=conn, actor=actor)
                if saved:
                    from tenant_knowledge_live_queue import enqueue as enqueue_case_extraction
                    enqueue_case_extraction(cur,tenant,str(route['channel_id']),str(route['conversation_id']),
                        lead_id,str(saved['id']),actor_id=actor)
        conn.commit()

