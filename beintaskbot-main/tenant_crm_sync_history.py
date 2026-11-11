"""Redacted local CRM event history, authorized by current source-card R.

No provider reads, mutation/recovery, snapshots, identities or actor disclosure.
Journals retain events but update their latest outcome; this is not a complete
append-only state-transition audit and does not verify current provider state.
"""
from datetime import datetime, timezone
from uuid import UUID

from tenant_platform_core import _connect, _ensure_schema
from tenant_crm_native_access import CRMAccessError, current_access, locked_resource

SOURCES = {'contact': ('customers','saas_crm_native_contacts'),
           'deal': ('deals','saas_crm_native_deals'),
           'company': ('companies','saas_crm_native_companies')}
KINDS = ('scalar','relation','legacy')
STATES = {'pending','processing','synced','superseded','disabled','conflict','unknown','needs_verification','failed'}
OUTCOMES = {'not_started','sending','acknowledged','unknown','unverified'}


def _stamp(value):
    try:
        stamp=value if isinstance(value,datetime) else datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if stamp.tzinfo is None or stamp.utcoffset() is None:return None
        return stamp.astimezone(timezone.utc).isoformat()
    except (ValueError,TypeError,OverflowError):return None


def _issue(state,error,outcome):
    if outcome in ('sending','unknown') or state=='unknown':return 'outcome_unknown'
    if state=='needs_verification':return 'needs_verification'
    if state=='superseded':return 'superseded'
    if state=='conflict':return 'conflict'
    if error in {'forbidden','scalar_gate_disabled','scalar_policy_changed','scalar_connection_changed','scalar_mapping_changed'}:
        return 'access_changed'
    if state in ('disabled','failed'):return 'not_completed'
    return 'none'


def _public(row):
    state=row['state'] if row['state'] in STATES else 'unknown'
    outcome=row['attempt_outcome'] if row['attempt_outcome'] in OUTCOMES else 'unverified'
    if outcome=='unknown' or (outcome=='sending' and state=='synced'):state='unknown'
    return {'event_id':str(row['id']),'kind':row['kind'],'state':state,
        'attempt_outcome':outcome,'local_revision':_stamp(row['local_revision']),
        'created_at':_stamp(row['created_at']),'recorded_at':_stamp(row['updated_at']),
        'issue':_issue(state,row['error_code'],outcome)}


def read_history(tenant,entity,ident,*,actor_id,limit=20,before=None,before_id=None,before_kind=None):
    if entity not in SOURCES:raise ValueError('invalid history source')
    tenant,ident=str(UUID(str(tenant))),str(UUID(str(ident)))
    if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=50:raise ValueError('invalid history limit')
    if any(value is not None for value in (before,before_id,before_kind)):
        before=_stamp(before)
        if before is None or before_id is None or before_kind not in KINDS:raise ValueError('invalid history cursor')
        before_id=str(UUID(str(before_id)))
    module,table=SOURCES[entity]
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            # Require an authenticated actor even for internal callers. No
            # actorless integration bypass exists on this read surface.
            if type(actor_id) is not int or actor_id<=0:raise CRMAccessError()
            profile=current_access(cur,tenant,actor_id,module,'view')
            if not locked_resource(cur,profile,module,'view',table,tenant,ident):return None
            # Relation events belong to the SOURCE card, which can differ from
            # changed_id. Never authorize a reverse-card event through target R.
            sql="""WITH events AS (
              SELECT id,'scalar'::text AS kind,state,attempt_outcome,local_revision::text,
                created_at,updated_at,error_code FROM saas_crm_scalar_outbox
                WHERE tenant_id=%s::uuid AND entity_type=%s AND internal_id=%s::uuid
              UNION ALL
              SELECT id,'relation'::text,state,attempt_outcome,intent->>'local_revision',
                created_at,updated_at,error_code FROM saas_crm_relation_outbox
                WHERE tenant_id=%s::uuid AND intent->'source'->>'entity_type'=%s
                  AND intent->'source'->>'internal_id'=%s
              UNION ALL
              SELECT o.id,'legacy'::text,o.state,
                CASE WHEN a.outcome IN ('sending','unknown') THEN a.outcome
                  WHEN a.outcome='not_started' AND a.claim_attempt=o.attempts
                    AND o.state IN ('pending','processing') THEN 'not_started'
                  WHEN o.state='processing' OR (o.state='pending' AND o.attempts>0) THEN 'unknown'
                  WHEN o.state='synced' AND a.outcome='not_started' THEN 'unknown'
                  ELSE 'unverified' END AS attempt_outcome,o.source_updated_at::text,
                o.created_at,o.updated_at,o.error_code
                FROM saas_crm_sync_outbox o LEFT JOIN saas_crm_legacy_delivery_attempts a ON a.event_id=o.id
                WHERE o.tenant_id=%s::uuid AND o.entity_type=%s AND o.internal_id=%s::uuid
            ) SELECT * FROM events"""
            params=[tenant,entity,ident,tenant,entity,ident,tenant,entity,ident]
            if before is not None:
                sql+=' WHERE (created_at,id,kind)<(%s::timestamptz,%s::uuid,%s)'
                params.extend((before,before_id,before_kind))
            sql+=' ORDER BY created_at DESC,id DESC,kind DESC LIMIT %s'
            params.append(limit+1)
            cur.execute(sql,params)
            rows=cur.fetchall()
            more=len(rows)>limit
            events=[_public(row) for row in rows[:limit]]
        conn.commit()
    last=events[-1] if more else None
    return {'events':events,'has_more':more,'next_cursor':({'before':last['created_at'],
        'before_id':last['event_id'],'before_kind':last['kind']} if last else None),
        'external_state_checked':False}
