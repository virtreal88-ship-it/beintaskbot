"""Authenticated read-only HTTP surface for Business Analyst tools."""
import asyncio
import json
import logging
from aiohttp import web

from tenant_ai_business_analytics import execute
from tenant_linear_policy import TenantLinearError


def handler(member_from_request, canonical_origin, logger=None):
    log = logger or logging.getLogger(__name__)

    async def handle(request):
        headers = {'Cache-Control': 'no-store'}
        try:
            if request.headers.get('Origin', '').rstrip('/') != canonical_origin.rstrip('/'):
                raise TenantLinearError('Sorğunun mənbəyi düzgün deyil.', 403)
            if request.content_type != 'application/json':
                raise TenantLinearError('Invalid analytics request.', 400)
            profile = member_from_request(request)
            if not profile:
                raise TenantLinearError('İcazə yoxdur.', 403)
            data = await request.json()
            required = {'expected_tenant_id', 'expected_user_id', 'tool', 'arguments'}
            if not isinstance(data, dict) or set(data) != required:
                raise TenantLinearError('Invalid analytics request.', 400)
            if (str(data['expected_tenant_id'] or '') != str(profile.get('tenant_id'))
                    or str(data['expected_user_id'] or '') != str(profile.get('telegram_id'))):
                raise TenantLinearError('Kabinet dəyişib. Yeniləyin.', 409)
            if not isinstance(data['tool'], str) or len(data['tool']) > 80:
                raise TenantLinearError('Invalid analytics request.', 400)
            result = await asyncio.to_thread(execute, profile, data['tool'], data['arguments'])
            return web.json_response({'success': True, 'result': result}, headers=headers)
        except TenantLinearError as error:
            return web.json_response({'success': False, 'error': str(error)}, status=error.status, headers=headers)
        except Exception:
            log.error('Business analytics read failed')
            return web.json_response({'success': False, 'error': 'Business analytics is temporarily unavailable.'},
                                     status=503, headers=headers)

    return handle


def analyst_handler(member_from_request, canonical_origin, logger=None):
    """Authenticated single-turn owner analyst; the server owns all tool execution."""
    log=logger or logging.getLogger(__name__)

    async def handle(request):
        headers={'Cache-Control':'no-store'}
        try:
            if request.headers.get('Origin','').rstrip('/')!=canonical_origin.rstrip('/'):
                raise TenantLinearError('Sorğunun mənbəyi düzgün deyil.',403)
            if request.content_type!='application/json':
                raise TenantLinearError('Invalid analyst request.',400)
            profile=member_from_request(request)
            if not profile:raise TenantLinearError('İcazə yoxdur.',403)
            data=await request.json()
            required={'expected_tenant_id','expected_user_id','request_id','prompt'}
            if not isinstance(data,dict) or set(data)!=required:
                raise TenantLinearError('Invalid analyst request.',400)
            if (str(data['expected_tenant_id'] or '')!=str(profile.get('tenant_id'))
                    or str(data['expected_user_id'] or '')!=str(profile.get('telegram_id'))):
                raise TenantLinearError('Kabinet dəyişib. Yeniləyin.',409)
            if not isinstance(data['request_id'],str) or not isinstance(data['prompt'],str):
                raise TenantLinearError('Invalid analyst request.',400)
            from tenant_ai_business_analyst import run
            result=await asyncio.to_thread(run,profile,prompt=data['prompt'],request_id=data['request_id'])
            return web.json_response({'success':True,'result':result},headers=headers)
        except TenantLinearError as error:
            return web.json_response({'success':False,'error':str(error)},status=error.status,headers=headers)
        except Exception as error:
            from tenant_chat_ai_provider import safe_failure_code
            log.error('Business analyst request failed code=%s',safe_failure_code(error))
            return web.json_response({'success':False,'error':'Business analyst is temporarily unavailable.'},
                                     status=503,headers=headers)

    return handle


def preferences_handler(member_from_request, canonical_origin, logger=None):
    """Owner-only read/update endpoint for the analyst's bounded presentation fields."""
    log=logger or logging.getLogger(__name__)

    def persist(profile, settings=None):
        from tenant_platform import _connect
        from tenant_ai_business_analytics import _owner
        from tenant_ai_business_analyst_preferences import ensure_schema,load,save,validate_settings
        if not isinstance(profile,dict) or profile.get('role')!='owner':
            raise TenantLinearError('Company owner access required.',403)
        with _connect() as conn:
            ensure_schema(conn)
            with conn.cursor() as cur:
                tenant,actor=_owner(cur,profile)
                if settings is None:
                    current=load(cur,tenant)
                else:
                    current=save(cur,tenant,actor,validate_settings(settings))
            if settings is not None:conn.commit()
        from tenant_ai_business_analyst_preferences import DEFAULT_SETTINGS
        return {key:current[key] for key in DEFAULT_SETTINGS}

    async def handle(request):
        headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}
        try:
            origin=request.headers.get('Origin','').rstrip('/')
            if origin and origin!=canonical_origin.rstrip('/'):
                raise TenantLinearError('Sorğunun mənbəyi düzgün deyil.',403)
            profile=member_from_request(request)
            if not profile:
                raise TenantLinearError('İcazə yoxdur.',403)
            from tenant_ai_agents_api import _expected
            if request.method=='GET':
                if set(request.query)!={'expected_tenant_id','expected_user_id'}:
                    raise TenantLinearError('Invalid analyst settings request.',400)
                _expected(request.query,profile)
                result=await asyncio.to_thread(persist,profile)
                return web.json_response({'success':True,'result':{'settings':result}},headers=headers)
            if request.method!='PATCH':
                raise TenantLinearError('Invalid analyst settings request.',405)
            if origin!=canonical_origin.rstrip('/'):
                raise TenantLinearError('Sorğunun mənbəyi düzgün deyil.',403)
            if request.content_type!='application/json':
                raise TenantLinearError('Invalid analyst settings request.',400)
            if request.content_length is not None and request.content_length>8192:
                raise TenantLinearError('Invalid analyst settings request.',413)
            if request.headers.getall('Content-Length',[]) and request.headers.getall('Transfer-Encoding',[]):
                raise TenantLinearError('Invalid analyst settings request.',400)
            body=bytearray()
            async for chunk in request.content.iter_chunked(1024):
                body.extend(chunk)
                if len(body)>8192:
                    raise TenantLinearError('Invalid analyst settings request.',413)
            data=json.loads(bytes(body))
            if not isinstance(data,dict) or set(data)!={'expected_tenant_id','expected_user_id','settings'}:
                raise TenantLinearError('Invalid analyst settings request.',400)
            _expected(data,profile)
            result=await asyncio.to_thread(persist,profile,data['settings'])
            return web.json_response({'success':True,'result':{'settings':result}},headers=headers)
        except TenantLinearError as error:
            return web.json_response({'success':False,'error':str(error)},status=error.status,headers=headers)
        except (ValueError,TypeError,KeyError):
            return web.json_response({'success':False,'error':'Invalid analyst settings request.'},status=400,headers=headers)
        except Exception:
            log.error('Business analyst settings operation failed')
            return web.json_response({'success':False,'error':'Business analyst settings are temporarily unavailable.'},
                                     status=503,headers=headers)

    return handle
