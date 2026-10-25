"""Transactional snapshots and conservative route dependency reconciliation.

Only called inside the owner reconciliation transaction; never invokes a worker,
provider or send function. Historical run/webhook evidence is immutable.
"""
import json
from tenant_linear_policy import TenantLinearError


TABLES = {
    'routes': 'saas_connect_conversations',
    'messages': 'saas_crm_messages',
    'states': 'saas_ai_conversation_state',
    'jobs': 'saas_ai_agent_jobs',
    'drafts': 'saas_ai_agent_drafts',
    'reads': 'saas_operator_dialog_reads',
    'aliases': 'saas_connect_direct_identities',
}
BLOCKERS = (
    ('saas_connect_participant_display_names', 'connect_channel_id::text=%s AND conversation_id=%s'),
    ('saas_support_tickets', 'channel_id::text=%s AND conversation_id=%s'),
    ('saas_knowledge_extractions', "route->>'channel_id'=%s AND route->>'conversation_id'=%s"),
    ('saas_crm_native_contact_conversations', 'channel_id=%s AND conversation_id=%s'),
    ('saas_crm_native_deals', 'channel_id::text=%s AND conversation_id=%s'),
    ('saas_crm_native_tasks', 'channel_id::text=%s AND conversation_id=%s'),
    ('saas_ai_no_answer_state', "provider='connect' AND channel_id=%s AND conversation_id=%s"),
    ('saas_ai_simulation_dialog_state', 'channel_id::text=%s AND conversation_id=%s'),
    ('saas_ai_simulation_queue', "channel_id::text=%s AND conversation_id=%s AND state IN ('queued','processing','unknown')"),
)


def present(cur, table):
    cur.execute('SELECT to_regclass(%s) AS present', (table,))
    return bool(cur.fetchone()['present'])


def lock(cur):
    # Bounded, fixed-order locks fence webhook inserts and worker updates as well
    # as concurrent owner requests. No network calls occur while holding them.
    cur.execute("SET LOCAL lock_timeout='5s'")
    tables = sorted(set(TABLES.values()) | {t for t, _ in BLOCKERS})
    for table in tables:
        if present(cur, table):
            cur.execute('LOCK TABLE ' + table + ' IN SHARE ROW EXCLUSIVE MODE')


def clause(kind, channel, ids, leads):
    if kind == 'routes': return 'connect_channel_id=%s::uuid AND conversation_id=ANY(%s::text[])', (channel, ids)
    if kind == 'messages': return "channel='connect:'||%s::text AND kommo_lead_id=ANY(%s::bigint[])", (channel, leads)
    if kind == 'drafts': return 'lead_id=ANY(%s::bigint[])', (leads,)
    if kind == 'aliases': return 'connect_channel_id=%s::uuid AND (canonical_conversation_id=ANY(%s::text[]) OR identity_key=ANY(%s::text[]))', (channel, ids, ids)
    return "provider='connect' AND channel_id=%s AND conversation_id=ANY(%s::text[])", (channel, ids)


def capture(cur, tenant, channel, ids, leads):
    result = {}
    for kind, table in TABLES.items():
        if not present(cur, table):
            result[kind] = []
            continue
        condition, args = clause(kind, channel, ids, leads)
        cur.execute('SELECT row_to_json(r) AS row FROM '+table+' r WHERE tenant_id=%s::uuid AND '+condition, (tenant, *args))
        result[kind] = sorted((dict(r['row']) for r in cur.fetchall()), key=lambda r: json.dumps(r, sort_keys=True))
    return result


