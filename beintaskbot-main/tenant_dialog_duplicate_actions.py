"""Exact owner-only inspection and display-only phone editing."""
import re
import uuid

from tenant_ai_agents_store import _audit
from tenant_connect_direct_identity import direct_identity
from tenant_connect_direct_reconcile import _owner
from tenant_connect_phone_evidence import normalize_phone, normalize_phone_jid
from tenant_linear_policy import TenantLinearError
from tenant_operator_access import access, assert_route
from tenant_platform import _connect


_ROUTE = {'provider', 'channel_id', 'conversation_id', 'lead_id'}
PHONE_SCHEMA = {'saas_connect_dialog_display_phones': ('tenant_id', 'channel_id', 'conversation_id', 'lead_id', 'phone')}


def prepare_schema(conn):
    """Startup-only, on the existing bounded migration connection."""
    from tenant_schema_preflight import ready
    with conn.cursor() as cur:
        if ready(cur, PHONE_SCHEMA, []):
            return
    with conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS saas_connect_dialog_display_phones (
          tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
          channel_id UUID NOT NULL, conversation_id TEXT NOT NULL, lead_id BIGINT NOT NULL,
          phone TEXT NOT NULL CHECK (phone ~ '^[+][1-9][0-9]{6,14}$'),
          PRIMARY KEY(tenant_id,channel_id,conversation_id,lead_id),
          FOREIGN KEY (tenant_id,channel_id,conversation_id)
            REFERENCES saas_connect_conversations(tenant_id,connect_channel_id,conversation_id) ON DELETE CASCADE)""")


def _require_schema(cur, columns):
    from tenant_schema_preflight import ready
    if not ready(cur, columns, []):
        raise TenantLinearError('Настройки диалогов временно недоступны.', 503)


def _route(value):
    if (not isinstance(value, dict) or set(value) != _ROUTE or value['provider'] != 'connect'
            or not isinstance(value['conversation_id'], str)
            or not direct_identity(value['conversation_id']) or type(value['lead_id']) is not int
            or not 0 < value['lead_id'] < 2**63):
        raise TenantLinearError('Укажите точный личный WhatsApp-диалог.', 400)
    return str(uuid.UUID(value['channel_id'])), value['conversation_id'], value['lead_id']


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


def preview(profile, payload):
    if not isinstance(payload, dict) or set(payload) != {'left', 'right'}:
        raise TenantLinearError('Выберите два диалога.', 400)
    a, b = _route(payload['left']), _route(payload['right'])
    if a[0] != b[0] or a[1] == b[1] or a[2] == b[2]:
        raise TenantLinearError('Выберите разные диалоги одного канала.', 400)
    with _connect() as conn, conn.cursor() as cur:
        _budget(cur)
        tenant, _ = _owner(cur, profile)
        _, _, policy = access(cur, profile, 'dialogs')
        rows = [assert_route(cur, tenant, policy, 'connect', conv, ch, lead, allow_closed_archive=True)
                for ch, conv, lead in sorted((a, b))]
        if any(r.get('conversation_kind') not in ('contact', '') for r in rows):
            raise TenantLinearError('Выберите личные WhatsApp-диалоги.',400)
        return {'eligible':False,'conflicts':['merge_deferred'],'name_choices':_labels(rows)}


def _budget(cur):
    cur.execute("SET LOCAL statement_timeout='5s'")
    cur.execute("SET LOCAL lock_timeout='2s'")


def _phone_locked(cur, tenant, channel, conversation, route):
    # Use current exact route data only; never load historical message payloads.
    if normalize_phone_jid(conversation):
        return True
    stored=normalize_phone(route.get('phone'))
    if stored and stored != conversation.split('@')[0]:
        return True
    cur.execute("""SELECT
      EXISTS(SELECT 1 FROM saas_connect_direct_identities i
        WHERE i.tenant_id=%s::uuid AND i.connect_channel_id=%s::uuid AND i.canonical_conversation_id=%s
          AND i.identity_key ~ '^[1-9][0-9]{6,14}@(s[.]whatsapp[.]net|c[.]us)$')
      OR EXISTS(SELECT 1 FROM saas_connect_identity_conflicts i
        WHERE i.tenant_id=%s::uuid AND i.connect_channel_id=%s::uuid AND i.conversation_id=%s) AS blocked""",
      (tenant,channel,conversation)*2)
    return cur.fetchone()['blocked']


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
    from tenant_connect_dialog_equivalence import canonical
    with _connect() as conn:
        with conn.cursor() as cur:
            _budget(cur)
            tenant, actor = _owner(cur, profile)
            _, _, policy = access(cur, profile, 'dialogs', action='update')
            from tenant_dialog_access import require_source
            from tenant_dialog_lifecycle import _row as lifecycle_row, enabled
            require_source(cur, tenant, policy, 'connect', channel, 'update')
            _, _, view_policy = access(cur, profile, 'dialogs')
            route = assert_route(cur, tenant, view_policy, 'connect', conversation, channel, lead)
            # The exact protected view holds the lifecycle lock through commit.
            # Waiting has no owner to override; closed/dismissed stay read-only.
            if enabled(cur, tenant):
                workflow = lifecycle_row(cur, tenant, channel, conversation)
                if workflow and workflow['state'] not in {'waiting','accepted'}:
                    raise TenantLinearError('Закрытый диалог доступен только для чтения.', 409)
            if route.get('conversation_kind') not in ('contact', '') or canonical(cur, tenant, channel, conversation) != conversation:
                raise TenantLinearError('Доступен только основной личный диалог.', 409)
            if phone and _phone_locked(cur, tenant, channel, conversation, route):
                raise TenantLinearError('Подтверждённый номер нельзя заменить.', 409)
            _require_schema(cur, PHONE_SCHEMA)
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
