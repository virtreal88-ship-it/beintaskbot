"""Explicit Kommo moves between currently authorized pipeline/stage pairs.

Read visibility by own-created identity is NOT a stage-write grant.
"""
import uuid
from tenant_policy import TenantPolicy
from tenant_task_flow import TaskFlowError


def positive(value):
    if isinstance(value, bool) or not str(value).isdigit() or not 0 < int(value) < 2**63:
        raise ValueError('invalid stage move identifier')
    return int(value)


def normalize(data):
    fields={'request_id','lead_id','expected_pipeline_id','expected_status_id','expected_updated_at','stage_id','source_revision'}
    if not isinstance(data,dict) or set(data) not in (fields, fields|{'target_pipeline_id'}):
        raise ValueError('invalid stage move fields')
    if 'target_pipeline_id' in data: fields=fields|{'target_pipeline_id'}
    if not isinstance(data['source_revision'],str) or len(data['source_revision'])!=64 or any(c not in '0123456789abcdef' for c in data['source_revision']):
        raise ValueError('invalid source revision')
    return {'source_revision':data['source_revision'],'request_id':str(uuid.UUID(str(data['request_id']))),
        **{key:positive(data[key]) for key in fields-{'request_id','source_revision'}}}


def stage_choices(profile, *, target=True):
    policy=TenantPolicy(profile)
    from tenant_member_access import page_capabilities
    if profile.get('active') is not True or not page_capabilities(profile).get('deals',False):
        return []
    # Explicit configured stages only: an empty list must not become wildcard.
    scopes={item['pipeline_id']:item['status_ids'] for item in policy.kommo_pipeline_scope()}
    return [{'pipeline_id':int(p['pipeline_id']), 'stage_id':int(s['stage_id'])}
        for p in policy.deal_board() for s in p.get('stages',[])
        if not (target and int(s['stage_id'])==142 and policy.requires_deal_approval())
        and int(p['pipeline_id']) in scopes and (not scopes[int(p['pipeline_id'])] or int(s['stage_id']) in scopes[int(p['pipeline_id'])])]


def destination(payload):
    return payload.get('target_pipeline_id',payload['expected_pipeline_id'])


def authorize_cached(cur,profile,payload, *, unknown=False):
    options=stage_choices(profile)
    wanted={'pipeline_id':destination(payload),'stage_id':payload['stage_id']}
    source={'pipeline_id':payload['expected_pipeline_id'],'stage_id':payload['expected_status_id']}
    if wanted not in options or source not in stage_choices(profile,target=False) or (destination(payload)!=payload['expected_pipeline_id'] and payload['stage_id']==142):
        raise TaskFlowError('kommo_stage_forbidden',403)
    cur.execute('''SELECT pipeline_id,status_id,raw FROM saas_crm_deals
        WHERE tenant_id=%s::uuid AND kommo_lead_id=%s''',(profile['tenant_id'],payload['lead_id']))
    cached=cur.fetchone()
    if not cached or not isinstance(cached.get('raw'),dict) or cached['raw'].get('source')=='connect':
        raise TaskFlowError('kommo_deal_unavailable',404)
    # A previous uncertain command may already have been synced at its destination.
    # Only these exact pairs are eligible; remote CAS still gates a first write.
    if {'pipeline_id':int(cached['pipeline_id']),'stage_id':int(cached['status_id'])} not in ((source,wanted) if unknown else (source,)):
        raise TaskFlowError('kommo_deal_changed',409)
    return cached


