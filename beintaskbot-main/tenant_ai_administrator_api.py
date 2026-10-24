"""Owner-only review surface for Autonomous Administrator proposals.

This API deliberately exposes approvals, not a generic write proxy.  Applying
is still fail-closed behind AI_AUTONOMOUS_APPLY_ENABLED and only supports the
versioned prompt patch allowlisted by tenant_ai_administrator_apply.
"""
import asyncio

from aiohttp import web

from tenant_ai_dashboard import _connect
from tenant_linear_policy import TenantLinearError
import tenant_ai_administrator_approvals as approvals
import tenant_ai_administrator_apply as application


def _origin(request, canonical_origin):
    if request.method != 'GET' and request.headers.get('Origin','').rstrip('/') != canonical_origin.rstrip('/'):
        raise TenantLinearError('Sorğunun mənbəyi düzgün deyil.',403)


def _with_connection(fn, *args, **kwargs):
    with _connect() as conn:
        try:
            with conn.cursor() as cur:
                result=fn(cur,*args,**kwargs)
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise


def handler(member_from_request, canonical_origin, logger):
    async def handle(request):
        try:
            profile=member_from_request(request)
            if not profile: raise TenantLinearError('İcazə yoxdur.',403)
            _origin(request,canonical_origin)
            if request.method=='GET':
                raw=request.query.get('limit','50')
                try: limit=int(raw)
                except ValueError: raise TenantLinearError('Invalid approval list.',400) from None
                value=await asyncio.to_thread(_with_connection,approvals.listing,profile,limit=limit)
            else:
                if request.content_type != 'application/json': raise TenantLinearError('JSON sorğusu tələb olunur.',415)
                data=await request.json()
                if not isinstance(data,dict): raise TenantLinearError('Invalid approval request.',400)
                action=data.get('action')
                if action=='request':
                    if set(data)!={'action','draft_id','version'}: raise TenantLinearError('Invalid approval request.',400)
                    value=await asyncio.to_thread(_with_connection,approvals.request,profile,data['draft_id'],data['version'])
                elif action in {'approve','reject'}:
                    if set(data)!={'action','approval_id'}: raise TenantLinearError('Invalid approval request.',400)
                    value=await asyncio.to_thread(_with_connection,approvals.approve,profile,data['approval_id'],action)
                elif action=='apply_agent_prompt':
                    if set(data)!={'action','approval_id','draft_id'}: raise TenantLinearError('Invalid approval request.',400)
                    value=await asyncio.to_thread(_with_connection,application.apply_agent_prompt,profile,data['approval_id'],data['draft_id'])
                elif action=='rollback_agent_prompt':
                    if set(data)!={'action','approval_id'}: raise TenantLinearError('Invalid approval request.',400)
                    value=await asyncio.to_thread(_with_connection,application.rollback_agent_prompt,profile,data['approval_id'])
                else: raise TenantLinearError('Invalid approval action.',400)
            return web.json_response({'success':True,'result':value},headers={'Cache-Control':'no-store'})
        except TenantLinearError as exc:
            return web.json_response({'success':False,'error':str(exc)},status=getattr(exc,'status',400),headers={'Cache-Control':'no-store'})
        except Exception:
            logger.exception('AI administrator approval API failed')
            return web.json_response({'success':False,'error':'Approval service is unavailable.'},status=503,headers={'Cache-Control':'no-store'})
    return handle
