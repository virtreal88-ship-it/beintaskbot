"""Read-only provider phone evidence, scoped to one tenant and Connect channel.

Results are internal: callers must not expose identity keys or phone sets through
an API that only permits opaque references. This module never establishes a new
alias, merges rows, fetches provider data, or treats editable contact fields as
identity. Message references are persisted CRM UUIDs, never message text.
"""
from collections import defaultdict
from itertools import combinations
import re


def normalize_phone(value):
    """Canonical E.164 digits; no country inference or national-prefix repair."""
    if not isinstance(value, str):
        return ''
    value = value.strip()
    if not re.fullmatch(r'\+?[0-9][0-9 ()\-\.]*', value):
        return ''
    digits = re.sub(r'[ ()\-\.]', '', value).lstrip('+')
    return digits if re.fullmatch(r'[1-9][0-9]{5,14}', digits) else ''


def normalize_phone_jid(value):
    """A LID's numeric prefix is never a phone."""
    if not isinstance(value, str):
        return ''
    value = value.strip()
    local, separator, domain = value.rpartition('@')
    return normalize_phone(local) if separator and domain in {'s.whatsapp.net', 'c.us'} else ''


def _identity(value):
    if not isinstance(value, str):
        return ''
    value = value.strip()
    return value if (normalize_phone_jid(value)
                     or re.fullmatch(r'[1-9][0-9]{5,22}@lid', value)) else ''


def _pair(value, current):
    if not isinstance(value, dict):
        return None
    jid, lid = _identity(value.get('jid')), _identity(value.get('lid'))
    if not jid or not normalize_phone_jid(jid) or not lid.endswith('@lid'):
        return None
    if current != lid and normalize_phone_jid(current) != normalize_phone_jid(jid):
        return None
    return jid, lid


def _order(identity):
    return identity.endswith('@lid'), not identity.endswith('@s.whatsapp.net'), identity


def _belongs(identity, pair):
    return bool(pair and (identity == pair[1]
                or normalize_phone_jid(identity) == normalize_phone_jid(pair[0])))


def _present(cur, table):
    cur.execute('SELECT to_regclass(%s) AS present', (table,))
    return bool(cur.fetchone()['present'])


