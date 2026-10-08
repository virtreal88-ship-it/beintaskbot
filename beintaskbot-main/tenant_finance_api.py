"""Thin tenant finance HTTP boundary. No provider transfers or global identities."""
import asyncio
from aiohttp import web
from tenant_finance import history,list_members,record
from tenant_finance_policy import TenantFinanceError


def finance_handler(member_from_request, canonical_origin: str, logger):
    async def handle(request: web.Request) -> web.Response:
        headers={'Cache-Control':'no-store'}
        try:
            profile=member_from_request(request)
            if not profile:
                raise TenantFinanceError('İcazə yoxdur.',403)
            if request.method=='GET':
                query=request.rel_url.query
                pagination={'limit':int(query.get('limit','50')),'offset':int(query.get('offset','0'))}
                if query.get('members')=='1':
                    result=await asyncio.to_thread(list_members,profile,**pagination)
                else:
                    result=await asyncio.to_thread(history,profile,member_id=query.get('member_id'),**pagination)
                return web.json_response({'success':True,**result},headers=headers)
            if request.content_type!='application/json' or request.headers.get('Origin','').rstrip('/')!=canonical_origin.rstrip('/'):
                raise TenantFinanceError('Sorğunun mənbəyi düzgün deyil.',403)
            data=await request.json()
            if not isinstance(data,dict):
                raise TenantFinanceError('Sorğu düzgün deyil.')
            if str(data.get('expected_tenant_id') or '')!=str(profile['tenant_id']) or str(data.get('expected_user_id') or '')!=str(profile['telegram_id']):
                raise TenantFinanceError('Kabinet dəyişib. Səhifəni yeniləyin.',409)
            entry=await asyncio.to_thread(record,profile,data)
            return web.json_response({'success':True,'entry':entry},headers=headers)
        except TenantFinanceError as error:
            return web.json_response({'success':False,'error':str(error)},status=error.status,headers=headers)
        except ValueError:
            return web.json_response({'success':False,'error':'Sorğu düzgün deyil.'},status=400,headers=headers)
        except Exception:
            logger.error('Tenant finance API failed')
            return web.json_response({'success':False,'error':'Maliyyə sorğusu alınmadı.'},status=503,headers=headers)
    return handle
