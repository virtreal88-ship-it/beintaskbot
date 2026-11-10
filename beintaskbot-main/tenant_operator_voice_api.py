"""Fail-closed production boundary for future private Copilot voice sessions."""
import asyncio
import json
from aiohttp import web
from tenant_ai_agents_api import _expected, _origin
from tenant_linear_policy import TenantLinearError
from tenant_operator_voice_context import check
from tenant_operator_voice_orchestration import open_server_session, require_pilot


def handler(member_from_request,origin,logger,resource):
    async def handle(request):
        headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}
        try:
            profile=member_from_request(request)
            if not profile:raise TenantLinearError('Нет доступа.',403)
            if resource=='preflight':
                _origin(request,origin)
                if request.method!='POST' or request.content_type!='application/json':
                    raise TenantLinearError('Метод не поддерживается.',405)
                if request.headers.getall('Content-Length',[]) and request.headers.getall('Transfer-Encoding',[]):
                    raise TenantLinearError('Некорректный запрос.',400)
                if request.content_length is not None and request.content_length>4096:
                    raise TenantLinearError('Слишком большой запрос.',413)
                body=bytearray()
                async for chunk in request.content.iter_chunked(1024):
                    body.extend(chunk)
                    if len(body)>4096:raise TenantLinearError('Слишком большой запрос.',413)
                data=json.loads(bytes(body))
                if not isinstance(data,dict):raise TenantLinearError('Некорректный запрос.',400)
                _expected(data,profile)
                route={k:v for k,v in data.items() if k not in {'expected_tenant_id','expected_user_id'}}
                scope=await asyncio.to_thread(check,profile,route)
                require_pilot(scope)
                # Availability is scoped to the one server-configured account.
                # The actual credential/model check happens on the real
                # provider handshake, never through browser-supplied secrets.
                return web.json_response({'success':True,'result':{'available':True}},headers=headers)
            if resource=='ws':
                # _origin only accepts mutation verbs; a WebSocket handshake
                # is GET, so use its exact canonical Origin check directly.
                if request.method!='GET' or request.headers.get('Origin','').rstrip('/')!=origin.rstrip('/'):
                    raise TenantLinearError('Источник запроса недоступен.',403)
                _expected(request.query,profile)
                # Authentication and single-user policy are checked by the
                # server-owned orchestrator before the WebSocket upgrade.
                return await open_server_session(request,profile,origin,member_from_request=member_from_request)
            raise TenantLinearError('Метод не поддерживается.',405)
        except TenantLinearError as error:
            return web.json_response({'success':False,'error':str(error)},status=error.status,headers=headers)
        except (ValueError,TypeError,KeyError):
            return web.json_response({'success':False,'error':'Некорректный запрос.'},status=400,headers=headers)
        except Exception:
            # Do not log dialog identifiers, request bodies, or credentials.
            logger.warning('Copilot voice admission unavailable')
            return web.json_response({'success':False,'error':'Голосовой режим пока недоступен.'},status=503,headers=headers)
    return handle
