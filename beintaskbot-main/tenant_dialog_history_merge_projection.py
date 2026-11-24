"""Optional owner presentation of a committed history merge; never changes routes."""
import json
import os
from collections import Counter
from tenant_dialog_history_merge_scope import MAX_ACTIVE_PAIRS, allows, configured, source_for


def enabled():
    return (configured() is not None
            and os.environ.get('BEINTASKBOT_UNIFIED_WHATSAPP_SEND') == '1')


def lifecycle_enabled():
    return enabled() and os.environ.get('DIALOG_HISTORY_MERGE_LIFECYCLE_ENABLED') == '1'


def _pairs(cur, tenant, policy, *, channel=None, principal=None, lock=False, eligible_lifecycle=False):
    eligible_lifecycle = eligible_lifecycle and lifecycle_enabled()
    scoped=source_for(tenant)
    if (not enabled() or not scoped or (channel is not None and str(channel)!=scoped)
            or (not eligible_lifecycle and policy.profile.get('role') != 'owner')):
        return []
    channel=scoped
    from tenant_schema_preflight import ready
    if not ready(cur, {
        'saas_dialog_history_merge_jobs': ('id','tenant_id','channel_id','left_id','right_id','left_lead',
            'right_lead','state','principal_id','proof_generation','proof_gateway_id','proof_at','display_name','display_phone','display_revision'),
        'saas_dialog_workflows': ('tenant_id','channel_id','conversation_id'),
        'saas_dialog_episodes': ('tenant_id','channel_id','conversation_id'),
        'saas_whatsapp_native_routes': ('tenant_id','channel_id','generation','enabled','paused'),
        'saas_whatsapp_capture_generations': ('tenant_id','channel_id','generation','gateway_id','account_jid','enabled'),
        'saas_browser_call_sources': ('tenant_id','channel_id','gateway_id','account_jid','enabled'),
        'saas_connect_dialog_links': ('tenant_id','channel_id','source_id','canonical_id','active'),
        'saas_connect_identity_conflicts': ('tenant_id','connect_channel_id','conversation_id','event_id','evidence'),
        'saas_connect_identity_conflict_events': ('tenant_id','connect_channel_id','conversation_id','event_id','evidence','created_at'),
        'saas_connect_direct_identities': ('tenant_id','connect_channel_id','identity_key','canonical_conversation_id'),
        'saas_crm_messages': ('tenant_id','kommo_lead_id','external_id','happened_at','created_at'),
    }, ['saas_crm_messages_history_position']):
        return []
    cur.execute("""SELECT j.id::text AS id,j.channel_id::text AS channel_id,
             j.principal_id AS primary_id,
             CASE WHEN j.principal_id=j.left_id THEN j.right_id ELSE j.left_id END AS alternate_id,
             CASE WHEN j.principal_id=j.left_id THEN l.lead_id ELSE r.lead_id END AS primary_lead,
             CASE WHEN j.principal_id=j.left_id THEN r.lead_id ELSE l.lead_id END AS alternate_lead,
             j.proof_generation,j.proof_gateway_id,j.proof_at,
             CASE WHEN %s THEN j.display_name END AS display_name,
             CASE WHEN %s THEN j.display_phone END AS display_phone,
             CASE WHEN %s THEN j.display_revision END AS display_revision,
             (EXISTS (SELECT 1 FROM saas_connect_identity_conflicts f
                WHERE f.tenant_id=j.tenant_id AND f.connect_channel_id=j.channel_id
                  AND f.conversation_id IN(j.left_id,j.right_id,split_part(j.principal_id,'@',1)||'@c.us',split_part(j.principal_id,'@',1)||'@s.whatsapp.net'))
              OR EXISTS (SELECT 1 FROM saas_connect_identity_conflict_events f
                WHERE f.tenant_id=j.tenant_id AND f.connect_channel_id=j.channel_id
                  AND f.conversation_id IN(j.left_id,j.right_id,split_part(j.principal_id,'@',1)||'@c.us',split_part(j.principal_id,'@',1)||'@s.whatsapp.net'))) AS has_evidence
      FROM saas_dialog_history_merge_jobs j
      JOIN saas_whatsapp_native_routes n ON n.tenant_id=j.tenant_id AND n.channel_id=j.channel_id
        AND n.generation=j.proof_generation AND n.enabled AND NOT n.paused
      JOIN saas_whatsapp_capture_generations g ON g.tenant_id=n.tenant_id AND g.channel_id=n.channel_id
        AND g.generation=n.generation AND g.gateway_id=j.proof_gateway_id AND g.enabled
      JOIN saas_browser_call_sources s ON s.tenant_id=g.tenant_id AND s.channel_id=g.channel_id
        AND s.gateway_id=g.gateway_id AND s.account_jid=g.account_jid AND s.enabled
      JOIN saas_connect_channels c ON c.tenant_id=j.tenant_id AND c.connect_channel_id=j.channel_id AND c.active
      JOIN saas_connect_conversations l ON l.tenant_id=j.tenant_id AND l.connect_channel_id=j.channel_id
        AND l.conversation_id=j.left_id AND l.lead_id=j.left_lead AND l.conversation_kind='contact'
      JOIN saas_connect_conversations r ON r.tenant_id=j.tenant_id AND r.connect_channel_id=j.channel_id
        AND r.conversation_id=j.right_id AND r.lead_id=j.right_lead AND r.conversation_kind='contact'
      WHERE j.tenant_id=%s::uuid AND j.state='completed'
        AND (%s::uuid IS NULL OR j.channel_id=%s::uuid)
        AND (%s::text IS NULL OR j.principal_id=%s OR (%s AND %s IN(j.left_id,j.right_id)))
        AND j.left_id<j.right_id AND j.left_lead<>j.right_lead
        AND j.principal_id IN(j.left_id,j.right_id)
        AND j.principal_id ~ '^[1-9][0-9]{6,14}@(s[.]whatsapp[.]net|c[.]us)$'
        AND (CASE WHEN j.principal_id=j.left_id THEN j.right_id ELSE j.left_id END) ~ '^[1-9][0-9]{5,21}@lid$'
        AND (%s OR NOT EXISTS (SELECT 1 FROM saas_dialog_workflows w
          WHERE w.tenant_id=j.tenant_id AND w.channel_id=j.channel_id AND w.conversation_id IN(j.left_id,j.right_id)))
        AND (%s OR NOT EXISTS (SELECT 1 FROM saas_dialog_episodes e
          WHERE e.tenant_id=j.tenant_id AND e.channel_id=j.channel_id AND e.conversation_id IN(j.left_id,j.right_id)))
        AND NOT EXISTS (SELECT 1 FROM saas_connect_dialog_links a
          WHERE a.tenant_id=j.tenant_id AND a.channel_id=j.channel_id AND a.active
            AND (a.source_id IN(j.left_id,j.right_id,split_part(j.principal_id,'@',1)||'@c.us',split_part(j.principal_id,'@',1)||'@s.whatsapp.net')
                 OR a.canonical_id IN(j.left_id,j.right_id,split_part(j.principal_id,'@',1)||'@c.us',split_part(j.principal_id,'@',1)||'@s.whatsapp.net')))
        AND (%s OR NOT EXISTS (SELECT 1 FROM saas_connect_identity_conflicts f
          WHERE f.tenant_id=j.tenant_id AND f.connect_channel_id=j.channel_id AND f.conversation_id IN(j.left_id,j.right_id,split_part(j.principal_id,'@',1)||'@c.us',split_part(j.principal_id,'@',1)||'@s.whatsapp.net')))
        AND (%s OR NOT EXISTS (SELECT 1 FROM saas_connect_identity_conflict_events f
          WHERE f.tenant_id=j.tenant_id AND f.connect_channel_id=j.channel_id AND f.conversation_id IN(j.left_id,j.right_id,split_part(j.principal_id,'@',1)||'@c.us',split_part(j.principal_id,'@',1)||'@s.whatsapp.net')))
        AND NOT EXISTS (SELECT 1 FROM saas_connect_direct_identities a
          WHERE a.tenant_id=j.tenant_id AND a.connect_channel_id=j.channel_id AND a.identity_key IN(j.left_id,j.right_id,split_part(j.principal_id,'@',1)||'@c.us',split_part(j.principal_id,'@',1)||'@s.whatsapp.net')
            AND a.canonical_conversation_id<>a.identity_key
            AND NOT (a.identity_key NOT IN(j.left_id,j.right_id) AND a.canonical_conversation_id=j.principal_id))
        AND NOT EXISTS (SELECT 1 FROM saas_connect_conversations alias
          WHERE alias.tenant_id=j.tenant_id AND alias.connect_channel_id=j.channel_id
            AND alias.conversation_id NOT IN(j.left_id,j.right_id)
            AND alias.conversation_id IN(split_part(j.principal_id,'@',1)||'@c.us',split_part(j.principal_id,'@',1)||'@s.whatsapp.net'))
      ORDER BY j.id LIMIT %s""" + (" FOR SHARE OF j,n,g,s,c NOWAIT" if lock else ""),
      ((policy.profile.get('role')=='owner',)*3+ (
       tenant,channel,channel,principal,principal,eligible_lifecycle,principal,eligible_lifecycle,
       eligible_lifecycle,eligible_lifecycle,eligible_lifecycle,2 if principal is not None else MAX_ACTIVE_PAIRS+1)))
    pairs=cur.fetchall()
    if len(pairs)>(1 if principal is not None else MAX_ACTIVE_PAIRS):
        return []
    occurrences=Counter((p['channel_id'],p[k]) for p in pairs for k in ('primary_id','alternate_id'))
    pairs=[p for p in pairs if all(occurrences[(p['channel_id'],p[k])]==1
                                    for k in ('primary_id','alternate_id'))]
    if eligible_lifecycle:
        from tenant_whatsapp_pair_evidence import check_pair_evidence
        pairs=[p for p in pairs if p['proof_at'] is not None and
               (not p['has_evidence'] or check_pair_evidence(cur,tenant,p['channel_id'],
                 p['primary_id'],p['alternate_id'],p['proof_at'])['safe'])]
    return pairs


