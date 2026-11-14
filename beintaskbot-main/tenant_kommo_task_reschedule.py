"""Deadline-only Kommo writes for currently visible tasks, independently of native rights."""
import asyncio
from datetime import datetime, timezone
from aiohttp import web
from tenant_task_flow import TaskFlowError, _uuid, _positive, query, _rows
from tenant_policy import TenantPolicy, positive_id
from tenant_kommo_board_commands import execute, read_payload

OPERATION='kommo_task_reschedule'


def timestamp(value, *, nullable=False):
    if value is None and nullable: return 0
    try:
        date=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if date.tzinfo is None: raise ValueError()
        result=int(date.timestamp())
        if result<=0: raise ValueError()
        return result
    except (ValueError,TypeError,OverflowError): raise TaskFlowError('board_invalid_deadline') from None


def normalize(data):
    if not isinstance(data,dict) or set(data)!={'request_id','task_id','expected_due_at','due_at','source_revision'}:
        raise TaskFlowError('board_invalid_request')
    if not isinstance(data['source_revision'],str) or len(data['source_revision'])!=64:
        raise TaskFlowError('board_source_sync_required',409)
    return {'source_revision':data['source_revision'],'request_id':_uuid(data['request_id']),'task_id':_positive(data['task_id']),
        'expected_due_at':timestamp(data['expected_due_at'],nullable=True),'due_at':timestamp(data['due_at'])}


def authorize_cached(cur,profile,payload):
    if profile.get('active') is not True or not _rows(cur,profile,'kommo',task_id=payload['task_id']):
        raise TaskFlowError('task_forbidden',403)


def prepare(cur, profile, binding, remote, payload, unknown, request):
    policy=TenantPolicy(profile)
    if profile.get('active') is not True: raise TaskFlowError('task_forbidden',403)
    rows=_rows(cur,profile,'kommo',task_id=payload['task_id'])
    if not rows: raise TaskFlowError('task_forbidden',403)
    if not isinstance(remote,dict) or str(remote.get('id'))!=payload['task_id']:
        raise TaskFlowError('task_forbidden',403)
    scope=policy.kommo_task_scope()
    visible=scope['kind']=='all'
    visible=visible or bool(scope.get('marker') and scope['marker'] in str(remote.get('text') or ''))
    visible=visible or bool(scope.get('kommo_user_id') and positive_id(remote.get('created_by'))==scope['kommo_user_id'])
    if not visible and remote.get('entity_type') in ('leads',2,'2') and positive_id(remote.get('entity_id')):
        lead=request(binding,'GET','leads/'+str(positive_id(remote['entity_id'])))
        if str(lead.get('id'))==str(positive_id(remote['entity_id'])):
            visible=policy.can_access_kommo_deal({**lead,'tenant_id':profile['tenant_id'],'raw':lead})
    if not visible: raise TaskFlowError('task_forbidden',403)
    if not unknown:
        if remote.get('is_completed'): raise TaskFlowError('board_task_completed',409)
        if positive_id(remote.get('complete_till'))!=payload['expected_due_at']:
            raise TaskFlowError('board_conflict',409)
        if payload['due_at']<=int(datetime.now(timezone.utc).timestamp()):
            raise TaskFlowError('board_deadline_past',409)
    return {'complete_till':payload['due_at']}


def verified(remote,payload):
    if str(remote.get('id'))!=payload['task_id'] or positive_id(remote.get('complete_till'))!=payload['due_at']:
        raise TaskFlowError('board_outcome_unknown',409)
    return {'task_id':payload['task_id'],'source':'kommo',
        'due_at':datetime.fromtimestamp(payload['due_at'],timezone.utc).isoformat()}


def cache_confirmed(cur,profile,payload,result):
    # Only update the verified scalar, never replace stale task text/relations.
    cur.execute("""UPDATE saas_crm_tasks SET due_at=%s::timestamptz,
      raw=jsonb_set(COALESCE(raw,'{}'::jsonb),'{complete_till}',to_jsonb(%s::bigint))
      WHERE tenant_id=%s::uuid AND kommo_task_id=%s
      AND COALESCE(EXTRACT(EPOCH FROM due_at)::bigint,0) IN (%s,%s)""",
      (result['due_at'],payload['due_at'],profile['tenant_id'],int(payload['task_id']),payload['expected_due_at'],payload['due_at']))


def handlers(*, member, bound_request, origin, logger):
    async def handler(request):
        try:
            profile=member(request)
            if not profile: raise TaskFlowError('task_forbidden',403)
            params=query(request,profile,required=True)
            checking=request.method=='GET'
            if checking:
                if not params.get('request_id'): raise TaskFlowError('board_invalid_request')
                payload=await asyncio.to_thread(read_payload,profile,params['request_id'],OPERATION)
            else:
                if request.content_type!='application/json' or request.headers.get('Origin','').rstrip('/')!=origin.rstrip('/'):
                    raise TaskFlowError('task_forbidden',403)
                payload=normalize(await request.json())
            result=await execute(profile,payload,bound_request,operation=OPERATION,
                path='tasks/'+payload['task_id'],prepare=prepare,verified=verified,verify_only=checking,authorize_cached=authorize_cached,cache_confirmed=cache_confirmed)
            return web.json_response({'success':True,**result},headers={'Cache-Control':'no-store'})
        except TaskFlowError as exc:
            return web.json_response({'success':False,'code':exc.code,'error':exc.code,
                'keep_request':getattr(exc,'keep_request',False),'retry_same_request':getattr(exc,'keep_request',False)},status=exc.status,headers={'Cache-Control':'no-store'})
        except Exception:
            logger.exception('Kommo deadline command unavailable')
            return web.json_response({'success':False,'code':'board_outcome_unknown','error':'board_outcome_unknown','retry_same_request':True,'keep_request':True},status=503,headers={'Cache-Control':'no-store'})
    return handler
