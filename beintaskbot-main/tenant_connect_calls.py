"""Validated service events; calls never enter customer-message automation."""
from datetime import datetime, timezone
import hashlib
import re
import uuid


def validate(event):
    data = event.get('data')
    if not isinstance(data, dict):
        raise ValueError('invalid call data')
    expected_status = {'call.missed': 'missed', 'call.rejected': 'rejected', 'call.invalidated': 'invalidated', 'call.observed': 'observed'}.get(event.get('type'))
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
    if expected_status == 'observed' or is_observation_correction(data):
        if data.get('source') != 'evolution' or (expected_status == 'observed' and data.get('outcome') != 'unknown'):
            raise ValueError('invalid call observation')
        try:
            offer = datetime.fromisoformat(data['offerOccurredAt'].replace('Z', '+00:00'))
            terminated = datetime.fromisoformat(data['terminatedAt'].replace('Z', '+00:00'))
            data_stamp = datetime.fromisoformat(data['occurredAt'].replace('Z', '+00:00'))
            if (offer.tzinfo is None or terminated.tzinfo is None
                    or not 0 <= (terminated - offer).total_seconds() <= 1800
                    or stamp != terminated or data_stamp.tzinfo is None or data_stamp != terminated):
                raise ValueError('invalid call evidence time')
        except (KeyError, AttributeError, TypeError, ValueError):
            raise ValueError('invalid call evidence time') from None
        if any(key in data for key in ('duration', 'durationSeconds', 'duration_seconds', 'durationMs')):
            raise ValueError('unsupported call duration')
    return stamp.astimezone(timezone.utc).isoformat()


def is_observation_correction(data):
    return data.get('callStatus') == 'invalidated' and data.get('correctionOf') == 'call.observed'


def revision(data):
    status = data.get('callStatus')
    value = data.get('callRevision', 1)
    if type(value) is not int or status not in {'missed', 'rejected', 'invalidated', 'observed'}:
        raise ValueError('invalid call revision')
    if status == 'observed':
        if value != 0 or 'correctionOf' in data or data.get('outcome') != 'unknown':
            raise ValueError('invalid call observation revision')
    elif status == 'invalidated':
        if value != 2 or data.get('correctionOf') not in {'call.rejected', 'call.observed'}:
            raise ValueError('invalid call correction')
        if is_observation_correction(data) and ('outcome' in data or data.get('correctionReason') != 'provider_contradiction'):
            raise ValueError('invalid observation correction')
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
            or prior.get('source') != data.get('source')
            or not _same_stored_route(cur, tenant, channel, prior.get('conversationId'), conversation)):
        raise ValueError('call scope conflict')
    prior_revision = revision(prior)
    statuses = {prior.get('callStatus'), data['callStatus']}
    if is_observation_correction(prior) or is_observation_correction(data):
        # Revision numbers belong to their correction provenance. Rev2 retracting
        # observation cannot erase independently proven rev1 (in either order).
        prior_neutral = prior.get('callStatus') == 'observed' or is_observation_correction(prior)
        incoming_neutral = data.get('callStatus') == 'observed' or is_observation_correction(data)
        if prior_neutral and incoming_neutral:
            if any(prior.get(key) != data.get(key) for key in ('offerOccurredAt', 'terminatedAt', 'occurredAt')):
                raise ValueError('call scope conflict')
            if is_observation_correction(prior):
                return False
        elif not prior_neutral:
            return False  # Known outcome or its own correction wins.
        corrected = {**prior, **data, 'callRevision': incoming_revision, 'occurredAt': data.get('occurredAt'), 'conversationId': prior['conversationId'],
                     '_crm_call_observation': prior.get('_crm_call_observation') or prior}
        corrected.pop('outcome', None)
        if data['callStatus'] in {'missed', 'rejected'}:
            corrected.pop('correctionOf', None)
            corrected.pop('correctionReason', None)
        cur.execute("""UPDATE saas_crm_messages SET message_type=%s,raw=%s::jsonb
          WHERE tenant_id=%s::uuid AND external_id=%s""",
          ('call_' + data['callStatus'], _json(corrected), tenant, external))
        return False
    if 'observed' in statuses:
        if (prior.get('direction') != 'incoming' or data.get('direction') != 'incoming'
                or prior.get('source') != 'evolution' or data.get('source') != 'evolution'):
            raise ValueError('call scope conflict')
        if statuses == {'observed'}:
            if any(prior.get(key) != data.get(key) for key in ('offerOccurredAt', 'terminatedAt', 'outcome')):
                raise ValueError('call scope conflict')
            return False
        if incoming_revision < prior_revision:
            return False  # A late neutral observation never downgrades proven status.
        # Keep original source identity/time and retain neutral evidence even
        # when a late proven outcome replaces the displayed classification.
        corrected = {**prior, **data, 'callRevision': incoming_revision, 'occurredAt': data.get('occurredAt'), 'conversationId': prior['conversationId'],
                     '_crm_call_observation': prior}
        corrected.pop('outcome', None)
        cur.execute("""UPDATE saas_crm_messages SET message_type=%s,raw=%s::jsonb
          WHERE tenant_id=%s::uuid AND external_id=%s""",
          ('call_' + data['callStatus'], _json(corrected), tenant, external))
        return False
    if len(statuses) > 1 and statuses != {'rejected', 'invalidated'}:
        raise ValueError('call scope conflict')
    if incoming_revision <= prior_revision:
        evidence = (prior.get('_crm_call_correction') or {}).get('event') or prior
        if incoming_revision == prior_revision and evidence.get('occurredAt') != data.get('occurredAt'):
            raise ValueError('call scope conflict')
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
