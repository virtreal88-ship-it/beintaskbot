"""Instruction HTTP boundary; no model, transport, agent settings or /webapp."""
import asyncio,json,tempfile,re
from contextlib import asynccontextmanager
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
                    if asset and asset.endswith('.mp4'):
                        return await stream_video(request,profile,key,asset[:-4],headers)
                    if asset:
                        result=await asyncio.to_thread(store.private_video if asset.endswith('.mp4') else store.private_asset,profile,key,asset[:-4] if asset.endswith('.mp4') else asset)
                    else:result=await asyncio.to_thread(store.operation,profile,'read' if key else 'list',data,key)
                else:
                    _origin(request,origin)
                    if request.content_type=='multipart/form-data':
                        async with upload_slot():
                            with tempfile.TemporaryFile() as video:
                                data=await read_upload(request,profile,key,video)
                                data['_video_body']=video
                                result=await asyncio.to_thread(store.operation,profile,'video_upload',data,key)
                                return web.json_response({'success':True,'result':result},headers=headers)
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


UPLOAD_SLOTS=asyncio.Semaphore(1)
DOWNLOAD_SLOTS=asyncio.Semaphore(2)

@asynccontextmanager
async def upload_slot():
    if UPLOAD_SLOTS.locked():raise TenantLinearError('Video upload is busy; retry later',429)
    async with UPLOAD_SLOTS:yield

async def read_upload(request,profile,key,video):
    from tenant_instruction_videos import MAX_BYTES
    reader=await request.multipart();part=await reader.next()
    if not part or part.name!='metadata':raise ValueError('metadata_first')
    raw=bytearray()
    while chunk:=await part.read_chunk(8192):
        raw.extend(chunk)
        if len(raw)>8192:raise ValueError('metadata_size')
    data=json.loads(raw)
    if not isinstance(data,dict):raise ValueError('metadata')
    _expected(data,profile)
    await asyncio.to_thread(store.operation,profile,'read',{},key)
    if data.get('action')!='video_upload':raise ValueError('upload_action')
    part=await reader.next()
    if not part or part.name!='video':raise ValueError('video')
    size=0
    while chunk:=await part.read_chunk(65536):
        size+=len(chunk)
        if size>MAX_BYTES:raise TenantLinearError('Video exceeds 100 MiB',413)
        video.write(chunk)
    video.seek(0)
    return data


def byte_range(value,size):
    if not value:return 0,size-1,200
    match=re.fullmatch(r'bytes=(\d*)-(\d*)',value)
    if not match or not any(match.groups()):raise ValueError('range')
    left,right=match.groups()
    if not left:
        count=int(right)
        if count<=0:raise ValueError('range')
        return max(0,size-count),size-1,206
    start=int(left);end=min(int(right),size-1) if right else size-1
    if start>=size or start>end:raise ValueError('range')
    return start,end,206


async def stream_video(request,profile,key,asset,headers):
    from tenant_instruction_videos import DOWNLOAD_CHUNK_BYTES
    # Authenticate before revealing size or satisfying/rejecting any Range.
    info=await asyncio.to_thread(store.private_video_info,profile,key,asset)
    size=info['size_bytes'];headers={**headers,'Accept-Ranges':'bytes','ETag':'"'+info['sha256']+'"'}
    try:start,end,status=byte_range(request.headers.get('Range'),size)
    except ValueError:
        return web.Response(status=416,headers={**headers,'Content-Range':'bytes */'+str(size)})
    if DOWNLOAD_SLOTS.locked():raise TenantLinearError('Video preview is busy; retry later',429)
    async with DOWNLOAD_SLOTS:
        if status==206:headers['Content-Range']=f'bytes {start}-{end}/{size}'
        response=web.StreamResponse(status=status,headers={**headers,'Content-Type':'video/mp4','Content-Length':str(end-start+1)})
        await response.prepare(request)
        try:
            while start<=end:
                chunk=await asyncio.to_thread(store.private_video,profile,key,asset,start,min(DOWNLOAD_CHUNK_BYTES,end-start+1))
                if not chunk:raise ConnectionResetError('Video no longer available')
                await response.write(chunk);start+=len(chunk)
            await response.write_eof()
        except (ConnectionResetError,TenantLinearError):
            response.force_close()
        return response
