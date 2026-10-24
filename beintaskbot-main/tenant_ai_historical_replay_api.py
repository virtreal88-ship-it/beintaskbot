"""Owner-only transport for the existing one-shot pilot runner. No worker queue."""
import asyncio
import json
import tempfile
import uuid
from pathlib import Path
from aiohttp import web
import tenant_ai_historical_replay as replay
from tenant_ai_agents_store import _authorize
from tenant_platform import _connect
from tenant_linear_policy import TenantLinearError
from tenant_request_origin import require_json_post


def authorize(profile):
    with _connect() as conn:
        with conn.cursor() as cur:
            tenant,actor=_authorize(cur,profile)
            cur.execute('SELECT role FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s',(tenant,actor))
            if tenant!=replay.PILOT_TENANT or (cur.fetchone() or {}).get('role')!='owner':
                raise TenantLinearError('replay_owner_required',403)


def report_path(request_id):
    try: value=str(uuid.UUID(str(request_id)))
    except (ValueError,TypeError):raise TenantLinearError('replay_request_id_invalid',400) from None
    root=Path(tempfile.gettempdir())/'beintaskbot-historical-replay'
    root.mkdir(mode=0o700,exist_ok=True)
    if root.is_symlink():raise TenantLinearError('replay_storage_unavailable',503)
    return root/(replay.PILOT_TENANT+'-'+value+'.json')


def read_report(request_id):
    path=report_path(request_id)
    if not path.exists():raise TenantLinearError('replay_report_not_found',404)
    return json.loads(path.read_text(encoding='utf-8'))


def operate(profile,data):
    authorize(profile)
    action=data.get('action')
    if action=='eligible_examples' and set(data)<={'action','message_ids'}:
        return replay.eligible_examples(profile,data.get('message_ids'))
    if action=='preflight' and set(data)=={'action','message_ids'}:
        return replay.preflight(profile,data['message_ids'])
    if action!='execute' or set(data)!={'action','message_ids','request_id'}:
        raise TenantLinearError('replay_request_invalid',400)
    # Validate all five scoped sources and financial guards even on a duplicate.
    replay.preflight(profile,data['message_ids'])
    path=report_path(data['request_id'])
    if path.exists():
        old=read_report(data['request_id'])
        if [x['id'] for x in old['preflight']['messages']]!=data['message_ids']:
            raise TenantLinearError('replay_request_id_conflict',409)
        return old  # Includes partial/unknown results; never resubmit them.
    from scripts.whatsapp_replay import run_batch
    return run_batch(profile,data['message_ids'],path,execute=True)


def inventory(profile):
    authorize(profile)
    from scripts.audit_whatsapp_recovery import inventory as inspect
    with _connect() as conn:return inspect(conn,profile)


def handler(member_from_request,origin,logger):
    async def handle(request):
        headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}
        try:
            profile=member_from_request(request)
            await asyncio.to_thread(authorize,profile)
            if request.method=='GET':
                if set(request.query)=={'inventory'} and request.query['inventory']=='1':
                    result=await asyncio.to_thread(inventory,profile)
                elif set(request.query)=={'request_id'}:
                    result=await asyncio.to_thread(read_report,request.query['request_id'])
                else:raise TenantLinearError('replay_request_invalid',400)
            else:
                require_json_post(request,origin)
                if request.content_length is not None and request.content_length>4096:
                    raise TenantLinearError('replay_request_too_large',413)
                raw=await request.content.read(4097)
                if len(raw)>4096:raise TenantLinearError('replay_request_too_large',413)
                data=json.loads(raw)
                if not isinstance(data,dict):raise TenantLinearError('replay_request_invalid',400)
                result=await asyncio.to_thread(operate,profile,data)
            return web.json_response({'success':True,'result':result},headers=headers,dumps=lambda value:json.dumps(value,default=str))
        except TenantLinearError as exc:
            return web.json_response({'success':False,'error':str(exc),**({'details':exc.details} if hasattr(exc,'details') else {})},status=exc.status,headers=headers)
        except (ValueError,TypeError,KeyError):
            return web.json_response({'success':False,'error':'replay_request_invalid'},status=400,headers=headers)
        except Exception as exc:
            logger.error('Historical replay boundary failed type=%s',type(exc).__name__)
            return web.json_response({'success':False,'error':'replay_stopped_check_report_and_ledger'},status=503,headers=headers)
    return handle
