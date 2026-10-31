"""Same-origin support APIs; no CRM transport dependency."""
import asyncio
import json
from aiohttp import web
from tenant_ai_agents_api import _origin, _expected, _json_dumps
from tenant_linear_policy import TenantLinearError
from tenant_support_inbox_store import listing, detail, create, update, complete
from tenant_support_inbox_summary import regenerate


def handler(member_from_request, origin, logger, resource):
    async def handle(request):
        headers = {'Cache-Control':'no-store'}
        try:
            profile = member_from_request(request)
            if not profile: raise TenantLinearError('Нет доступа.',403)
            if request.method=='GET':
                if resource=='tickets':
                    result = await asyncio.to_thread(listing,profile,request.query.get('status','unresolved'),request.query.get('assignee',''),request.query.get('offset',0),request.query.get('search',''),request.query.get('channel_id',''))
                elif resource=='consultation':
                    from tenant_internal_consultations import listing as consultation_listing
                    result=await asyncio.to_thread(consultation_listing,profile,request.query.get('case_id',''))
                elif resource=='consultation_group':
                    from tenant_consultation_group import read
                    result=await asyncio.to_thread(read,profile,request.match_info['consultation_id'])
                elif resource=='consultation_groups':
                    from tenant_consultation_group import destinations
                    result=await asyncio.to_thread(destinations,profile)
                else:
                    result = await asyncio.to_thread(detail,profile,request.match_info['ticket_id'],request.query.get('before'),request.query.get('message_id'))
            else:
                _origin(request,origin)
                if request.content_type!='application/json': raise TenantLinearError('Ожидается JSON.',415)
                if request.content_length is not None and request.content_length>20000: raise TenantLinearError('Слишком большой запрос.',413)
                raw = await request.read()
                if len(raw)>20000: raise TenantLinearError('Слишком большой запрос.',413)
                data = json.loads(raw)
                if not isinstance(data,dict): raise TenantLinearError('Некорректный запрос.')
                _expected(data,profile)
                payload = {k:v for k,v in data.items() if k not in {'expected_tenant_id','expected_user_id'}}
                if resource=='complete' and request.method=='POST':
                    result = await asyncio.to_thread(complete,profile,payload)
                elif resource=='tickets' and request.method=='POST':
                    result = await asyncio.to_thread(create,profile,payload)
                elif resource=='ticket' and request.method=='PATCH':
                    result = await asyncio.to_thread(update,profile,request.match_info['ticket_id'],payload)
                elif resource=='summary' and request.method=='POST':
                    result = await asyncio.to_thread(regenerate,profile,request.match_info['ticket_id'])
                elif resource=='consultation' and request.method=='POST':
                    from tenant_internal_consultations import create as create_consultation
                    result=await asyncio.to_thread(create_consultation,profile,payload)
                elif resource=='consultation_reply' and request.method=='POST':
                    from tenant_internal_consultations import reply
                    result=await asyncio.to_thread(reply,profile,request.match_info['consultation_id'],payload)
                elif resource=='consultation_group' and request.method=='POST':
                    from tenant_consultation_group import prepare,dispatch,save_draft
                    action=payload.pop('action',None);key=request.match_info['consultation_id']
                    if action=='prepare':result=await asyncio.to_thread(prepare,profile,key,payload)
                    elif action=='send' and set(payload)=={'request_id'}:result=await asyncio.to_thread(dispatch,profile,key,payload['request_id'])
                    elif action=='draft':result=await asyncio.to_thread(save_draft,profile,key,payload)
                    else:raise TenantLinearError('Некорректный запрос.',400)
                else: raise TenantLinearError('Метод не поддерживается.',405)
            return web.json_response({'success':True,'result':result},headers=headers,dumps=_json_dumps)
        except TenantLinearError as error:
            return web.json_response({'success':False,'error':str(error)},status=error.status,headers=headers)
        except (TypeError,ValueError,KeyError):
            return web.json_response({'success':False,'error':'Некорректный запрос.'},status=400,headers=headers)
        except Exception:
            logger.exception('Support inbox request failed')
            return web.json_response({'success':False,'error':'Очередь обращений временно недоступна.'},status=503,headers=headers)
    return handle