def collect(cur, tenant, channel, routes=None):
    """Return phone sets, proven pairs and conflicts without choosing a winner.

    ``routes`` may be an inventory mapping identity->row, route rows, or identity
    strings. It filters returned identities/pairs, not the underlying evidence:
    a contradictory fact outside the selection must still invalidate a pair.
    None returns all direct conversation routes in the selected channel.

    ``phones_by_identity`` values are sets of normalized E.164 digits. ``pairs``
    have canonical_id/split_id, sources, message_refs, phones, conflicts, verified
    and receipts. A verified pair with conflicts is NOT eligible for a merge.
    ``evidence_by_identity`` supplies sources and opaque CRM message references.
    Optional native/alias/ledger tables are read only when already present.
    """
    tenant, channel = str(tenant), str(channel)
    cur.execute('''SELECT conversation_id,lead_id,conversation_kind
      FROM saas_connect_conversations WHERE tenant_id=%s::uuid
        AND connect_channel_id=%s::uuid''', (tenant, channel))
    stored = {str(row['conversation_id']): dict(row) for row in cur.fetchall()}
    direct = {key for key, row in stored.items()
              if _identity(key) and row.get('conversation_kind') != 'group'}
    if routes is None:
        selected = direct
    else:
        values = routes.keys() if isinstance(routes, dict) else routes
        selected = {str(value.get('conversation_id') or '') if isinstance(value, dict)
                    else str(value) for value in values} & direct

    graph = defaultdict(set)
    facts = defaultdict(lambda: {'sources': set(), 'message_refs': set()})
    flagged, hard_flagged, reviewable_flags = set(), set(), set()

    def add(identity, source, reference=None):
        identity = _identity(identity)
        if not identity or stored.get(identity, {}).get('conversation_kind') == 'group':
            return ''
        graph[identity]
        facts[identity]['sources'].add(source)
        if reference:
            facts[identity]['message_refs'].add(str(reference))
        return identity

    def link(a, b, source, reference=None, conflict=False):
        a, b = add(a, source, reference), add(b, source, reference)
        if not a or not b:
            return
        graph[a].add(b)
        graph[b].add(a)
        if conflict:
            flagged.update((a, b))

    for identity in direct:
        add(identity, 'provider_phone_jid' if normalize_phone_jid(identity) else 'direct_route')

    # Only persisted Connect receipts with an existing, same-channel route are
    # used. Raw may retain the pre-canonical identity in originalConversationId.
    cur.execute('''SELECT m.id,m.external_id,m.raw,c.conversation_id,c.conversation_kind
      FROM saas_crm_messages m JOIN saas_connect_conversations c
        ON c.tenant_id=m.tenant_id AND c.lead_id=m.kommo_lead_id
          AND m.channel='connect:'||c.connect_channel_id::text
      WHERE m.tenant_id=%s::uuid AND c.connect_channel_id=%s::uuid
        AND m.external_id LIKE 'connect:%%' ORDER BY m.id''', (tenant, channel))
    receipts = {}
    for row in cur.fetchall():
        raw = row.get('raw')
        if not isinstance(raw, dict):
            continue
        route = str(row['conversation_id'])
        if raw.get('channelId') not in (None, channel):
            continue
        current = raw.get('originalConversationId') or raw.get('conversationId')
        route_matches = raw.get('conversationId') == route or current == route
        if not route_matches:
            continue
        reference = str(row['id'])
        is_group = route.endswith('@g.us') or row.get('conversation_kind') == 'group'
        if is_group:
            # Same contract as tenant_connect_group_history.page(): the sender
            # pair belongs to an exact, signed group/provider event.
            pair = _pair(raw.get('participantIdentity'), raw.get('participantJid'))
            if (pair and raw.get('conversationId') == route and raw.get('channelId') == channel
                    and re.fullmatch(r'[A-Za-z0-9_-]{5,200}', str(raw.get('externalMessageId') or ''))):
                link(*pair, 'signed_participant_identity', reference,
                     conflict=raw.get('participantIdentityConflict') is True
                     or raw.get('conversationIdentityConflict') is True)
                if raw.get('participantIdentityConflict') is True or raw.get('conversationIdentityConflict') is True:
                    hard_flagged.update(pair)
            continue
        if raw.get('isGroup') is True or raw.get('conversationType') in {'group', 'groupchat'}:
            continue
        if raw.get('conversationIdentityConflict') is True:
            flagged.add(route)
        pair = _pair(raw.get('directIdentity'), current)
        if raw.get('conversationIdentityConflict') is True:
            affected = {route} | (set(pair) if pair else set())
            if pair and _belongs(route, pair):
                reviewable_flags.update(affected)
            else:
                hard_flagged.update(affected)
        receipt_phones = set()
        if pair:
            link(*pair, 'signed_direct_identity', reference,
                 conflict=raw.get('conversationIdentityConflict') is True)
            receipt_phones.add(normalize_phone_jid(pair[0]))
        for identity in (raw.get('conversationId'), current):
            if normalize_phone_jid(identity):
                add(identity, 'signed_phone_jid', reference)
                receipt_phones.add(normalize_phone_jid(identity))
        # Only this exact receipt can validate a native link's first_message_id.
        receipts[str(row['external_id'])] = (route, receipt_phones, reference,
                                             raw.get('conversationIdentityConflict') is True)

    if _present(cur, 'saas_connect_direct_identities'):
        cur.execute('''SELECT identity_key,canonical_conversation_id FROM saas_connect_direct_identities
          WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid''', (tenant, channel))
        for row in cur.fetchall():
            link(row['identity_key'], row['canonical_conversation_id'], 'persisted_provider_alias')

    if _present(cur, 'saas_connect_identity_conflicts'):
        sql='''SELECT conversation_id,evidence FROM saas_connect_identity_conflicts
          WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid'''
        args=(tenant,channel)
        if _present(cur,'saas_connect_identity_conflict_events'):
            sql+=''' UNION ALL SELECT conversation_id,evidence FROM saas_connect_identity_conflict_events
              WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid'''
            args+=(tenant,channel)
        cur.execute(sql,args)
        for row in cur.fetchall():
            identity = _identity(row['conversation_id'])
            if identity:
                flagged.add(identity)
            proof = row.get('evidence')
            if not isinstance(proof, dict):
                continue
            # Revalidate the pair instead of trusting the stored boolean alone.
            pair = _pair(proof.get('directIdentity'), proof.get('originalConversationId'))
            if pair and proof.get('pair_matches_conversation') is True:
                link(*pair, 'signed_conflict_receipt', conflict=True)
                if _belongs(proof.get('canonicalConversationId'), pair) and _belongs(identity, pair):
                    reviewable_flags.update({identity, *pair})
                else:
                    hard_flagged.update({identity, *pair})
            elif identity:
                hard_flagged.add(identity)

    if (_present(cur, 'saas_crm_native_contact_conversations')
            and _present(cur, 'saas_crm_native_contact_identities')):
        cur.execute('''SELECT l.conversation_id,l.first_message_id,i.identity_value
          FROM saas_crm_native_contact_conversations l
          JOIN saas_crm_native_contact_identities i
            ON i.tenant_id=l.tenant_id AND i.contact_id=l.contact_id
              AND i.identity_type='whatsapp_phone'
          WHERE l.tenant_id=%s::uuid AND l.provider='connect' AND l.channel_id=%s::uuid''',
                    (tenant, channel))
        for row in cur.fetchall():
            identity = str(row['conversation_id'])
            phone = normalize_phone(str(row['identity_value']))
            first = str(row.get('first_message_id') or '')
            receipt = receipts.get('connect:' + first)
            if (identity not in direct or not phone or not receipt
                    or receipt[0] != identity or receipt[3] or receipt[1] != {phone}):
                continue
            link(identity, phone + '@s.whatsapp.net', 'native_identity_provenance', receipt[2])

    # Domain/format aliases of an explicit phone JID prove the same phone with
    # no LID pair needed. Do not apply this rule to arbitrary numeric chat IDs.
    by_phone = defaultdict(list)
    for identity in list(graph):
        phone = normalize_phone_jid(identity)
        if phone:
            by_phone[phone].append(identity)
    for identities in by_phone.values():
        for other in identities[1:]:
            link(identities[0], other, 'provider_phone_jid')

    phones_by_identity, evidence_by_identity, conflicts, pairs = {}, {}, {}, []
    reviewable_conflicts = {}
    visited = set()
    for start in sorted(graph):
        if start in visited:
            continue
        component, pending = set(), [start]
        while pending:
            identity = pending.pop()
            if identity in component:
                continue
            component.add(identity)
            pending.extend(graph[identity] - component)
        visited |= component
        phones = {normalize_phone_jid(identity) for identity in component} - {''}
        lids = {identity for identity in component if identity.endswith('@lid')}
        reasons = []
        if len(phones) > 1:
            reasons.append('provider_phone_conflict')
        if len(lids) > 1:
            reasons.append('provider_identity_ambiguous')
        if component & flagged:
            reasons.append('provider_identity_conflict')
        # A ledger flag is never silently cleared. Complete, self-consistent
        # split evidence may be resolved by an explicit owner reconciliation
        # over its digest; unrelated/contradictory facts remain hard blockers.
        reviewable = (reasons == ['provider_identity_conflict'] and len(phones) == 1
                      and len(component & direct) >= 2 and not component & hard_flagged
                      and component & flagged <= reviewable_flags)
        sources = sorted(set().union(*(facts[key]['sources'] for key in component)) - {'direct_route'})
        references = sorted(set().union(*(facts[key]['message_refs'] for key in component)))
        for identity in component & selected:
            phones_by_identity[identity] = set(phones)
            evidence_by_identity[identity] = {'sources': sources, 'message_refs': references}
            if reasons:
                conflicts[identity] = list(reasons)
            if reviewable:
                reviewable_conflicts[identity] = ['historical_route_split']
        for a, b in combinations(sorted(component & selected, key=_order), 2):
            pairs.append({'canonical_id': a, 'split_id': b, 'phones': sorted(phones),
                          'sources': sources, 'message_refs': references,
                          'receipts': len(references), 'conflicts': list(reasons), 'verified': True,
                          'reviewable_conflicts': ['historical_route_split'] if reviewable else []})
    return {'phones_by_identity': phones_by_identity, 'pairs': pairs,
            'conflicts': conflicts, 'evidence_by_identity': evidence_by_identity,
            'reviewable_conflicts': reviewable_conflicts}


def lookup(cur, tenant, channel, identity, routes=None):
    """Return exact identity evidence; no unique-phone guess on conflicts."""
    result = collect(cur, tenant, channel, routes)
    return {'phones': result['phones_by_identity'].get(identity, set()),
            'conflicts': result['conflicts'].get(identity, []),
            **result['evidence_by_identity'].get(identity, {'sources': [], 'message_refs': []})}