def current_display(cur,tenant,pair):
    """Recheck editable metadata only after both routes are independently visible."""
    if not (pair.get('display_name') or pair.get('display_phone')):
        return pair
    from tenant_dialog_merge_display import exact_options
    routes=tuple((pair[k+'_id'],pair[k+'_lead']) for k in ('primary','alternate'))
    options=(exact_options(cur,tenant,pair['channel_id'],routes) if pair.get('display_revision') else None)
    if not options or options['display_revision']!=pair['display_revision']:
        return {**pair,'display_name':None,'display_phone':None}
    return pair


def fold_completed(cur, tenant, policy, rows, native_companions, *, channel=None):
    from tenant_whatsapp_history_pairs import fold_cards
    if not enabled() or policy.profile.get('role') != 'owner':
        return rows
    def key(row):
        return row['channel_id'],row['conversation_id'],int(row['lead_id'])
    native=set(native_companions)|{key(row) for row in native_companions.values()}
    visible={key(r) for r in rows if r['provider']=='connect' and not any(
        r.get(field) for field in ('archive_read_only','search_overview_read_only','owner_overview_read_only'))}-native
    if len(visible)<2:
        return rows
    pairs=[current_display(cur,tenant,p) for p in _pairs(cur,tenant,policy,channel=channel)
           if all((p['channel_id'],p[k+'_id'],int(p[k+'_lead'])) in visible
                  for k in ('primary','alternate'))]
    folded,_=fold_cards(rows,pairs)
    return folded


