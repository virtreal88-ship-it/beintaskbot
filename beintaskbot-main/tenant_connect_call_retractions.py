"""Durable observation-only retractions. Never resolve identities or create routes."""
import hashlib
import json


def call_key(channel, call_id):
    return 'connect:call:' + hashlib.sha256((channel + '\0' + call_id).encode()).hexdigest()


def lock(cur, tenant, channel, call_id):
    # Always precedes alias/dialog/message locks in call ingestion. No other
    # writer acquires this lock after acquiring a row or dialogue lock.
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                ('connect-call:' + tenant + ':' + channel + ':' + call_id,))


def _peer(data):
    return data.get('_crm_provider_conversation_id') or data.get('conversationId')


def _pair(data):
    pair = data.get('directIdentity')
    return (pair.get('jid'), pair.get('lid')) if isinstance(pair, dict) else (None, None)


def _check_scope(previous, incoming, *, timing):
    if (previous.get('source') != incoming.get('source')
            or previous.get('externalCallId') != incoming.get('externalCallId')
            or previous.get('isVideo') is not incoming.get('isVideo')
            or _peer(previous) != incoming.get('conversationId')
            or (previous.get('originalConversationId') or _peer(previous)) != (incoming.get('originalConversationId') or incoming.get('conversationId'))
            or _pair(previous) != _pair(incoming)):
        raise ValueError('call scope conflict')
    if timing and any(previous.get(key) != incoming.get(key)
                      for key in ('offerOccurredAt', 'terminatedAt', 'occurredAt')):
        raise ValueError('call scope conflict')


def check_retraction(cur, tenant, channel, data):
    cur.execute('''SELECT evidence FROM saas_connect_call_retractions
      WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND external_call_id=%s''',
      (tenant, channel, data['externalCallId']))
    row = cur.fetchone()
    if not row:
        return False
    observed = data.get('callStatus') == 'observed'
    _check_scope(row['evidence'], data, timing=observed)
    return observed  # Compatible proven evidence wins; scope never widens.


def retract(cur, tenant, channel, data):
    """Caller has authenticated signature/tenant/active channel and holds call lock.

    Receipt and ledger are committed atomically by caller. Even before the
    original arrives, this ledger prevents resurrection without creating a CRM
    message, lead, conversation, alias or AI job. Exact persisted provenance is
    used instead of consulting mutable identity mappings.
    """
    from tenant_connect_calls import is_observation_correction
    cur.execute('''SELECT evidence FROM saas_connect_call_retractions
      WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND external_call_id=%s''',
      (tenant, channel, data['externalCallId']))
    prior_ledger = cur.fetchone()
    if prior_ledger:
        _check_scope(prior_ledger['evidence'], data, timing=True)
    external = call_key(channel, data['externalCallId'])
    cur.execute('''SELECT channel,raw FROM saas_crm_messages
      WHERE tenant_id=%s::uuid AND external_id=%s FOR UPDATE''', (tenant, external))
    row = cur.fetchone()
    if row:
        prior = row['raw'] or {}
        if row['channel'] != 'connect:' + channel:
            raise ValueError('call scope conflict')
        observation = prior.get('_crm_call_observation') or prior
        _check_scope(observation, data, timing=observation.get('callStatus') == 'observed' or is_observation_correction(observation))
        if prior.get('callStatus') == 'observed':
            corrected = {**prior, 'callStatus':'invalidated', 'callRevision':2,
                'correctionOf':'call.observed', 'correctionReason':'provider_contradiction',
                '_crm_call_observation':prior}
            corrected.pop('outcome', None)
            cur.execute('''UPDATE saas_crm_messages SET message_type='call_invalidated',raw=%s::jsonb
              WHERE tenant_id=%s::uuid AND external_id=%s''', (json.dumps(corrected), tenant, external))
        elif prior.get('callStatus') not in {'missed', 'rejected', 'invalidated'}:
            raise ValueError('call scope conflict')
    # Store only the signed minimal immutable correlation facts, not internal
    # projection fields or arbitrary inbound metadata.
    evidence = {key:data.get(key) for key in ('source','externalCallId','conversationId',
        'isVideo','offerOccurredAt','terminatedAt','occurredAt','originalConversationId')}
    if isinstance(data.get('directIdentity'), dict):
        evidence['directIdentity'] = {key:data['directIdentity'].get(key) for key in ('jid','lid')}
    cur.execute('''INSERT INTO saas_connect_call_retractions
      (tenant_id,connect_channel_id,external_call_id,evidence)
      VALUES(%s::uuid,%s::uuid,%s,%s::jsonb)
      ON CONFLICT(tenant_id,connect_channel_id,external_call_id) DO NOTHING''',
      (tenant, channel, data['externalCallId'], json.dumps(evidence)))
