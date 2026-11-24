"""Owner-only browser access to provider-proven retained-route merges."""
import json
import os
import re
import uuid

from tenant_ai_dialogue_merge import _pairs, _token, ensure_schema, execute
from tenant_ai_agents_store import _audit
from tenant_connect_direct_identity import direct_identity
from tenant_connect_direct_reconcile import _owner
from tenant_connect_phone_evidence import collect, normalize_phone, normalize_phone_jid
from tenant_linear_policy import TenantLinearError
from tenant_operator_access import access, assert_route
from tenant_platform import _connect


_ROUTE = {'provider', 'channel_id', 'conversation_id', 'lead_id'}
PHONE_SCHEMA = {'saas_connect_dialog_display_phones': ('tenant_id', 'channel_id', 'conversation_id', 'lead_id', 'phone')}


def _route(value):
    if (not isinstance(value, dict) or set(value) != _ROUTE or value['provider'] != 'connect'
            or not isinstance(value['conversation_id'], str)
            or not direct_identity(value['conversation_id']) or type(value['lead_id']) is not int
            or not 0 < value['lead_id'] < 2**63):
        raise TenantLinearError('Укажите точный личный WhatsApp-диалог.', 400)
    return str(uuid.UUID(value['channel_id'])), value['conversation_id'], value['lead_id']


def _config(tenant, actor, channel):
    # Keep pair tokens compatible with established MCP tooling where enabled.
    secret = os.environ.get('AI_LEARNING_MCP_CURSOR_SECRET') or os.environ.get('TENANT_ENCRYPTION_KEY')
    return {'AI_LEARNING_MCP_TENANT_ID': tenant, 'actor_id': actor,
            'AI_LEARNING_MCP_CHANNEL_ID': channel, 'AI_LEARNING_MCP_CURSOR_SECRET': secret}


def _labels(rows):
    choices = []
    for row in rows:
        conversation = row['conversation_id']
        for value in (row.get('manual_display_name'), row.get('contact_name'), row.get('display_name')):
            name = str(value or '').strip()
            if (name and len(name) <= 180 and name not in {conversation, conversation.split('@')[0]}
                    and not re.fullmatch(r'[+0-9().\s-]+', name)
                    and not re.search(r'@(lid|s\.whatsapp\.net|g\.us|c\.us)$', name, re.I)
                    and not any(ord(c) < 32 or ord(c) == 127 for c in name)):
                choices.append({'lead_id': row['lead_id'], 'display_name': name})
                break
    return choices


def preview_token(cfg, precondition, routes):
    labels = sorted(_labels(routes), key=lambda label: label['lead_id'])
    return _token(cfg, 'browser-duplicate-preview', precondition, json.dumps(labels, sort_keys=True))


def _phone(canonical_route):
    # Only called after the core pair guard establishes one common phone.
    jid = normalize_phone_jid(canonical_route)
    return '+' + jid.split('@')[0] if jid else ''


def preview(profile, payload):
    if not isinstance(payload, dict) or set(payload) != {'left', 'right'}:
        raise TenantLinearError('Выберите два диалога.', 400)
    a, b = _route(payload['left']), _route(payload['right'])
    if a[0] != b[0] or a[1] == b[1] or a[2] == b[2]:
        raise TenantLinearError('Выберите разные диалоги одного канала.', 400)
    channel = a[0]
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, actor = _owner(cur, profile)
            _, _, policy = access(cur, profile, 'dialogs', action='update')
            rows = [assert_route(cur, tenant, policy, 'connect', conv, ch, lead, action='update')
                    for ch, conv, lead in (a, b)]
            if any(r.get('conversation_kind') not in ('contact', '') for r in rows):
                raise TenantLinearError('Групповые диалоги нельзя объединять.', 400)
            cfg = _config(tenant, actor, channel)
            _, _, _, items = _pairs(cur, cfg)
            item = next((x for x in items if {x['canonical_lead_id'], x['split_lead_id']} == {a[2], b[2]}), {})
            phone = _phone(next((r['conversation_id'] for r in rows
                                if r['lead_id'] == item.get('canonical_lead_id')), '')) if item.get('eligible') else ''
            conflicts = list(item.get('conflicts') or ([] if phone else ['verified_phone_required']))
            if phone:
                from tenant_connect_dialog_equivalence import assert_lifecycle_unaffected
                try:
                    assert_lifecycle_unaffected(cur,tenant,channel,[a[1],b[1]])
                except TenantLinearError:
                    conflicts.append('dialog_lifecycle_in_use')
            return {'eligible': bool(item.get('eligible') and phone and not conflicts),
                    'conflicts': conflicts,
                    'pair_id': item.get('pair_id', ''),
                    'precondition': preview_token(cfg,item['precondition'],rows) if item else '',
                    'canonical_lead_id': item.get('canonical_lead_id'), 'split_lead_id': item.get('split_lead_id'),
                    'phone': phone, 'name_choices': _labels(rows)}