def prepare(cur, profile, binding, remote, payload, unknown=False, request=None):
    if not binding or str(binding.tenant_id)!=str(profile['tenant_id']):
        raise TaskFlowError('kommo_connection_changed',409)
    cached = authorize_cached(cur,profile,payload,unknown=unknown)
    try:
        valid=(isinstance(remote,dict) and not remote.get('is_deleted')
            and positive(remote.get('id'))==payload['lead_id']
            and positive(remote.get('pipeline_id')) in ((payload['expected_pipeline_id'],destination(payload)) if unknown else (payload['expected_pipeline_id'],))
            and (unknown or (positive(remote.get('status_id'))==payload['expected_status_id']
                 and positive(remote.get('updated_at'))==payload['expected_updated_at'])))
    except (ValueError,TypeError):
        valid=False
    if not valid:
        raise TaskFlowError('kommo_deal_changed',409)
    if unknown and remote['pipeline_id'] == payload['expected_pipeline_id'] and remote['status_id'] == payload['expected_status_id']:
        # Only a newer, independently synced snapshot of this same record can
        # retire an uncertain attempt after an external return to its source.
        snapshot = (cached or {}).get('raw') or {}
        keys = ('id', 'pipeline_id', 'status_id', 'updated_at')
        if (type(remote.get('updated_at')) is int and remote['updated_at'] > payload['expected_updated_at']
                and all(remote.get(key) == snapshot.get(key) for key in keys)
                and (remote['pipeline_id'], remote['status_id']) != (destination(payload), payload['stage_id'])):
            raise TaskFlowError('board_changed_since_confirmation', 409)
    # A pipeline is changed only alongside an explicitly selected mapped stage.
    body={'status_id':payload['stage_id']}
    if destination(payload)!=payload['expected_pipeline_id']: body['pipeline_id']=destination(payload)
    return body


def verified(remote, payload):
    if (not isinstance(remote,dict) or remote.get('is_deleted') or positive(remote.get('id'))!=payload['lead_id']
            or positive(remote.get('pipeline_id'))!=destination(payload)
            or positive(remote.get('status_id'))!=payload['stage_id']
            or positive(remote.get('updated_at'))<payload['expected_updated_at']):
        raise TaskFlowError('kommo_stage_outcome_unknown',409)
    return {'lead_id':str(payload['lead_id']), 'pipeline_id':destination(payload),
        'status_id':payload['stage_id'],'updated_at':positive(remote['updated_at'])}


def cache_confirmed(cur,profile,payload,result):
    from datetime import datetime, timezone
    stamp=datetime.fromtimestamp(result['updated_at'],timezone.utc)
    pipeline=next((p for p in TenantPolicy(profile).deal_board()
        if int(p['pipeline_id'])==destination(payload)), {})
    stage_name=next((s.get('name','') for s in pipeline.get('stages',[])
        if int(s['stage_id'])==payload['stage_id']), '')
    cur.execute("""UPDATE saas_crm_deals SET pipeline_id=%s,status_id=%s,stage_name=%s,source_updated_at=%s,synced_at=clock_timestamp(),
        raw=COALESCE(raw,'{}'::jsonb)||jsonb_build_object('pipeline_id',%s::bigint,'status_id',%s::bigint,'updated_at',%s::bigint)
        WHERE tenant_id=%s::uuid AND kommo_lead_id=%s
        AND ((pipeline_id=%s AND status_id=%s) OR (pipeline_id=%s AND status_id=%s))
        AND (source_updated_at IS NULL OR source_updated_at<=%s)
        AND COALESCE(raw->>'source','')<>'connect'""",
        (destination(payload),payload['stage_id'],stage_name,stamp,
         destination(payload),payload['stage_id'],result['updated_at'],profile['tenant_id'],payload['lead_id'],
         payload['expected_pipeline_id'],payload['expected_status_id'],destination(payload),payload['stage_id'],stamp))
    if cur.rowcount!=1: raise TaskFlowError('kommo_deal_changed',409)
    result['stage_name'] = stage_name



async def move(profile,payload,bound_request):
    from tenant_kommo_board_commands import execute
    payload=normalize(payload)
    return await execute(profile,payload,bound_request,operation='deal_stage_move',
        path='leads/'+str(payload['lead_id']),prepare=prepare,verified=verified,authorize_cached=authorize_cached,cache_confirmed=cache_confirmed)
