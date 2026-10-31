"""HTTP adapters for authenticated share management and public read-only cases."""
import base64
import json
import os
from aiohttp import web
from tenant_ai_agents_api import _origin, _expected
from tenant_linear_policy import TenantLinearError
import tenant_public_case as cases

_PUBLIC_HEADERS={'Cache-Control':'no-store, private, max-age=0','Pragma':'no-cache',
 'X-Robots-Tag':'noindex, nofollow, noarchive','Referrer-Policy':'no-referrer',
 'X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; media-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"}


def owner_handler(member_from_request,origin):
    async def handle(request):
        try:
            profile=member_from_request(request,owner_only=True)
            if not profile:raise TenantLinearError('Нет доступа.',403)
            if request.method=='GET':
                result=await __import__('asyncio').to_thread(cases.list_shares,profile,request.match_info['ticket_id'])
                return web.json_response({'success':True,'result':result},headers={'Cache-Control':'no-store'})
            _origin(request,origin)
            if request.content_type!='application/json':raise TenantLinearError('Ожидается JSON.',415)
            data=await request.json()
            _expected(data,profile)
            if request.method=='POST':
                payload={k:v for k,v in data.items() if k not in {'expected_tenant_id','expected_user_id'}}
                result=await __import__('asyncio').to_thread(cases.create,profile,request.match_info['ticket_id'],payload,origin)
            elif request.method=='DELETE':
                result=await __import__('asyncio').to_thread(cases.revoke,profile,request.match_info['share_id'])
            else:raise TenantLinearError('Метод не поддерживается.',405)
            return web.json_response({'success':True,'result':result},headers={'Cache-Control':'no-store'})
        except TenantLinearError as exc:return web.json_response({'success':False,'error':str(exc)},status=exc.status,headers={'Cache-Control':'no-store'})
        except Exception:return web.json_response({'success':False,'error':'Ссылку обращения не удалось обработать.'},status=503,headers={'Cache-Control':'no-store'})
    return handle


def public_handlers(logger):
    async def page(request):
        try:
            result=await __import__('asyncio').to_thread(cases.read,request.match_info['token'],request.remote or '')
            path=os.path.join(os.path.dirname(os.path.abspath(__file__)),'..','docs','public-case.html')
            with open(path,encoding='utf-8') as source:html=source.read()
            safe=json.dumps(result,ensure_ascii=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
            html=html.replace('__CASE_DATA__',safe)
            return web.Response(text=html,content_type='text/html',headers=_PUBLIC_HEADERS)
        except TenantLinearError as exc:return web.Response(status=exc.status,text='This case page is unavailable.',content_type='text/plain',headers=_PUBLIC_HEADERS)
        except Exception:
            logger.exception('Public case page failed')
            return web.Response(status=503,text='This case page is unavailable.',content_type='text/plain',headers=_PUBLIC_HEADERS)
    async def media(request):
        try:
            value=await __import__('asyncio').to_thread(cases.media,request.match_info['token'],request.match_info['message_id'],request.remote or '')
            content=base64.b64decode(value['base64'],validate=True)
            name='case-attachment.'+{'image/jpeg':'jpg','image/png':'png','image/webp':'webp','image/gif':'gif','video/mp4':'mp4','audio/ogg':'ogg','application/ogg':'ogg','audio/mpeg':'mp3','audio/mp4':'m4a','audio/wav':'wav','audio/x-wav':'wav','audio/webm':'webm','audio/flac':'flac','audio/x-flac':'flac','audio/aac':'aac','application/pdf':'pdf','text/plain':'txt','application/vnd.openxmlformats-officedocument.wordprocessingml.document':'docx'}.get(value['mimeType'],'bin')
            headers={**_PUBLIC_HEADERS,'Content-Disposition':f'inline; filename="{name}"'}
            return web.Response(body=content,content_type=value['mimeType'],headers=headers)
        except TenantLinearError as exc:return web.Response(status=exc.status,text='Attachment unavailable.',content_type='text/plain',headers=_PUBLIC_HEADERS)
        except Exception:return web.Response(status=503,text='Attachment unavailable.',content_type='text/plain',headers=_PUBLIC_HEADERS)
    return page,media