def selection(canonical, rows, choices):
    """Revalidate the presentation selections under the central merge locks."""
    _, name_lead_id, phone_lead_id = choices
    by_lead = {row['lead_id']: row for row in rows}
    if len(by_lead) != 2 or phone_lead_id not in by_lead or not _phone(canonical):
        raise TenantLinearError('Номер не подтверждён для обоих диалогов.', 409)
    labels = {x['lead_id']: x['display_name'] for x in _labels(rows)}
    if name_lead_id is None and not labels:
        return None
    if name_lead_id not in labels:
        raise TenantLinearError('Выберите доступное имя.', 409)
    previous = next(row.get('manual_display_name', '') for row in rows if row['conversation_id'] == canonical)
    return {'selected': labels[name_lead_id], 'previous': previous}


def merge(profile, payload):
    expected = {'channel_id', 'pair_id', 'precondition', 'request_id', 'confirm',
                'name_lead_id', 'phone_lead_id'}
    if not isinstance(payload, dict) or set(payload) != expected or payload['confirm'] is not True:
        raise TenantLinearError('Подтвердите действие над диалогами.', 400)
    channel = str(uuid.UUID(payload['channel_id']))
    name, phone = payload['name_lead_id'], payload['phone_lead_id']
    if (name is not None and type(name) is not int) or type(phone) is not int:
        raise TenantLinearError('Выберите имя и номер.', 400)
    choices = (channel, name, phone)
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, actor = _owner(cur, profile)
            access(cur, profile, 'dialogs', action='update')
            result = execute(cur, _config(tenant, actor, channel),
                {k: payload[k] for k in ('pair_id', 'precondition', 'request_id')} | {'operation': 'merge'},
                display_selection=choices)
        conn.commit()
    return result


def save_phone(profile, payload):
    if not isinstance(payload, dict) or set(payload) != _ROUTE | {'phone'}:
        raise TenantLinearError('Укажите диалог и номер.', 400)
    channel, conversation, lead = _route({k: payload[k] for k in _ROUTE})
    value = payload['phone']
    if not isinstance(value, str) or len(value) > 40:
        raise TenantLinearError('Укажите номер в международном формате.', 400)
    value = value.strip()
    digits = normalize_phone(value)
    if value and (not value.startswith('+') or not digits or len(digits) < 7 or digits == conversation.split('@')[0]):
        raise TenantLinearError('Укажите номер в международном формате.', 400)
    phone = '+' + digits if digits else ''
    from tenant_connect_transport import _schema
    from tenant_connect_dialog_equivalence import canonical
    with _connect() as conn:
        _schema(conn)
        with conn.cursor() as cur:
            tenant, actor = _owner(cur, profile)
            _, _, policy = access(cur, profile, 'dialogs', action='update')
            route = assert_route(cur, tenant, policy, 'connect', conversation, channel, lead, action='update')
            if route.get('conversation_kind') not in ('contact', '') or canonical(cur, tenant, channel, conversation) != conversation:
                raise TenantLinearError('Доступен только основной личный диалог.', 409)
            if phone and (normalize_phone(route.get('phone')) or
                          collect(cur, tenant, channel, [conversation])['phones_by_identity'].get(conversation)):
                raise TenantLinearError('Подтверждённый номер нельзя заменить.', 409)
            from tenant_schema_preflight import ready
            if not ready(cur, PHONE_SCHEMA, []):
                cur.execute("SELECT pg_advisory_xact_lock(hashtextextended('dialog-display-phone-schema',0))")
                cur.execute("""CREATE TABLE IF NOT EXISTS saas_connect_dialog_display_phones (
                  tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
                  channel_id UUID NOT NULL, conversation_id TEXT NOT NULL, lead_id BIGINT NOT NULL,
                  phone TEXT NOT NULL CHECK (phone ~ '^[+][1-9][0-9]{6,14}$'),
                  PRIMARY KEY(tenant_id,channel_id,conversation_id,lead_id),
                  FOREIGN KEY (tenant_id,channel_id,conversation_id)
                    REFERENCES saas_connect_conversations(tenant_id,connect_channel_id,conversation_id) ON DELETE CASCADE)""")
            if phone:
                cur.execute("""INSERT INTO saas_connect_dialog_display_phones
                  (tenant_id,channel_id,conversation_id,lead_id,phone) VALUES(%s::uuid,%s::uuid,%s,%s,%s)
                  ON CONFLICT(tenant_id,channel_id,conversation_id,lead_id) DO UPDATE SET phone=EXCLUDED.phone""",
                  (tenant, channel, conversation, lead, phone))
            else:
                cur.execute("""DELETE FROM saas_connect_dialog_display_phones WHERE
                  tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id=%s AND lead_id=%s""",
                  (tenant, channel, conversation, lead))
            _audit(cur, tenant, actor, 'dialog_display_phone_set', 'connect_conversation', str(lead),
                   {'channel_id': channel, 'display_only': True, 'cleared': not phone})
        conn.commit()
    return {'display_phone': phone}
