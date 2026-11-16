"""Authenticated, no-store GET endpoint for agent activity reports."""
import asyncio
from aiohttp import web
from tenant_linear_policy import TenantLinearError
from tenant_agent_reports import report


def handler(member_from_request, logger):
    async def handle(request):
        headers={'Cache-Control':'no-store'}
        try:
            profile=member_from_request(request)
            if not profile: raise TenantLinearError('Нет доступа.',403)
            q=request.query
            if q.get('expected_tenant_id') and q['expected_tenant_id']!=str(profile.get('tenant_id')):
                raise TenantLinearError('Компания изменилась. Откройте отчёт заново.',409)
            if q.get('expected_user_id') and q['expected_user_id']!=str(profile.get('telegram_id')):
                raise TenantLinearError('Пользователь изменился. Откройте отчёт заново.',409)
            result=await asyncio.to_thread(report,profile,agent_id=q.get('agent_id',''),
                first=q.get('from',''),last=q.get('to',''),zone=q.get('timezone',''),
                expected_access_version=q.get('expected_access_version',''))
            return web.json_response({'success':True,'result':result},headers=headers)
        except TenantLinearError as error:
            return web.json_response({'success':False,'error':str(error)},status=error.status,headers=headers)
        except Exception:
            logger.exception('Agent report read failed')
            return web.json_response({'success':False,'error':'Отчёт временно недоступен.'},status=503,headers=headers)
    return handle
