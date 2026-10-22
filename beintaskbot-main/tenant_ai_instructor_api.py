"""Authenticated HTTP boundary for AI Instructor setup and job review."""
import asyncio
from aiohttp import web
from tenant_ai_agents_api import _expected, _origin
from tenant_linear_policy import TenantLinearError
import tenant_ai_instructor_store as store


def handler(member, origin, logger, resource='collection'):
    async def handle(request):
        headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff'}
        try:
            profile=member(request)
            if not profile: raise TenantLinearError('No access',403)
            _expected(request.query,profile) if request.method=='GET' else None
            if resource=='artifact':
                path,mime=await asyncio.to_thread(store.private_artifact,profile,request.match_info['job_id'],request.match_info['artifact_id'])
                return web.FileResponse(path,headers={**headers,'Content-Type':mime,'Content-Security-Policy':"default-src 'none'; sandbox"})
            if request.method=='GET':
                if resource=='connections': result=await asyncio.to_thread(store.list_connections,profile)
                elif resource=='jobs': result=await asyncio.to_thread(store.list_jobs,profile,request.query.get('limit',50))
                elif resource=='job': result=await asyncio.to_thread(store.job_detail,profile,request.match_info['job_id'])
                else: result={'enabled':store._feature_enabled(),'worker':'not_configured',
                              'real_pilot':'unverified','customer_send_allowed':False,'auto_publish':False,
                              'available_actions':['save_test_credentials','view_jobs'],
                              'blocked_actions':['queue_job','activate_connection','run_real_erp','publish_lesson']}
                return web.json_response({'success':True,'result':result},headers=headers)
            _origin(request,origin)
            if request.content_type!='application/json': raise ValueError('json')
            data=await request.json()
            if not isinstance(data,dict): raise ValueError('json')
            _expected(data,profile)
            data={k:v for k,v in data.items() if k not in {'expected_tenant_id','expected_user_id'}}
            if resource=='connections': result=await asyncio.to_thread(store.save_connection,profile,data)
            elif resource=='jobs': result=await asyncio.to_thread(store.enqueue,profile,data)
            elif resource=='job':
                if data.get('action')!='cancel': raise ValueError('action')
                result=await asyncio.to_thread(store.cancel,profile,request.match_info['job_id'])
            else: raise TenantLinearError('Method not allowed',405)
            return web.json_response({'success':True,'result':result},headers=headers)
        except TenantLinearError as exc: return web.json_response({'success':False,'error':str(exc)},status=exc.status,headers=headers)
        except (KeyError,ValueError,TypeError): return web.json_response({'success':False,'error':'Invalid instructor request'},status=400,headers=headers)
        except Exception:
            logger.error('AI Instructor request unavailable')
            return web.json_response({'success':False,'error':'AI Instructor request unavailable'},status=503,headers=headers)
    return handle

