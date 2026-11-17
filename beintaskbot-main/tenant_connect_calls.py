"""Validated service events; calls never enter customer-message automation."""
from datetime import datetime, timezone
import hashlib
import re
import uuid


def validate(event):
    data = event.get('data')
    if not isinstance(data, dict):
        raise ValueError('invalid call data')
    expected_status = {'call.missed': 'missed', 'call.rejected': 'rejected', 'call.invalidated': 'invalidated'}.get(event.get('type'))
    if expected_status is None:
        raise ValueError('unsupported call event')
    call_id = data.get('externalCallId')
    if not isinstance(call_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', call_id):
        raise ValueError('invalid call id')
    if (data.get('direction') != 'incoming' or data.get('type') != 'call'
            or data.get('callStatus') != expected_status or type(data.get('isVideo')) is not bool
            or str(data.get('conversationType') or 'direct').lower() not in {'direct','contact','personal'}
            or str(data.get('chatType') or 'direct').lower() not in {'direct','contact','personal'}
            or data.get('fromMe') is True or data.get('isGroup') is not False
            or not re.fullmatch(r'[0-9]{5,22}@(s[.]whatsapp[.]net|c[.]us|lid)', str(data.get('conversationId') or ''))):
        raise ValueError('unsupported call event')
    revision(data)  # Strict fixed protocol; never accept arbitrary revision jumps.
    occurred = event.get('occurred_at')
    try:
        stamp = datetime.fromisoformat(occurred.replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError('missing timezone')
    except (AttributeError, TypeError, ValueError):
        raise ValueError('invalid call timestamp') from None
    return stamp.astimezone(timezone.utc).isoformat()


def revision(data):
    status = data.get('callStatus')
    value = data.get('callRevision', 1)
    if type(value) is not int or status not in {'missed', 'rejected', 'invalidated'}:
        raise ValueError('invalid call revision')
    if status == 'invalidated':
        if value != 2 or data.get('correctionOf') != 'call.rejected':
            raise ValueError('invalid call correction')
    elif value != 1 or 'correctionOf' in data:
        raise ValueError('invalid call revision')
    return value


def _same_stored_route(cur, tenant, channel, previous, current):
    if previous == current:
        return True
    # Ingestion already holds provider-alias locks then the current canonical
    # dialogue lock. Reconciliation/rollback writers lock old and new canonical
    # dialogues before changing mappings, so a mapping to current stays fenced.
    # Never acquire another alias lock here after the message row lock.
    from tenant_connect_direct_identity import established_canonical_conversation
    from tenant_connect_dialog_equivalence import canonical
    established = established_canonical_conversation(cur, tenant, channel, previous)
    # Empty includes absent AND ambiguous proof. Neither may be guessed from
    # names, phone labels, call IDs or a coincidentally shared CRM lead.
    return bool(established and canonical(cur, tenant, channel, established) == current)


def record(cur, tenant, channel, conversation, lead_id, data, occurred):
    """Source/call-keyed monotonic service-event projection, never a customer message.

    INSERT conflict locking serializes concurrent first deliveries. FOR UPDATE then
    serializes revision checks/updates for existing rows. Invalidation is a neutral
    tombstone: late original retries cannot reinstate a rejected classification.
    """
    from tenant_platform import _json
    incoming_revision = revision(data)
    if data.get('conversationIdentityConflict') or data.get('conversationId') != conversation:
        raise ValueError('call scope conflict')
    digest = hashlib.sha256((channel + '\0' + data['externalCallId']).encode()).hexdigest()
    external = 'connect:call:' + digest
    cur.execute('''INSERT INTO saas_crm_messages
      (id,tenant_id,kommo_lead_id,external_id,direction,channel,author_name,body,message_type,media_url,happened_at,raw)
      VALUES(%s,%s::uuid,%s,%s,'system',%s,'WhatsApp','',%s,'',%s::timestamptz,%s::jsonb)
      ON CONFLICT(tenant_id,external_id) DO NOTHING RETURNING id''',
      (uuid.uuid4(), tenant, lead_id, external, 'connect:' + channel, 'call_' + data['callStatus'], occurred, _json(data)))
    inserted = bool(cur.fetchone())
    if inserted:
        return True
    cur.execute('SELECT channel,kommo_lead_id,raw FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id=%s FOR UPDATE', (tenant, external))
    previous = cur.fetchone()
    from tenant_connect_dialog_equivalence import lead_members
    prior = (previous or {}).get('raw') or {}
    if (not previous or previous['channel'] != 'connect:' + channel
            or previous['kommo_lead_id'] not in lead_members(cur,tenant,channel,lead_id)
            or prior.get('isVideo') is not data['isVideo']
            or prior.get('externalCallId') != data['externalCallId']
            or not _same_stored_route(cur, tenant, channel, prior.get('conversationId'), conversation)):
        raise ValueError('call scope conflict')
    prior_revision = revision(prior)
    statuses = {prior.get('callStatus'), data['callStatus']}
    if len(statuses) > 1 and statuses != {'rejected', 'invalidated'}:
        raise ValueError('call scope conflict')
    if incoming_revision <= prior_revision:
        return False
    # Only rejected(1) -> invalidated(2) is admitted. Keep original occurrence
    # time, identity and provenance; do not invent a completed/answered call.
    if prior.get('callStatus') != 'rejected' or data['callStatus'] != 'invalidated':
        raise ValueError('call scope conflict')
    corrected = {**prior, 'callStatus': 'invalidated', 'callRevision': 2,
                 'correctionOf': 'call.rejected', '_crm_call_correction': {
                     'previousStatus': 'rejected', 'previousRevision': prior_revision,
                     'event': data, 'occurredAt': occurred}}
    cur.execute('''UPDATE saas_crm_messages SET message_type='call_invalidated',raw=%s::jsonb
      WHERE tenant_id=%s::uuid AND external_id=%s''', (_json(corrected), tenant, external))
    return False
