"""Owner channel verification/settings and reviewer delivery actions, same-origin only."""
import asyncio
from aiohttp import web
from tenant_news_telegram_settings import read,save
from tenant_news_telegram_policy import verify,settings,address
from tenant_news_telegram_queue import operate
from tenant_linear_policy import TenantLinearError


def handler(member_from_request,canonical_origin,bot_getter,logger,*,configuration=False):
    async def handle(request):
        headers={'Cache-Control':'no-store'}
        try:
            profile=member_from_request(request)
            if not profile:raise TenantLinearError('İcazə yoxdur.',403)
            if request.method=='GET':
                result=await asyncio.to_thread(read,profile,owner_only=configuration)
                if configuration:
                    try:
                        me=await bot_getter().get_me()
                        result['bot_username']='@'+me.username if me.username else ''
                    except Exception:result['bot_username']=''
            else:
                if request.content_type!='application/json' or request.headers.get('Origin','').rstrip('/')!=canonical_origin.rstrip('/'):
                    raise TenantLinearError('Sorğunun mənbəyi düzgün deyil.',403)
                data=await request.json()
                if not isinstance(data,dict):raise TenantLinearError('Sorğu düzgün deyil.')
                if str(data.get('expected_tenant_id') or '')!=str(profile['tenant_id']) or str(data.get('expected_user_id') or '')!=str(profile['telegram_id']):
                    raise TenantLinearError('Kabinet dəyişib. Yeniləyin.',409)
                if configuration:
                    # Fresh owner check before read-only Telegram calls; save rechecks under lock.
                    await asyncio.to_thread(read,profile,owner_only=True);config=settings(data)
                    verified=None
                    if config['enabled'] or data.get('verify') is True:
                        bot=bot_getter()
                        if not bot:raise TenantLinearError('Telegram botu hazır deyil.',503)
                        verified=await verify(bot,address(data.get('channel')),int(profile['telegram_id']))
                    result=await asyncio.to_thread(save,profile,data,verified)
                else:result=await asyncio.to_thread(operate,profile,data)
            return web.json_response({'success':True,**result},headers=headers)
        except TenantLinearError as error:return web.json_response({'success':False,'error':str(error)},status=error.status,headers=headers)
        except (ValueError,TypeError,KeyError):return web.json_response({'success':False,'error':'Sorğu düzgün deyil.'},status=400,headers=headers)
        except Exception:
            logger.error('Tenant news Telegram API unavailable')
            return web.json_response({'success':False,'error':'Kanal yoxlanılmadı. Botun hüquqlarını yoxlayın.'},status=503,headers=headers)
    return handle
