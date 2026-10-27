"""Exact case interval admission into the existing guarded knowledge campaign.

Planning is read-only.  The owner must approve the resulting campaign before any
fragment is queued.  No conversation history discovery is used for this mode.
"""
from datetime import timedelta

from tenant_linear_policy import TenantLinearError, identifier
from tenant_knowledge_candidates_policy import MAX_MESSAGES, moment
from tenant_knowledge_media_coverage import SUPPORTED
import tenant_knowledge_candidates_store as store
from tenant_media_evidence import media_version, source_hash


def plan(cur, profile, candidate_id):
    tenant, _, policy = store.authorize(cur, profile)
    if policy.profile.get('role')!='owner':
        raise TenantLinearError('Owner media consent required.',403)
    key = identifier(candidate_id)
    cur.execute('SELECT * FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND id=%s::uuid FOR SHARE', (tenant, key))
    case = cur.fetchone()
    if not case or case['status'] in {'rejected', 'archived'}:
        raise TenantLinearError('Case unavailable.', 404)
    run = store.extraction_row(cur, tenant, case['extraction_id'])
    binding = run['route']
    chosen = store.assert_route(cur, tenant, policy, 'connect', binding['conversation_id'], binding['channel_id'])
    content = case['content']
    start, end = content.get('start_message_id'), content.get('end_message_id')
    if not start or not end:
        return {'status': 'pending_boundaries', 'reason': 'case_boundaries_missing_or_ambiguous', 'case_id': key}
    try:
        anchors, count, inside = store._boundary_context(cur, tenant, binding, chosen, start, end,
            [identifier(value) for value in content.get('source_message_ids',[])])
    except TenantLinearError:
        return {'status': 'pending_boundaries', 'reason': 'case_boundaries_unavailable', 'case_id': key}
    if not inside:
        return {'status': 'pending_boundaries', 'reason': 'case_evidence_outside_boundaries', 'case_id': key}
    if count > MAX_MESSAGES:
        return {'status': 'pending_boundaries', 'reason': 'case_boundary_context_limit', 'case_id': key}
    channel = str(binding['channel_id'])
    cur.execute('''SELECT id::text AS id,external_id,body,raw,message_type,
        COALESCE(happened_at,created_at) AS at FROM saas_crm_messages
      WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND channel=%s
        AND (COALESCE(happened_at,created_at),id)>=(%s::timestamptz,%s::uuid)
        AND (COALESCE(happened_at,created_at),id)<=(%s::timestamptz,%s::uuid)
      ORDER BY COALESCE(happened_at,created_at),id LIMIT %s''',
      (tenant, int(chosen['lead_id']), 'connect:'+channel, anchors[start]['at'], start,
       anchors[end]['at'], end, MAX_MESSAGES))
    rows = cur.fetchall()
    coverage = store.case_media_coverage(cur, tenant, run, chosen, content)
    by_id = {item['id']: item for item in coverage['media_results']}
    media = []
    for row in rows:
        kind = str(row.get('message_type') or 'text')
        if kind == 'text':
            continue
        at = moment(str(row['at']))
        if at.tzinfo is None:
            raise TenantLinearError('Case message time unavailable.', 409)
        item = by_id.get(str(row['id']), {})
        version=media_version(row.get('raw'))
        media.append({'id': str(row['id']), 'kind': kind,
            'source_version': version,
            'source_hash': source_hash({'external_id': row.get('external_id'),
              'body_hash': store.digest(str(row.get('body') or '')),
              'media_kind': kind, 'media_version': version}),
            'at': at.isoformat(), 'period_end': (at+timedelta(microseconds=1)).isoformat(),
            'status': item.get('status', 'unknown'), 'code': item.get('code', 'status_not_recorded'),
            'complete': item.get('complete') is True})
    return {'status': 'pending_consent', 'case_id': key, 'case_version': case['version'],
        'channel_id': channel, 'conversation_id': binding['conversation_id'],
        'start_message_id': start, 'end_message_id': end, 'media': media,
        'plan_hash': store.digest([(x['id'],x['kind'],x['source_hash'],x['at']) for x in media]
                                  + [start,end,case['version']])}


def routes(planned, agent_id, *, allow_legacy_unknown=True):
    """One source per fragment avoids the collector's four-file/two-visual cap."""
    for item in planned['media']:
        if (item['complete'] or item['kind'] not in SUPPORTED
            or item['status'] not in {'pending_processing','pending_consent','unknown'}
            or item['status']=='unknown' and (not allow_legacy_unknown or item['code'] not in {'status_not_recorded','source_version_unverified'})):
            continue
        yield {'provider':'connect', 'channel_id':planned['channel_id'],
            'conversation_id':planned['conversation_id'], 'history_mode':'channel',
            'message_ids':[item['id']], 'period_start':item['at'],
            'period_end':item['period_end'], 'analysis_agent_id':str(agent_id),
            'source_version':item['source_version'], 'case_id':planned['case_id'],
            'case_version':planned['case_version'], 'case_plan_hash':planned['plan_hash']}