def history_pair(cur, tenant, policy, channel, conversation, lead, *, owner_overview=False):
    scoped=lifecycle_enabled()
    candidates=_pairs(cur,tenant,policy,channel=channel,principal=conversation,eligible_lifecycle=scoped)
    if not candidates:
        return None
    pair=candidates[0]
    requested='alternate' if scoped and pair['alternate_id']==conversation else 'primary'
    companion='alternate' if requested=='primary' else 'primary'
    if pair[requested+'_lead']!=lead or pair[requested+'_id']!=conversation:
        return None
    alternate=pair[companion+'_id']
    # Legacy routes may not hold the ordinary lifecycle lock. Protect both
    # exact endpoints without waiting behind inverse-order writers.
    for exact in sorted((conversation,alternate)):
        cur.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0)) AS acquired",
            ('ai-dialog:'+json.dumps([str(tenant),'connect',str(channel),exact]),))
        if not (cur.fetchone() or {}).get('acquired'):
            return None
    from tenant_operator_access import assert_route
    from tenant_linear_policy import TenantLinearError
    try:
        row=assert_route(cur,tenant,policy,'connect',alternate,channel,pair[companion+'_lead'],
                         allow_closed_archive=scoped,owner_overview=owner_overview and scoped)
    except TenantLinearError as exc:
        if exc.status in {403,404,409}:
            return None
        raise
    if (str(row['connect_channel_id']),row['conversation_id'],row['lead_id']) != (
            channel,alternate,pair[companion+'_lead']):
        return None
    from psycopg.errors import LockNotAvailable
    # A transient channel/generation/undo write returns the ordinary route
    # instead of making an optional view wait or poison the parent transaction.
    cur.execute("SAVEPOINT owner_merge_read_proof")
    try:
        fresh=_pairs(cur,tenant,policy,channel=channel,principal=conversation,lock=True,
                     eligible_lifecycle=scoped)
    except LockNotAvailable:
        cur.execute("ROLLBACK TO SAVEPOINT owner_merge_read_proof")
        fresh=[]
    cur.execute("RELEASE SAVEPOINT owner_merge_read_proof")
    if fresh!=candidates:
        return None
    pair=current_display(cur,tenant,pair)
    if not scoped:
        return pair
    from tenant_dialog_lifecycle import _row, is_shared_archive, is_owner_observer
    actor=int(policy.profile['telegram_id'])
    restricted=[]
    options=[]
    facets=[]
    for side in ('primary','alternate'):
        exact=pair[side+'_id']
        workflow=_row(cur,tenant,channel,exact)
        state=(workflow or {}).get('state','waiting')
        passive=(is_shared_archive(workflow,actor) or
                 (owner_overview and is_owner_observer(cur,tenant,actor,channel,workflow)))
        route={'provider':'connect','channel_id':channel,'conversation_id':exact,'lead_id':pair[side+'_lead']}
        facets.append({**route,'state':state,'read_only':bool(passive or state in {'closed','dismissed'})})
        if passive:
            restricted.append(pair[side+'_lead'])
        elif workflow and workflow.get('state')=='accepted' and int(workflow.get('operator_id') or 0)==actor:
            options.append(route)
    if len(options)==2:
        from tenant_operator_access import access
        from tenant_dialog_access import require_source
        try:
            _,_,updater=access(cur,{'tenant_id':tenant,'telegram_id':actor},'dialogs',action='update')
            require_source(cur,tenant,updater,'connect',channel,'update')
        except TenantLinearError as exc:
            if exc.status not in {403,404,409}:
                raise
            options=[]
    # Opening a passive/archive route never elevates that view into a full
    # internal timeline simply because the actor can separately open the other
    # route. The ordinary independent route can still show its normal history.
    if lead in restricted:
        restricted=[pair['primary_lead'],pair['alternate_lead']]
    mask=''.join('1' if pair[side+'_lead'] in restricted else '0' for side in ('primary','alternate'))
    return {**pair,'projection_revision':pair['id']+':'+mask,'transcript_only_leads':restricted,
            'action_routes':options if len(options)==2 else [],
            'read_routes':facets if ((facets[0]['state'],facets[0]['read_only'])
                                     != (facets[1]['state'],facets[1]['read_only'])) else []}


