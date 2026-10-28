"""Durable, channel-scoped direct identity resolution before conversation writes.

Only provider IDs and signed pairs establish aliases. Display names, avatars,
stored phone labels and equal message text never establish identity.
"""
import json
import re


def direct_identity(value):
    value = str(value or '').strip()
    return value if re.fullmatch(r'[1-9][0-9]{5,22}@(s\.whatsapp\.net|c\.us|lid)', value) else ''


def phone_jid(value):
    value = direct_identity(value)
    return value.split('@', 1)[0] + '@s.whatsapp.net' if value.endswith(('@s.whatsapp.net', '@c.us')) else ''


def _aliases(value):
    phone = phone_jid(value)
    return {phone, phone.replace('@s.whatsapp.net', '@c.us')} if phone else {value}


def verified_phone_jid(cur, tenant, channel, conversation):
    """Phone evidence for a canonical LID, without consulting mutable labels."""
    cur.execute('''SELECT identity_key FROM saas_connect_direct_identities
      WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND canonical_conversation_id=%s''',
      (tenant, channel, conversation))
    phones = {phone_jid(r['identity_key']) for r in cur.fetchall()} | {phone_jid(conversation)}
    phones.discard('')
    return next(iter(phones)) if len(phones) == 1 else ''


def canonical_conversation(cur, tenant, channel, conversation, data, metadata):
    """Resolve and reserve identities inside the caller's message transaction.

    Locks cover absent rows too and remain held through route/message/receipt
    commit. Their sorted order is shared by paired and unpaired deliveries.
    The alias primary key is the durable uniqueness boundary across workers.
    An existing route wins over a preferred JID; historical splits are flagged,
    never merged or removed by ingestion.
    """
    current = direct_identity(conversation)
    if metadata.get('kind') == 'group' or not current:
        return conversation, False
    pair = data.get('directIdentity')
    pair = pair if isinstance(pair, dict) else {}
    jid, lid = direct_identity(pair.get('jid')), direct_identity(pair.get('lid'))
    conflict = data.get('conversationIdentityConflict') is True
    complete = bool(jid and lid)
    verified = (complete and phone_jid(jid) and lid.endswith('@lid')
                and (current == lid or phone_jid(current) == phone_jid(jid)))
    if complete and not verified:
        conflict = True
    aliases = _aliases(current)
    if verified and not conflict:
        aliases |= _aliases(jid) | {lid}
    for identity in sorted(aliases):
        cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                    ('connect-direct:' + json.dumps([str(tenant), str(channel), identity]),))
    cur.execute('''SELECT identity_key,canonical_conversation_id FROM saas_connect_direct_identities
      WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND identity_key=ANY(%s::text[])''',
      (tenant, channel, sorted(aliases)))
    known = {str(r['identity_key']): str(r['canonical_conversation_id']) for r in cur.fetchall()}
    targets = set(known.values())
    # Partial/missing metadata must still reuse a previously committed alias.
    fallback = known.get(current, conversation)
    if len(targets) > 1:
        if verified and not conflict:
            from tenant_connect_owner_identity import active_link,provider_conflicts
            assertion=active_link(cur,tenant,channel,current)
            if (assertion and targets<={assertion['canonical_id'],assertion['source_id']}
                    and lid==assertion['source_id'] and phone_jid(jid).split('@')[0]==assertion['phone']
                    and not provider_conflicts(cur,tenant,channel,assertion)):
                # This receipt really supplies provider evidence. Preserve the
                # original independent aliases; the owner overlay routes it.
                metadata['phone_jid']=phone_jid(jid)
                metadata['direct_aliases']=sorted(aliases)
                return fallback,False
        return fallback, True
    cur.execute('''SELECT conversation_id FROM saas_connect_conversations
      WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=ANY(%s::text[])''',
      (tenant, channel, sorted(aliases | targets)))
    routes = {str(r['conversation_id']) for r in cur.fetchall()}
    # A prior explicit reconciliation may leave a verified mapping next to a
    # historical alias row. Preserve its established routing on unpaired events;
    # a new pair must not silently reconcile two independently owned histories.
    if len(routes) > 1 and (verified or not targets):
        from tenant_connect_dialog_equivalence import same_routes
        if not same_routes(cur,tenant,channel,routes):
            return fallback, True
    canonical = next(iter(targets or routes), phone_jid(jid) if verified and not conflict else conversation)
    cur.execute('''SELECT identity_key FROM saas_connect_direct_identities
      WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND canonical_conversation_id=%s''',
      (tenant, channel, canonical))
    established = {str(r['identity_key']) for r in cur.fetchall()} | {canonical}
    phones = {phone_jid(x) for x in established | aliases} - {''}
    lids = {x for x in established | aliases if x.endswith('@lid')}
    if len(phones) > 1 or len(lids) > 1:
        return fallback, True
    if conflict:
        return fallback, True
    for identity in sorted(aliases):
        cur.execute('''INSERT INTO saas_connect_direct_identities
          (tenant_id,connect_channel_id,identity_key,canonical_conversation_id)
          VALUES(%s::uuid,%s::uuid,%s,%s)
          ON CONFLICT(tenant_id,connect_channel_id,identity_key) DO NOTHING''',
          (tenant, channel, identity, canonical))
    # Fail atomically if a writer outside this locking protocol won an alias.
    # Never return an unchecked local winner after ON CONFLICT DO NOTHING.
    cur.execute('''SELECT canonical_conversation_id FROM saas_connect_direct_identities
      WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND identity_key=ANY(%s::text[])''',
      (tenant, channel, sorted(aliases)))
    if {str(r['canonical_conversation_id']) for r in cur.fetchall()} != {canonical}:
        from psycopg.errors import SerializationFailure
        raise SerializationFailure('Direct identity changed during reservation')
    # Route identity can remain a LID. The verified phone is separate and is
    # used for contact linkage / employee recognition, never guessed from LID.
    metadata['phone_jid'] = next(iter(phones), '')
    metadata['direct_aliases'] = sorted(established | aliases)
    return canonical, False


