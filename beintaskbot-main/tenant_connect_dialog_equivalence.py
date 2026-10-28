"""Audited direct-dialog equivalence without moving or deleting source records.

Every source keeps its lead, messages, CRM links and media route. Only explicitly
approved links participate in canonical routing and the combined timeline.
"""
import json
from tenant_linear_policy import TenantLinearError


def schema(cur):
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_connect_dialog_links(
      tenant_id UUID NOT NULL, channel_id UUID NOT NULL,
      source_id TEXT NOT NULL, canonical_id TEXT NOT NULL,
      source_lead BIGINT NOT NULL, canonical_lead BIGINT NOT NULL,
      pair_id TEXT NOT NULL, active BOOLEAN NOT NULL DEFAULT TRUE,
      snapshot JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,channel_id,source_id),
      CHECK(source_id<>canonical_id), CHECK(source_lead<>canonical_lead),
      FOREIGN KEY(tenant_id,channel_id,source_id)
        REFERENCES saas_connect_conversations(tenant_id,connect_channel_id,conversation_id),
      FOREIGN KEY(tenant_id,channel_id,canonical_id)
        REFERENCES saas_connect_conversations(tenant_id,connect_channel_id,conversation_id))''')
    cur.execute('''CREATE INDEX IF NOT EXISTS saas_connect_dialog_links_canonical
      ON saas_connect_dialog_links(tenant_id,channel_id,canonical_id) WHERE active''')


def available(cur):
    cur.execute("SELECT to_regclass('saas_connect_dialog_links') AS present")
    return bool((cur.fetchone() or {}).get('present'))


def canonical(cur, tenant, channel, conversation):
    if not available(cur):
        return conversation
    cur.execute('''SELECT canonical_id FROM saas_connect_dialog_links WHERE tenant_id=%s::uuid
      AND channel_id=%s::uuid AND source_id=%s AND active''', (tenant, channel, conversation))
    return str((cur.fetchone() or {}).get('canonical_id') or conversation)


def members(cur, tenant, channel, conversation):
    target = canonical(cur, tenant, channel, conversation)
    cur.execute('''SELECT conversation_id,lead_id FROM saas_connect_conversations
      WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s''',
      (tenant, channel, target))
    result = {str(r['conversation_id']): int(r['lead_id']) for r in cur.fetchall()}
    if available(cur):
        cur.execute('''SELECT c.conversation_id,c.lead_id FROM saas_connect_dialog_links l
          JOIN saas_connect_conversations c ON c.tenant_id=l.tenant_id
            AND c.connect_channel_id=l.channel_id AND c.conversation_id=l.source_id
            AND c.lead_id=l.source_lead
          WHERE l.tenant_id=%s::uuid AND l.channel_id=%s::uuid AND l.canonical_id=%s AND l.active''',
          (tenant, channel, target))
        result.update({str(r['conversation_id']): int(r['lead_id']) for r in cur.fetchall()})
    return result


def same_routes(cur, tenant, channel, routes):
    return len({canonical(cur, tenant, channel, r) for r in routes}) == 1


def lead_members(cur, tenant, channel, lead):
    cur.execute('''SELECT conversation_id FROM saas_connect_conversations WHERE tenant_id=%s::uuid
      AND connect_channel_id=%s::uuid AND lead_id=%s''', (tenant, channel, lead))
    route = cur.fetchone()
    return list(members(cur, tenant, channel, route['conversation_id']).values()) if route else [int(lead)]


def lock(cur, tenant, channel, ids):
    # Same order as webhook: provider aliases, then dialogue locks. The owner
    # check must use FOR SHARE, not a tenant-row UPDATE lock (FK deadlock).
    from tenant_connect_direct_identity import _aliases
    from tenant_ai_dialog_scope import lock_dialog
    aliases = set().union(*(_aliases(identity) for identity in ids))
    cur.execute('''SELECT identity_key FROM saas_connect_direct_identities WHERE tenant_id=%s::uuid
      AND connect_channel_id=%s::uuid AND canonical_conversation_id=ANY(%s::text[])''',(tenant,channel,ids))
    aliases.update(r['identity_key'] for r in cur.fetchall())
    for identity in sorted(aliases):
        cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                    ('connect-direct:' + json.dumps([str(tenant), str(channel), identity]),))
    for identity in sorted(ids):
        lock_dialog(cur, tenant, 'connect', channel, identity)


def snapshot(cur, tenant, channel, ids, leads):
    from tenant_connect_reconcile_dependencies import capture, present
    result = capture(cur, tenant, channel, ids, leads)
    # These dependencies retain their original keys; record counts for review.
    from tenant_connect_reconcile_dependencies import BLOCKERS
    result['references'] = {}
    for table, condition in BLOCKERS:
        if present(cur, table):
            counts = []
            for identity in ids:
                cur.execute('SELECT count(*) AS n FROM '+table+' WHERE tenant_id=%s::uuid AND '+condition,
                            (tenant, channel, identity))
                counts.append(int(cur.fetchone()['n']))
            result['references'][table] = counts
    return result


def conflicts(cur, tenant, channel, ids, before):
    reasons = []
    states = before['states']
    for field in ('agent_id', 'assigned_to', 'agent_assignment_id'):
        if len({str(r[field]) for r in states if r.get(field) is not None}) > 1:
            reasons.append('conflicting_state_assignment:'+field)
    if any(r.get('state') not in {'human','paused'} and r.get('agent_id') for r in states):
        reasons.append('assigned_conversation_not_stopped')
    if any(r.get('state') not in {'drafted','sent','skipped','failed'} or r.get('lease_token')
           or r.get('lease_until') for r in before['jobs']):
        reasons.append('job_not_terminal')
    if any(r.get('state') in {'sending','unknown'} for r in before['drafts']):
        reasons.append('draft_send_in_flight')
    cur.execute('''SELECT 1 FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid
      AND provider='connect' AND channel_id='' AND conversation_id=ANY(%s::text[]) LIMIT 1''', (tenant, ids))
    if cur.fetchone(): reasons.append('legacy_channel_ambiguous')
    cur.execute('''SELECT 1 FROM saas_connect_dialog_links WHERE tenant_id=%s::uuid
      AND channel_id=%s::uuid AND active AND (source_id=ANY(%s::text[])
        OR canonical_id=%s) LIMIT 1''', (tenant, channel, ids, ids[1]))
    if cur.fetchone(): reasons.append('route_already_merged')
    # An approved pair cannot silently absorb an unrelated reserved alias.
    from tenant_connect_phone_evidence import collect
    proof = collect(cur, tenant, channel, ids)
    phones = set().union(*(proof['phones_by_identity'].get(i, set()) for i in ids))
    if len(phones) != 1 or any(proof['phones_by_identity'].get(i,set()) != phones for i in ids):
        reasons.append('verified_phone_required')
    for i in ids:
        hard=proof['conflicts'].get(i,[])
        if hard and proof['reviewable_conflicts'].get(i)!=['historical_route_split']:
            reasons.extend(hard)
    return sorted(set(reasons))


def activate(cur, tenant, channel, pair_id, ids, leads, before):
    """Called after fresh proof and locks. No message/CRM/source row is rewritten."""
    from tenant_connect_direct_identity import _aliases
    jid, source = ids
    aliases = set().union(*(_aliases(i) for i in ids))
    aliases.update(r['identity_key'] for r in before['aliases'])
    for identity in sorted(aliases):
        cur.execute('''INSERT INTO saas_connect_direct_identities
          (tenant_id,connect_channel_id,identity_key,canonical_conversation_id)
          VALUES(%s::uuid,%s::uuid,%s,%s) ON CONFLICT(tenant_id,connect_channel_id,identity_key)
          DO UPDATE SET canonical_conversation_id=EXCLUDED.canonical_conversation_id''',
          (tenant, channel, identity, jid))
    cur.execute('''UPDATE saas_connect_conversations SET
      ai_enabled=ai_enabled AND %s,manual_hold=manual_hold OR %s
      WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s
        AND (ai_enabled<> (ai_enabled AND %s) OR manual_hold<> (manual_hold OR %s))''',
      (all(r['ai_enabled'] for r in before['routes']),any(r['manual_hold'] for r in before['routes']),
       tenant,channel,jid,all(r['ai_enabled'] for r in before['routes']),any(r['manual_hold'] for r in before['routes'])))
    # Preserve every assignment and the strictest existing stop. Source controls
    # stay intact for audit/rollback; they are fenced from execution while linked.
    states = before['states']
    selected = sorted(states, key=lambda r: ({'human':2,'paused':1}.get(r['state'],0), r['conversation_id']==jid), reverse=True)
    if selected:
        chosen = dict(selected[0])
        for field in ('agent_id','assigned_to','agent_assignment_id'):
            values = [r[field] for r in states if r.get(field) is not None]
            if values: chosen[field] = values[0]
        cur.execute('''INSERT INTO saas_ai_conversation_state
          (tenant_id,provider,channel_id,conversation_id,state,reason,agent_id,assigned_to,agent_assignment_id)
          VALUES(%s::uuid,'connect',%s,%s,%s,%s,%s::uuid,%s,%s::uuid)
          ON CONFLICT(tenant_id,provider,channel_id,conversation_id) DO UPDATE SET
            state=EXCLUDED.state,reason=EXCLUDED.reason,agent_id=EXCLUDED.agent_id,
            assigned_to=EXCLUDED.assigned_to,agent_assignment_id=EXCLUDED.agent_assignment_id,
            revision=saas_ai_conversation_state.revision+1,updated_at=now()''',
          (tenant, channel, jid, chosen['state'], chosen.get('reason') or '',
           chosen.get('agent_id'), chosen.get('assigned_to'), chosen.get('agent_assignment_id')))
    cur.execute('''SELECT row_to_json(s) AS row FROM saas_ai_conversation_state s
      WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id=%s''', (tenant,channel,jid))
    after_state = (cur.fetchone() or {}).get('row')
    saved = {'version':1,'aliases_before':before['aliases'], 'aliases_after':sorted(aliases),
             'states_before':before['states'],'canonical_state_after':after_state,
             'reference_counts':before['references'],
             'route_controls_before':{r['conversation_id']:{k:r[k] for k in ('ai_enabled','manual_hold')} for r in before['routes']},
             'message_ids': [r['id'] for r in before['messages']]}
    cur.execute('''INSERT INTO saas_connect_dialog_links
      (tenant_id,channel_id,source_id,canonical_id,source_lead,canonical_lead,pair_id,snapshot)
      VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s::jsonb)
      ON CONFLICT(tenant_id,channel_id,source_id) DO UPDATE SET canonical_id=EXCLUDED.canonical_id,
        canonical_lead=EXCLUDED.canonical_lead,pair_id=EXCLUDED.pair_id,snapshot=EXCLUDED.snapshot,
        active=TRUE,updated_at=now()''', (tenant,channel,source,jid,leads[1],leads[0],pair_id,json.dumps(saved)))
    return saved


def rollback(cur, tenant, channel, pair_id):
    cur.execute('''SELECT * FROM saas_connect_dialog_links WHERE tenant_id=%s::uuid
      AND channel_id=%s::uuid AND pair_id=%s AND active FOR UPDATE''', (tenant,channel,pair_id))
    row = cur.fetchone()
    if not row: raise TenantLinearError('Retained dialogue merge is unavailable.',409)
    s = row['snapshot']; jid = row['canonical_id']; source = row['source_id']
    cur.execute('''SELECT row_to_json(s) AS row FROM saas_ai_conversation_state s
      WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id=%s''', (tenant,channel,jid))
    if (cur.fetchone() or {}).get('row') != s['canonical_state_after']:
        raise TenantLinearError('Dialogue controls changed since merge; rollback requires review.',409)
    cur.execute('''SELECT 1 FROM saas_connect_dialog_links WHERE tenant_id=%s::uuid AND channel_id=%s::uuid
      AND canonical_id=%s AND active AND pair_id<>%s LIMIT 1''', (tenant,channel,jid,pair_id))
    if cur.fetchone(): raise TenantLinearError('Roll back newer dialogue merges first.',409)
    previous = {a['identity_key']:a['canonical_conversation_id'] for a in s['aliases_before']}
    for alias in s['aliases_after']:
        from tenant_connect_direct_identity import _aliases
        target = previous.get(alias, alias if alias in {jid,source} else source if alias in _aliases(source) else jid)
        cur.execute('''UPDATE saas_connect_direct_identities SET canonical_conversation_id=%s
          WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND identity_key=%s
            AND canonical_conversation_id=%s RETURNING identity_key''', (target,tenant,channel,alias,jid))
        if not cur.fetchone(): raise TenantLinearError('Dialogue alias changed since merge.',409)
    old = next((r for r in s['states_before'] if r['conversation_id']==jid),None)
    if old:
        cur.execute('''UPDATE saas_ai_conversation_state SET state=%s,reason=%s,agent_id=%s::uuid,
          assigned_to=%s,agent_assignment_id=%s::uuid,revision=revision+1,updated_at=now()
          WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id=%s''',
          (old['state'],old.get('reason') or '',old.get('agent_id'),old.get('assigned_to'),old.get('agent_assignment_id'),tenant,channel,jid))
    # If merge created a stop row, retain it conservatively instead of deleting
    # it or implicitly enabling AI. The snapshot records this retained control.
    cur.execute('''UPDATE saas_connect_dialog_links SET active=FALSE,updated_at=now()
      WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND pair_id=%s''', (tenant,channel,pair_id))
    return {'source_lead_id':row['source_lead'],'canonical_lead_id':row['canonical_lead'],'conservative_stops_preserved':True}
