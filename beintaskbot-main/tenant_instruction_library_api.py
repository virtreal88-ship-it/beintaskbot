"""Instruction HTTP boundary; no model, transport, agent settings or /webapp."""
import asyncio,json
from aiohttp import web
from tenant_ai_agents_api import _expected,_origin
from tenant_linear_policy import TenantLinearError
import tenant_instruction_library as store

def handler(member,origin,logger,resource='collection'):
    async def handle(request):
        headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff'}
        try:
            key=request.match_info.get('lesson_id');asset=request.match_info.get('asset_id')
            if resource=='public':result=await asyncio.to_thread(store.shared,request.match_info['token'],asset)
            else:
                profile=member(request)
                if not profile:raise TenantLinearError('No access',403)
                if request.method=='GET':
                    data=dict(request.query);_expected(data,profile)
                    if asset:
                        result=await asyncio.to_thread(store.private_video if asset.endswith('.mp4') else store.private_asset,profile,key,asset[:-4] if asset.endswith('.mp4') else asset)
                    else:result=await asyncio.to_thread(store.operation,profile,'read' if key else 'list',data,key)
                else:
                    _origin(request,origin)
                    if request.content_type=='multipart/form-data':
                        from tenant_instruction_videos import MAX_BYTES
                        reader=await request.multipart();part=await reader.next()
                        if not part or part.name!='metadata':raise ValueError('metadata_first')
                        raw=bytearray()
                        while True:
                            chunk=await part.read_chunk(8192)
                            if not chunk:break
                            raw.extend(chunk)
                            if len(raw)>8192:raise ValueError('metadata_size')
                        data=json.loads(raw);_expected(data,profile)
                        await asyncio.to_thread(store.operation,profile,'read',{},key)
                        if data.get('action')!='video_upload':raise ValueError('upload_action')
                        part=await reader.next()
                        if not part or part.name!='video':raise ValueError('video')
                        body=bytearray()
                        while True:
                            chunk=await part.read_chunk(65536)
                            if not chunk:break
                            body.extend(chunk)
                            if len(body)>MAX_BYTES:raise TenantLinearError('Video exceeds 100 MiB',413)
                        data['_video_body']=bytes(body)
                    elif request.content_type=='application/json':
                        data=await request.json()
                    else:raise ValueError('json')
                    if not isinstance(data,dict):raise ValueError('json')
                    _expected(data,profile)
                    result=await asyncio.to_thread(store.operation,profile,data.get('action','save'),data,key)
            if asset:return web.Response(body=result,content_type='video/mp4' if asset.endswith('.mp4') and resource!='public' else 'image/jpeg',headers=headers)
            return web.json_response({'success':True,'result':result},headers=headers)
        except TenantLinearError as e:return web.json_response({'success':False,'error':str(e)},status=e.status,headers=headers)
        except (KeyError,ValueError,TypeError):return web.json_response({'success':False,'error':'Invalid instruction data'},status=400,headers=headers)
        except Exception:
            logger.error('Instruction operation unavailable')
            return web.json_response({'success':False,'error':'Instruction operation unavailable'},status=503,headers=headers)
    return handle
