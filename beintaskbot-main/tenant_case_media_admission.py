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


def routes(planned, agent_id):
    """One source per fragment avoids the collector's four-file/two-visual cap."""
    for item in planned['media']:
        if (item['complete'] or item['kind'] not in SUPPORTED
            or item['status'] not in {'pending_processing','pending_consent','unknown'}
            or item['status']=='unknown' and item['code'] not in {'status_not_recorded','source_version_unverified'}):
            continue
        yield {'provider':'connect', 'channel_id':planned['channel_id'],
            'conversation_id':planned['conversation_id'], 'history_mode':'channel',
            'message_ids':[item['id']], 'period_start':item['at'],
            'period_end':item['period_end'], 'analysis_agent_id':str(agent_id),
            'source_version':item['source_version'], 'case_id':planned['case_id'],
            'case_version':planned['case_version'], 'case_plan_hash':planned['plan_hash']}
