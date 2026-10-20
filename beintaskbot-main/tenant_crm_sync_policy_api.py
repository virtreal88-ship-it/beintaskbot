"""Owner-only HTTP API for external CRM sync direction and field allowlists."""
import asyncio
from aiohttp import web
from tenant_crm_sync_policy import read_settings,save_settings,SyncPolicyError
from tenant_request_origin import require_json_post

def handler(member_from_request,origin,logger):
    async def handle(request):
        profile=member_from_request(request)
        if not profile or profile.get('role')!='owner':
            return web.json_response({'success':False,'error':'forbidden'},status=403,headers={'Cache-Control':'no-store'})
        try:
            if request.method=='GET':result=await asyncio.to_thread(read_settings,profile)
            elif request.method=='POST':
                require_json_post(request,origin)
                body=await request.json()
                if not isinstance(body,dict) or set(body)!={'settings'}:raise SyncPolicyError('Sinxronizasiya ayarları düzgün deyil.')
                result=await asyncio.to_thread(save_settings,profile,body['settings'])
            else:return web.json_response({'success':False,'error':'method_not_allowed'},status=405)
            return web.json_response({'success':True,'result':result},headers={'Cache-Control':'no-store'})
        except SyncPolicyError as exc:
            return web.json_response({'success':False,'error':str(exc)},status=403 if 'Yalnız şirkət sahibi' in str(exc) else 400,headers={'Cache-Control':'no-store'})
        except Exception:
            logger.exception('External CRM sync policy request failed')
            return web.json_response({'success':False,'error':'crm_sync_policy_unavailable'},status=503,headers={'Cache-Control':'no-store'})
    return handle