def plan_existing_cases(cur, profile, *, limit=500):
    """Build a fail-closed, versioned snapshot of current support cases.

    Only already-created, non-rejected/non-archived candidates are included.
    Each case is still bounded by its own verified start/end messages. A case
    with ambiguous boundaries is recorded for owner review and never routed.
    """
    tenant,_,policy=store.authorize(cur,profile)
    if policy.profile.get('role')!='owner':
        raise TenantLinearError('Owner media consent required.',403)
    cur.execute("""SELECT id::text AS id,version,status FROM saas_knowledge_candidates
      WHERE tenant_id=%s::uuid AND status NOT IN ('rejected','archived') ORDER BY id LIMIT %s""",
      (tenant,limit+1))
    candidates=cur.fetchall()
    if len(candidates)>limit:
        raise TenantLinearError('Current case snapshot exceeds the bounded campaign size.',409)
    rows=[];plans=[];channels=set();eligible=0;reused=0;review=0;unknown=0;unavailable=0
    for candidate in candidates:
        case_id=str(candidate['id'])
        try:
            item=plan(cur,profile,case_id)
        except TenantLinearError as error:
            # Cases disappearing or crossing tenant scope invalidate the
            # snapshot rather than being silently omitted.
            if error.status in (403,404):
                raise TenantLinearError('Case snapshot changed; prepare it again.',409)
            item={'case_id':case_id,'status':'pending_boundaries','reason':'case_boundaries_unavailable'}
        if item.get('case_version') is not None and int(item['case_version'])!=int(candidate['version']):
            raise TenantLinearError('Case snapshot changed; prepare it again.',409)
        # Consent is for the exact worklist shown to the owner, not only for
        # source identity. Receipt/state changes between preview and approval
        # can add or remove billable calls and must invalidate that preview.
        media_scope=sorted([[m.get('id'),m.get('kind'),m.get('source_version'),m.get('status'),
          m.get('code'),bool(m.get('complete'))] for m in item.get('media',[])],key=lambda row:str(row[0]))
        rows.append([case_id,int(candidate['version']),candidate['status'],item.get('plan_hash',''),
          item.get('reason',''),media_scope])
        if item.get('status')!='pending_consent':
            review+=1;plans.append({'case_id':case_id,'status':'pending_boundaries','reason':item.get('reason','case_boundaries_unavailable')});continue
        if not item['media']:
            plans.append({'case_id':case_id,'status':'no_media','plan_hash':item['plan_hash']});continue
        channels.add(item['channel_id'])
        pending=list(routes(item,'00000000-0000-0000-0000-000000000000',allow_legacy_unknown=False))
        eligible+=len(pending)
        reused+=sum(1 for media in item['media'] if media.get('complete'))
        routed={route['message_ids'][0] for route in pending}
        unknown+=sum(1 for media in item['media'] if not media.get('complete') and media['id'] not in routed and media.get('status')=='unknown')
        unavailable+=sum(1 for media in item['media'] if not media.get('complete') and media['id'] not in routed and media.get('status')!='unknown')
        plans.append(item)
    if len(channels)>100:
        raise TenantLinearError('Current case snapshot spans too many channels.',409)
    return {'status':'pending_consent','cases':plans,'channel_ids':sorted(channels),
      'case_count':len(candidates),'eligible_count':eligible,'reused_count':reused,
      'review_count':review,'unknown_media_count':unknown,'unavailable_media_count':unavailable,
      'snapshot_hash':store.digest(rows)}


def existing_routes(snapshot, agent_id, video_model=None):
    """Yield only incomplete, exact-boundary media in the fixed case snapshot."""
    for case in snapshot.get('cases',[]):
        if case.get('status')!='pending_consent':
            continue
        for route in routes(case,agent_id,allow_legacy_unknown=False):
            if any(item['id']==route['message_ids'][0] and item['kind']=='video'
                   for item in case.get('media',[])):
                if not video_model:
                    continue
                route['video_model']=video_model
            yield route


def grant_for_message(cur, tenant, message_id):
    """Return the durable unified-grant row if it snapshots this exact media ID."""
    cur.execute("SELECT to_regclass('public.saas_ai_media_grants') AS table_name")
    if not cur.fetchone().get('table_name'):
        return None
    cur.execute('''SELECT g.id,g.status,g.approved_at,c.progress FROM saas_ai_media_grants g
      JOIN saas_knowledge_campaigns c ON c.tenant_id=g.tenant_id AND c.id=g.campaign_id
      WHERE g.tenant_id=%s::uuid AND c.progress->>'mode'='case_media_batch'
      ORDER BY g.created_at DESC''',(tenant,))
    for grant in cur.fetchall():
        for case in (grant.get('progress') or {}).get('case_plans',[]):
            if any(str(media.get('id'))==str(message_id) for media in case.get('media',[])):
                return {'id':str(grant['id']),'status':grant['status'],'approved':bool(grant.get('approved_at'))}
    return None
