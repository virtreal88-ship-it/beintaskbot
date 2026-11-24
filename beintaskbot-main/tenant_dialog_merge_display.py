"""Read-only display choices for two exact, already-authorized WhatsApp routes."""
import hashlib
import json
import re
import unicodedata

from tenant_connect_direct_identity import direct_identity
from tenant_schema_preflight import ready

PHONE_SCHEMA = {'saas_connect_dialog_display_phones': ('tenant_id', 'channel_id', 'conversation_id', 'lead_id', 'phone')}
_PN = r'[1-9][0-9]{6,14}@(s[.]whatsapp[.]net|c[.]us)'
_PHONE = r'[+][1-9][0-9]{6,14}'


def display_name_choices(rows, *, strict=False):
    choices = []
    for row in rows:
        conversation = row['conversation_id']
        for value in (row.get('manual_display_name'), row.get('contact_name'), row.get('display_name')):
            name = str(value or '').strip()
            if (name and len(name) <= 180 and name not in {conversation, conversation.split('@')[0]}
                    and not re.fullmatch(r'[+0-9().\s-]+', name)
                    and not re.search(r'@(lid|s\.whatsapp\.net|g\.us|c\.us)$', name, re.I)
                    and not any(ord(c) < 32 or ord(c) == 127 for c in name)
                    and (not strict or (not re.search(r'@(lid|s\.whatsapp\.net|g\.us|c\.us)\b', name, re.I)
                         and not any(unicodedata.category(c) in ('Cc', 'Cf', 'Cs') for c in name)))):
                choices.append({'lead_id': row['lead_id'], 'display_name': name})
                break
    return choices


def _verified_mapping(cur, tenant, channel, conversation):
    # Existing index covers the exact canonical route. Normalize equivalent PN
    # domains; a second distinct number means there is no singleton proof.
    cur.execute('''SELECT DISTINCT split_part(identity_key,'@',1) AS digits
      FROM saas_connect_direct_identities
      WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid
        AND canonical_conversation_id=%s AND identity_key ~ %s LIMIT 2''',
        (tenant, channel, conversation, '^' + _PN + '$'))
    rows = cur.fetchall()
    if len(rows) != 1:
        return ''
    digits = rows[0]['digits']
    exact = [conversation, digits + '@s.whatsapp.net', digits + '@c.us']
    cur.execute('''SELECT
      EXISTS(SELECT 1 FROM saas_connect_identity_conflicts f WHERE f.tenant_id=%s::uuid
        AND f.connect_channel_id=%s::uuid AND f.conversation_id=ANY(%s::text[]))
      OR EXISTS(SELECT 1 FROM saas_connect_identity_conflict_events f WHERE f.tenant_id=%s::uuid
        AND f.connect_channel_id=%s::uuid AND f.conversation_id=ANY(%s::text[])) AS conflict''',
        (tenant, channel, exact) * 2)
    return '' if cur.fetchone()['conflict'] else '+' + digits


def _manual_phone(cur, tenant, channel, conversation, lead, *, lock=False):
    cur.execute('''SELECT phone FROM saas_connect_dialog_display_phones
      WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id=%s AND lead_id=%s'''
        + (' FOR SHARE' if lock else ''),
        (tenant, channel, conversation, lead))
    phone = (cur.fetchone() or {}).get('phone', '')
    return phone if re.fullmatch(_PHONE, phone) and phone[1:] != conversation.split('@')[0] else ''


def merge_display_options(cur, tenant, channel, rows, *, lock=False):
    """Fence stale display selections without using them as identity evidence.

    Authorization, source locks, and provider proof all belong to callers.
    Each row is the exact authorized record from assert_route. No route.phone,
    CRM message, or editable display value participates in identity evidence.
    """
    tenant, channel = str(tenant), str(channel)
    if len(rows) != 2:
        raise ValueError('Two exact routes required')
    keys = [(r['conversation_id'], r['lead_id']) for r in rows]
    if (len({k[0] for k in keys}) != 2 or len({k[1] for k in keys}) != 2
            or any(not direct_identity(conv) or direct_identity(conv) != conv
                   or type(lead) is not int or not 0 < lead < 2**63
                   for conv, lead in keys)
            or any(r.get('connect_channel_id') is not None and str(r['connect_channel_id']) != channel
                   or r.get('tenant_id') is not None and str(r['tenant_id']) != tenant for r in rows)):
        raise ValueError('Two distinct exact routes required')
    rows = sorted(rows, key=lambda r: (r['conversation_id'], r['lead_id']))
    names = display_name_choices(rows, strict=True)
    phones = []
    manual_ready = None
    for row in rows:
        conv, lead = row['conversation_id'], row['lead_id']
        if re.fullmatch(_PN, conv):
            phone = '+' + conv.split('@')[0]
        elif conv.endswith('@lid'):
            phone = _verified_mapping(cur, tenant, channel, conv)
        else:
            # A syntactically direct route outside the strict E.164 bound is
            # not a phone choice and must not be repaired from mutable data.
            continue
        if phone:
            phones.append({'lead_id': lead, 'display_phone': phone})
            continue
        if manual_ready is None:
            manual_ready = ready(cur, PHONE_SCHEMA, [])
        if manual_ready and (phone := _manual_phone(cur, tenant, channel, conv, lead, lock=lock)):
            phones.append({'lead_id': lead, 'display_phone': phone, 'display_only': True})
    revision = hashlib.sha256(json.dumps(
        ['history-merge-display-v1', tenant, channel, sorted(keys), names, phones],
        ensure_ascii=True, sort_keys=True, separators=(',', ':'),
    ).encode()).hexdigest()
    return {'name_choices': names, 'phone_choices': phones, 'display_revision': revision}


def exact_options(cur, tenant, channel, routes, *, lock=False):
    """Current metadata only, after both exact routes have been authorized."""
    (left,ll),(right,rl)=routes
    cur.execute('''SELECT tenant_id,connect_channel_id,conversation_id,lead_id,
      manual_display_name,contact_name,display_name
      FROM saas_connect_conversations WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid
        AND (conversation_id,lead_id) IN ((%s,%s),(%s,%s))
      ORDER BY conversation_id,lead_id''' + (' FOR SHARE' if lock else ''),
      (tenant,channel,left,ll,right,rl))
    rows=cur.fetchall()
    return merge_display_options(cur,tenant,channel,rows,lock=lock) if len(rows)==2 else None
