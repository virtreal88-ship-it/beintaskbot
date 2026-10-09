"""Same-origin completion/review with explicit company and user binding."""
from aiohttp import web
from tenant_linear_policy import TenantLinearError
from tenant_deal_completion import complete, reviews


def handler(member_from_request, origin, provider, logger, *, review: bool=False):
    async def handle(request):
        headers={'Cache-Control':'no-store'}
        try:
            session=member_from_request(request)
            if not session:raise TenantLinearError('İcazə yoxdur.',403)
            if request.method=='GET' and review:
                result=await reviews(session,None,provider,limit=int(request.rel_url.query.get('limit','50')),offset=int(request.rel_url.query.get('offset','0')))
            else:
                if request.method!='POST' or request.content_type!='application/json' or request.headers.get('Origin','').rstrip('/')!=origin.rstrip('/'):
                    raise TenantLinearError('İcazə yoxdur.',403)
                data=await request.json()
                if not isinstance(data,dict):raise ValueError()
                if (str(data.get('expected_tenant_id')),str(data.get('expected_user_id')))!=(str(session['tenant_id']),str(session['telegram_id'])):
                    raise TenantLinearError('Kabinet dəyişib.',409)
                result=await (reviews(session,data,provider) if review else complete(session,data,provider))
            return web.json_response({'success':True,**result},headers=headers)
        except TenantLinearError as error:
            return web.json_response({'success':False,'error':str(error),'keep_request':error.status==409},status=error.status,headers=headers)
        except (ValueError,TypeError,KeyError):
            return web.json_response({'success':False,'error':'Sorğu düzgün deyil.'},status=400,headers=headers)
        except Exception:
            logger.error('Tenant deal completion unavailable')
            return web.json_response({'success':False,'error':'Nəticəni yoxlayın. Yeni sorğu yaratmayın.','keep_request':True},status=503,headers=headers)
    return handle