def record_conflict(cur, tenant, channel, canonical, original, data, event_id):
    """Retain conflicting evidence even when a message/event is a replay.

    First evidence per affected identity is immutable. This additive ledger
    neither replaces a message's original raw payload nor changes its owner.
    """
    pair = data.get('directIdentity')
    pair = pair if isinstance(pair, dict) else {}
    keys = {canonical, original}
    jid,lid=direct_identity(pair.get('jid')),direct_identity(pair.get('lid'))
    related=bool(phone_jid(jid) and lid.endswith('@lid')
        and (original==lid or phone_jid(original)==phone_jid(jid)))
    for value in (original, *( (jid,lid) if related else () )):
        value = direct_identity(value)
        if value:
            keys |= _aliases(value)
    evidence = json.dumps({'originalConversationId': original, 'canonicalConversationId': canonical,
        'directIdentity': {key: direct_identity(pair.get(key)) for key in ('jid', 'lid')},
        'pair_matches_conversation': related})
    for identity in sorted(keys):
        cur.execute('''INSERT INTO saas_connect_identity_conflicts
          (tenant_id,connect_channel_id,conversation_id,event_id,evidence)
          VALUES(%s::uuid,%s::uuid,%s,%s,%s::jsonb)
          ON CONFLICT(tenant_id,connect_channel_id,conversation_id) DO NOTHING''',
          (tenant, channel, identity, event_id, evidence))

        cur.execute('''INSERT INTO saas_connect_identity_conflict_events
          (tenant_id,connect_channel_id,conversation_id,event_id,evidence)
          VALUES(%s::uuid,%s::uuid,%s,%s,%s::jsonb)
          ON CONFLICT(tenant_id,connect_channel_id,conversation_id,event_id) DO NOTHING''',
          (tenant,channel,identity,event_id,evidence))
        state_ids={identity}
        # An explicit owner merge overlays the source identity onto a
        # canonical conversation. Fence both state rows when either side is
        # contradicted, while keeping the alias/evidence ledger unchanged.
        from tenant_connect_owner_identity import active_link
        owner_link=active_link(cur,tenant,channel,identity)
        if owner_link:
            state_ids.update((owner_link.get('canonical_id'),owner_link.get('source_id')))
        for state_id in sorted(str(value) for value in state_ids if value):
            # A dialog may already have received an automatic ownership
            # placeholder before a later provider assertion proves its identity
            # ambiguous. Surface the quarantine on that managed placeholder as
            # well. Explicit operator decisions remain untouched.
            cur.execute('''INSERT INTO saas_ai_conversation_state
              (tenant_id,provider,channel_id,conversation_id,agent_id,state,reason)
              SELECT %s::uuid,'connect',%s,%s,NULL,'paused','conversation_identity_conflict'
              WHERE EXISTS(SELECT 1 FROM saas_connect_conversations
                WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s)
              ON CONFLICT(tenant_id,provider,channel_id,conversation_id) DO UPDATE
                SET state='paused',reason='conversation_identity_conflict',
                    revision=saas_ai_conversation_state.revision+1,updated_at=now()
              WHERE saas_ai_conversation_state.agent_assignment_id IS NULL
                AND (saas_ai_conversation_state.reason LIKE 'automatic_assignment_%%'
                  OR saas_ai_conversation_state.reason='conversation_identity_conflict'
                  OR (saas_ai_conversation_state.agent_id IS NULL
                    AND saas_ai_conversation_state.state='active'
                    AND COALESCE(saas_ai_conversation_state.reason,'')=''))''',
              (tenant,channel,state_id,tenant,channel,state_id))