def available_job(cur, tenant, policy, job):
    """Revalidate a trusted complete-job row for the current owner and route."""
    if (not enabled() or policy.profile.get('role') != 'owner'
            or job.get('state') != 'completed' or str(job.get('tenant_id')) != str(tenant)
            or not allows(tenant,job.get('channel_id'))):
        return False
    primary = job.get('principal_id')
    side = next((k for k in ('left','right') if primary and job.get(k+'_id') == primary), None)
    if not side:
        return False
    channel, lead = str(job.get('channel_id') or ''), job.get(side+'_lead')
    if not channel or not lead:
        return False
    from tenant_operator_access import assert_route
    from tenant_linear_policy import TenantLinearError
    # Match ordinary history: principal scope/ACL first; companion rechecked below.
    try:
        row = assert_route(cur,tenant,policy,'connect',primary,channel,lead,
                           allow_closed_archive=lifecycle_enabled(),owner_overview=lifecycle_enabled())
    except TenantLinearError as exc:
        if exc.status in {403,404,409}:
            return False
        raise
    if (str(row['connect_channel_id']),row['conversation_id'],row['lead_id']) != (channel,primary,lead):
        return False
    pair = history_pair(cur,tenant,policy,channel,primary,lead,owner_overview=lifecycle_enabled())
    return bool(pair and pair['id'] == str(job.get('id')))


