"""Validated service events; calls never enter customer-message automation."""
from datetime import datetime, timezone
import hashlib
import re
import uuid


def validate(event):
    data = event.get('data')
    if not isinstance(data, dict):
        raise ValueError('invalid call data')
    call_id = data.get('externalCallId')
    if not isinstance(call_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', call_id):
        raise ValueError('invalid call id')
    if (data.get('direction') != 'incoming' or data.get('type') != 'call'
            or data.get('callStatus') != 'missed' or type(data.get('isVideo')) is not bool
            or str(data.get('conversationType') or 'direct').lower() not in {'direct','contact','personal'}
            or str(data.get('chatType') or 'direct').lower() not in {'direct','contact','personal'}
            or data.get('fromMe') is True or data.get('isGroup') is not False
            or not re.fullmatch(r'[0-9]{5,22}@(s[.]whatsapp[.]net|c[.]us|lid)', str(data.get('conversationId') or ''))):
        raise ValueError('unsupported call event')
    occurred = event.get('occurred_at')
    try:
        stamp = datetime.fromisoformat(occurred.replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError('missing timezone')
    except (AttributeError, TypeError, ValueError):
        raise ValueError('invalid call timestamp') from None
    return stamp.astimezone(timezone.utc).isoformat()


def record(cur, tenant, channel, conversation, lead_id, data, occurred):
    """Deduplicate only exact call IDs within source; no text/time matching."""
    from tenant_platform import _json
    digest = hashlib.sha256((channel + '\0' + data['externalCallId']).encode()).hexdigest()
    external = 'connect:call:' + digest
    cur.execute('''INSERT INTO saas_crm_messages
      (id,tenant_id,kommo_lead_id,external_id,direction,channel,author_name,body,message_type,media_url,happened_at,raw)
      VALUES(%s,%s::uuid,%s,%s,'system',%s,'WhatsApp','','call_missed','',%s::timestamptz,%s::jsonb)
      ON CONFLICT(tenant_id,external_id) DO NOTHING RETURNING id''',
      (uuid.uuid4(), tenant, lead_id, external, 'connect:' + channel, occurred, _json(data)))
    inserted = bool(cur.fetchone())
    if not inserted:
        cur.execute('SELECT channel,kommo_lead_id,raw FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id=%s', (tenant, external))
        previous = cur.fetchone()
        from tenant_connect_dialog_equivalence import lead_members
        if (not previous or previous['channel'] != 'connect:' + channel
                or previous['kommo_lead_id'] not in lead_members(cur,tenant,channel,lead_id)
                or (previous.get('raw') or {}).get('isVideo') is not data['isVideo']):
            raise ValueError('call scope conflict')
    return inserted
