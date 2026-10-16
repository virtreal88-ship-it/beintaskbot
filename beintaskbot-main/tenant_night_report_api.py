"""Same-origin, authenticated read-only night-report endpoints."""
import asyncio
from aiohttp import web
from tenant_linear_policy import TenantLinearError
from tenant_night_report_store import report,detail


def handler(member_from_request,logger,resource='report'):
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
            if resource=='report':
                result=await asyncio.to_thread(report,profile,q.get('date',''),q.get('timezone',''),q.get('start',''),q.get('end',''),q.get('offset',0))
            else:
                result=await asyncio.to_thread(detail,profile,ticket_id=request.match_info.get('ticket_id',''),
                    channel_id=q.get('channel_id',''),conversation_id=q.get('conversation_id',''),before=q.get('before',''))
            return web.json_response({'success':True,'result':result},headers=headers)
        except TenantLinearError as error:
            return web.json_response({'success':False,'error':str(error)},status=error.status,headers=headers)
        except (ValueError,TypeError):
            return web.json_response({'success':False,'error':'Некорректные параметры отчёта.'},status=400,headers=headers)
        except Exception:
            logger.exception('Night report read failed')
            return web.json_response({'success':False,'error':'Отчёт временно недоступен.'},status=503,headers=headers)
    return handle