def conflicts(cur, tenant, channel, ids, leads, snapshot):
    reasons = []
    for table, condition in BLOCKERS:
        if present(cur, table):
            cur.execute('SELECT 1 FROM '+table+' WHERE tenant_id=%s::uuid AND '+condition+' LIMIT 1', (tenant, channel, ids[1]))
            if cur.fetchone(): reasons.append('unsupported_route_dependency:'+table)
    # Unscoped legacy states must never be silently claimed by this channel.
    if present(cur, TABLES['states']):
        cur.execute("SELECT 1 FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id='' AND conversation_id=ANY(%s::text[]) LIMIT 1", (tenant, ids))
        if cur.fetchone(): reasons.append('legacy_channel_ambiguous')
    states = snapshot['states']
    for field in ('agent_id', 'assigned_to', 'agent_assignment_id'):
        if len({str(r[field]) for r in states if r.get(field) is not None}) > 1:
            reasons.append('conflicting_state_assignment:'+field)
    if any(r['state'] not in {'human', 'paused'} for r in states):
        reasons.append('conversation_not_stopped')
    keys = set()
    for job in snapshot['jobs']:
        if job['state'] not in {'drafted', 'sent', 'skipped', 'failed'} or job.get('lease_token') or job.get('lease_until'):
            reasons.append('job_not_terminal')
        if job['agent_id'] in keys: reasons.append('job_key_collision')
        keys.add(job['agent_id'])
        if job['lead_id'] not in leads or job['chat_id'] not in ids:
            reasons.append('job_route_mismatch')
    if any(r['state'] in {'sending', 'unknown'} for r in snapshot['drafts']):
        reasons.append('draft_send_in_flight')
    if any(r['chat_id'] not in ids for r in snapshot['drafts']):
        reasons.append('draft_route_mismatch')
    if any(r['lead_id'] not in leads for r in snapshot['reads']):
        reasons.append('read_route_mismatch')
    # Catch future FK dependencies rather than cascading/deleting unknown data.
    cur.execute("""SELECT conrelid::regclass::text AS source FROM pg_constraint
      WHERE contype='f' AND confrelid=ANY(%s::regclass[])""", ([TABLES[k] for k in ('routes','states','jobs','reads')],))
    known = {t for t, _ in BLOCKERS} | set(TABLES.values())
    if any(r['source'].split('.')[-1] not in known for r in cur.fetchall()):
        reasons.append('unknown_foreign_key_dependency')
    return sorted(set(reasons))


def replace_rows(cur, table, tenant, condition, args, rows):
    # Used only for state/read rows without downstream dependencies, under locks.
    cur.execute('DELETE FROM '+table+' WHERE tenant_id=%s::uuid AND '+condition, (tenant, *args))
    for row in rows: insert_row(cur, table, row)


def insert_row(cur, table, row):
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s AND is_generated='NEVER' ORDER BY ordinal_position", (table,))
    columns = [r['column_name'] for r in cur.fetchall()]
    # Identifiers originate only from PostgreSQL's catalog, never request fields.
    names = ','.join('"'+x.replace('"','""')+'"' for x in columns)
    cur.execute('INSERT INTO '+table+'('+names+') SELECT '+names+' FROM json_populate_record(NULL::'+table+',%s::json)', (json.dumps(row),))


def migrate(cur, tenant, channel, ids, leads, before):
    jid, lid = ids; canonical, split = leads
    # State precedence never resumes AI. Both original records remain in backup.
    if before['states']:
        chosen = dict(sorted(before['states'], key=lambda r: (r['state']=='human', r['conversation_id']==jid), reverse=True)[0])
        chosen.update(conversation_id=jid, revision=max(r['revision'] for r in before['states'])+1, resumed_at=None)
        for field in ('agent_id', 'assigned_to', 'agent_assignment_id'):
            values = [r[field] for r in before['states'] if r.get(field) is not None]
            if values: chosen[field] = values[0]
        condition, args = clause('states', channel, ids, leads)
        replace_rows(cur, TABLES['states'], tenant, condition, args, [chosen])
    for job in before['jobs']:
        if job['conversation_id'] == lid:
            cur.execute("""UPDATE saas_ai_agent_jobs SET conversation_id=%s,chat_id=%s,lead_id=%s
              WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id=%s""", (jid,jid,canonical,tenant,job['agent_id'],channel,lid))
    for draft in before['drafts']:
        if draft['lead_id'] == split:
            cur.execute('UPDATE saas_ai_agent_drafts SET lead_id=%s,chat_id=%s WHERE tenant_id=%s::uuid AND id=%s::uuid', (canonical,jid,tenant,draft['id']))
    reads = {}
    for original in before['reads']:
        row = dict(original); row.update(conversation_id=jid, lead_id=canonical)
        key = row['telegram_id']
        # Earlier cursor is conservative: never silently mark unread data read.
        if key not in reads or (row['last_read_at'],str(row.get('last_read_message_id'))) < (reads[key]['last_read_at'],str(reads[key].get('last_read_message_id'))): reads[key]=row
    condition, args = clause('reads', channel, ids, leads)
    replace_rows(cur,TABLES['reads'],tenant,condition,args,list(reads.values()))
    cur.execute("UPDATE saas_connect_direct_identities SET canonical_conversation_id=%s WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND canonical_conversation_id=%s", (jid,tenant,channel,lid))
    cur.execute("UPDATE saas_crm_messages SET kommo_lead_id=%s WHERE tenant_id=%s::uuid AND channel='connect:'||%s::text AND kommo_lead_id=%s", (canonical,tenant,channel,split))
    cur.execute('DELETE FROM saas_connect_conversations WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s', (tenant,channel,lid))


