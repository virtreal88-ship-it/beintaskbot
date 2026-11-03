"""Authenticated, no-cache HTTP boundary for manual customer memory."""
import asyncio
import json
from aiohttp import web
from tenant_crm_request_context import require_expected_context, CRMContextError
from tenant_linear_policy import TenantLinearError
from tenant_request_origin import require_json_post
import tenant_customer_memory as memory


def handler(member, origin, logger):
    async def endpoint(request):
        headers={'Cache-Control':'no-store','Vary':'Cookie'}
        profile=member(request)
        if not profile:
            return web.json_response({'success':False,'error':'unavailable'},status=403,headers=headers)
        try:
            require_expected_context(request,profile)
            contact=request.match_info['contact_id']
            if request.method=='GET':
                if set(request.query)-{'expected_tenant_id','expected_user_id'}:
                    raise TenantLinearError('Invalid query.',400)
                result=await asyncio.to_thread(memory.read,profile,contact)
            else:
                require_json_post(request,origin)
                if request.content_length is not None and request.content_length>20_000:
                    raise TenantLinearError('Request too large.',413)
                raw=await request.read()
                if len(raw)>20_000: raise TenantLinearError('Request too large.',413)
                body=json.loads(raw)
                if not isinstance(body,dict): raise TenantLinearError('Invalid request.',400)
                action=body.get('action')
                if action=='confirm_link' and set(body)=={'action','channel_id','conversation_id','confirm'}:
                    result=await asyncio.to_thread(memory.confirm_link,profile,contact,body['channel_id'],body['conversation_id'],body['confirm'])
                elif action=='revoke_link' and set(body)=={'action','link_id','expected_version'}:
                    result=await asyncio.to_thread(memory.revoke_link,profile,contact,body['link_id'],body['expected_version'])
                elif action=='add_fact' and set(body)=={'action','fact'}:
                    result=await asyncio.to_thread(memory.add_fact,profile,contact,body['fact'])
                elif action in {'edit','confirm','outdated','refute','delete'} and set(body)==({'action','fact_id','expected_version','value'} if action=='edit' else {'action','fact_id','expected_version'}):
                    result=await asyncio.to_thread(memory.change_fact,profile,contact,body['fact_id'],body['expected_version'],action,value=body.get('value'))
                else: raise TenantLinearError('Invalid request.',400)
            return web.json_response({'success':True,'data':result},dumps=lambda x:json.dumps(x,default=str),headers=headers)
        except CRMContextError as exc:
            return web.json_response({'success':False,'error':'context_changed'},status=exc.status,headers=headers)
        except TenantLinearError as exc:
            return web.json_response({'success':False,'error':'memory_unavailable' if exc.status in (403,404) else 'memory_conflict' if exc.status==409 else 'memory_invalid'},status=exc.status,headers=headers)
        except (ValueError,TypeError):
            return web.json_response({'success':False,'error':'memory_invalid'},status=400,headers=headers)
        except Exception:
            # Do not log customer input or source content.
            logger.warning('Customer memory request unavailable')
            return web.json_response({'success':False,'error':'memory_unavailable'},status=503,headers=headers)
    return endpoint
