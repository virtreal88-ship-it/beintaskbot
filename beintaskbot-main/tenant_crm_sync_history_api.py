"""Read-only, no-store source-card CRM event history."""
import asyncio
from uuid import UUID
from aiohttp import web
from tenant_policy import TenantPolicy
from tenant_crm_native_access import CRMAccessError
from tenant_crm_request_context import require_expected_context, native_policy_profile, CRMContextError
from tenant_crm_sync_history import SOURCES, read_history

HEADERS={'Cache-Control':'private, no-store, max-age=0','Pragma':'no-cache','Vary':'Cookie'}


def handler(member_from_request,logger,*,entity):
    if entity not in SOURCES:raise ValueError('invalid history source')
    async def handle(request):
        def response(data,status=200):return web.json_response(data,status=status,headers=HEADERS)
        try:
            if request.method!='GET':return response({'success':False,'error':'method_not_allowed'},405)
            profile=member_from_request(request)
            if not profile:return response({'success':False,'error':'forbidden'},403)
            profile=native_policy_profile(profile)
            require_expected_context(request,profile)
            if not TenantPolicy(profile).allows_action(SOURCES[entity][0],'view'):
                raise CRMAccessError()
            ident=str(UUID(str(request.match_info.get('id'))))
            allowed={'limit','before','before_id','before_kind','expected_tenant_id','expected_user_id'}
            if set(request.query)-allowed:raise ValueError('invalid history query')
            for key in request.query:
                if hasattr(request.query,'getall') and len(request.query.getall(key))!=1:
                    raise ValueError('invalid history query')
            raw=request.query.get('limit','20')
            if not str(raw).isascii() or not str(raw).isdigit():raise ValueError('invalid history limit')
            history=await asyncio.to_thread(read_history,str(profile['tenant_id']),entity,ident,
                actor_id=profile.get('telegram_id') or 0,limit=int(raw),before=request.query.get('before'),
                before_id=request.query.get('before_id'),before_kind=request.query.get('before_kind'))
            if history is None:return response({'success':False,'error':'not_found'},404)
            return response({'success':True,'history':history})
        except CRMContextError as exc:return response({'success':False,'error':exc.code},exc.status)
        except CRMAccessError as exc:return response({'success':False,'error':exc.code},exc.status)
        except (ValueError,TypeError):return response({'success':False,'error':'invalid_history_query'},400)
        except Exception:
            logger.exception('CRM local history read failed')
            return response({'success':False,'error':'history_unavailable'},503)
    return handle