def page(cur, tenant, channel, pair, *, before=None, call_filter='', timeline_boundary=None):
    """Take at most 51 indexed keys per route, then interleave their timelines."""
    if not enabled() or not allows(tenant,channel) or str(pair.get('channel_id')) != str(channel):
        return []
    leads=(pair['primary_lead'],pair['alternate_lead'])
    history_channel='connect:'+channel
    restricted=pair.get('transcript_only_leads') or []
    cursor=None
    if before:
        cur.execute("""SELECT COALESCE(happened_at,created_at) AS at,external_id FROM saas_crm_messages
          WHERE tenant_id=%s::uuid AND id=%s::uuid AND channel=%s AND kommo_lead_id=ANY(%s::bigint[])
            AND (NOT kommo_lead_id=ANY(%s::bigint[]) OR direction IN ('incoming','outgoing'))""",
          (tenant,before,history_channel,list(leads),restricted))
        cursor=cur.fetchone()
        if not cursor:
            return []
        if timeline_boundary is not None:
            # Derived only from the same authorized combined-history cursor
            # that is about to fence the message query.
            timeline_boundary['before_stamp']=cursor['at']
    branch="""SELECT id,COALESCE(happened_at,created_at) AS merge_at,external_id FROM saas_crm_messages m
              WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND channel=%s"""
    cursor_clause=" AND (COALESCE(happened_at,created_at),external_id)<(%s,%s)" if cursor else ''
    branches=[branch+cursor_clause+(" AND m.direction IN ('incoming','outgoing')" if lead in restricted else '')
              +call_filter+' ORDER BY merge_at DESC,external_id DESC LIMIT 51' for lead in leads]
    args=tuple(x for lead in leads for x in (
        (tenant,lead,history_channel,cursor['at'],cursor['external_id']) if cursor else (tenant,lead,history_channel)))
    cur.execute("""WITH chosen AS (
          SELECT * FROM (("""+branches[0]+""") UNION ALL ("""+branches[1]+""")) keys
          ORDER BY merge_at DESC,external_id DESC LIMIT 51
        ) SELECT m.id,m.external_id,m.direction,m.channel,m.author_name,m.body,m.message_type,m.media_url,
           m.happened_at,m.created_at,m.raw,m.kommo_lead_id AS source_lead_id
          FROM chosen JOIN saas_crm_messages m ON m.id=chosen.id AND m.tenant_id=%s::uuid
          ORDER BY chosen.merge_at DESC,chosen.external_id DESC""",(*args,tenant))
    messages=cur.fetchall()
    origins=dict(zip(leads,(pair['primary_id'],pair['alternate_id'])))
    for message in messages:
        message['source_conversation_id']=origins[message['source_lead_id']]
        message['source_channel_id']=channel
    return messages