def restore(cur, tenant, channel, ids, leads, before, after):
    if capture(cur,tenant,channel,ids,leads) != after:
        raise TenantLinearError('direct_merge_rollback_state_changed',409)
    # Recreate the exact source route first, never overwrite an existing route.
    for row in before['routes']:
        if row['conversation_id']==ids[1]: insert_row(cur,TABLES['routes'],row)
    for row in before['messages']:
        cur.execute('UPDATE saas_crm_messages SET kommo_lead_id=%s WHERE tenant_id=%s::uuid AND id=%s::uuid', (row['kommo_lead_id'],tenant,row['id']))
    for kind in ('states','reads'):
        condition,args=clause(kind,channel,ids,leads)
        replace_rows(cur,TABLES[kind],tenant,condition,args,before[kind])
    for row in before['jobs']:
        cur.execute("UPDATE saas_ai_agent_jobs SET conversation_id=%s,chat_id=%s,lead_id=%s WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id=%s", (row['conversation_id'],row['chat_id'],row['lead_id'],tenant,row['agent_id'],channel,ids[0]))
    for row in before['drafts']:
        cur.execute('UPDATE saas_ai_agent_drafts SET lead_id=%s,chat_id=%s WHERE tenant_id=%s::uuid AND id=%s::uuid', (row['lead_id'],row['chat_id'],tenant,row['id']))
    for row in before['aliases']:
        cur.execute('UPDATE saas_connect_direct_identities SET canonical_conversation_id=%s WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND identity_key=%s', (row['canonical_conversation_id'],tenant,channel,row['identity_key']))
    if capture(cur,tenant,channel,ids,leads) != before:
        raise TenantLinearError('direct_merge_rollback_verification_failed',409)


def verify_transfer(before, after, ids, leads):
    # Fail closed if a trigger or unexpected schema behaviour changes evidence.
    for kind, fields in (('messages',('kommo_lead_id',)),('drafts',('lead_id','chat_id')),('jobs',('conversation_id','chat_id','lead_id'))):
        expected=[]
        for original in before[kind]:
            row=dict(original)
            if kind=='messages' and row['kommo_lead_id']==leads[1]: row['kommo_lead_id']=leads[0]
            if kind=='drafts' and row['lead_id']==leads[1]: row.update(lead_id=leads[0],chat_id=ids[0])
            if kind=='jobs' and row['conversation_id']==ids[1]: row.update(conversation_id=ids[0],chat_id=ids[0],lead_id=leads[0])
            expected.append(row)
        ordered=lambda rows: sorted(rows,key=lambda r:json.dumps(r,sort_keys=True))
        if ordered(expected)!=ordered(after[kind]):
            raise TenantLinearError('direct_merge_transfer_verification_failed:'+kind,409)
    expected_routes=[r for r in before['routes'] if r['conversation_id']==ids[0]]
    if after['routes']!=expected_routes:
        raise TenantLinearError('direct_merge_transfer_verification_failed:routes',409)
