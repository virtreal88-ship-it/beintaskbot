"""Connect photo inputs: read-only route, bounded cost, no send authority."""
import asyncio,hashlib,os,uuid,time
from datetime import datetime,timedelta
from tenant_platform import _connect
from tenant_connect_transport import connect_route
from tenant_connect_media import download_media,IMAGE_TYPES
from tenant_connect_voice import active,generation_budget_available
from tenant_ai_group_coordination import job_allowed

# Exact IDs only; no prefix inference. Operator overrides are server-owned.
KNOWN_VISION={'gpt-4o','gpt-4o-mini','gpt-4.1','gpt-4.1-mini','gpt-4.1-nano'}
MAX_IMAGES=2
MAX_TOTAL_BYTES=8*1024*1024
TURN_SECONDS=35
_download_slots=asyncio.Semaphore(2)

class ImageInactive(ValueError):pass

def supports_images(model):
    configured=os.environ.get('AI_AGENTS_VISION_MODELS')
    allowed=set(configured.split(',')) if configured is not None else KNOWN_VISION
    return str(model).strip() in {m.strip() for m in allowed if m.strip()}

def live(job,reservation):
    if not active(job,reservation):return False
    from tenant_ai_agent_run_store import load_agent_config
    from tenant_ai_agent_connect import scope_matches
    config=load_agent_config(job)
    if not config or not scope_matches(config.get('scopes'),str(job['channel_id'])):return False
    with _connect() as conn:
        with conn.cursor() as cur:return job_allowed(cur,job)

def resolve(job,reservation,message):
    if not live(job,reservation):raise ImageInactive('image_parent_inactive')
    key=str(uuid.UUID(str(message)));channel=str(uuid.UUID(str(job['channel_id'])))
    with _connect() as conn:
        with conn.cursor() as cur:
            route=connect_route(cur,str(job['tenant_id']),int(job['lead_id']))
            if not route or route['channel_id']!=channel or route['conversation_id']!=job['conversation_id']:raise ValueError('image_scope_mismatch')
            cur.execute("""SELECT 1 FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id=%s
              AND channel=%s AND external_id=%s AND direction='incoming' AND message_type='image'""",
              (job['tenant_id'],int(job['lead_id']),'connect:'+channel,'connect:'+key))
            if not cur.fetchone():raise ValueError('image_scope_mismatch')
    return {'route':route,'message_id':key,'row':{'message_type':'image'}}

def signature(data,mime):
    return {'image/jpeg':data.startswith(b'\xff\xd8\xff'),'image/png':data.startswith(b'\x89PNG\r\n\x1a\n'),
      'image/gif':data.startswith((b'GIF87a',b'GIF89a')),'image/webp':data.startswith(b'RIFF') and data[8:12]==b'WEBP'}.get(mime,False)

async def prepare(job,reservation,rows,model,logger):
    images,analyzed,hashes=[],set(),{};deadline=time.monotonic()+TURN_SECONDS;size=0
    if not supports_images(model):return images,analyzed
    if not await asyncio.to_thread(generation_budget_available,job,reservation):return images,analyzed
    # Only the current unanswered episode, never arbitrary old attachments.
    episode=rows
    for i,r in enumerate(rows):
        if r.get('direction')=='outgoing':episode=rows[i+1:]
    candidates=[r for r in episode if r.get('direction')=='incoming' and r.get('message_type')=='image']
    if rows and rows[-1].get('happened_at'):
        try:
            end=datetime.fromisoformat(rows[-1]['happened_at'].replace('Z','+00:00'))
            candidates=[r for r in candidates if r.get('happened_at') and timedelta(0)<=end-datetime.fromisoformat(r['happened_at'].replace('Z','+00:00'))<=timedelta(minutes=10)]
        except (ValueError,TypeError):candidates=[]
    candidates=candidates[-MAX_IMAGES:]
    for row in candidates:
        if row.get('channel') not in (None,str(job['channel_id'])) and not row.get('photo_message_id'):continue
        try:
            remaining=deadline-time.monotonic()
            if remaining<=0:break
            item=await asyncio.to_thread(resolve,job,reservation,row.get('photo_message_id') or row['external_id'])
            async def bounded_download():
                async with _download_slots:return await download_media(item)
            data,mime=await asyncio.wait_for(bounded_download(),timeout=max(.01,deadline-time.monotonic()))
            if mime not in IMAGE_TYPES or not data or size+len(data)>MAX_TOTAL_BYTES or not signature(data,mime):continue
            if not await asyncio.to_thread(live,job,reservation):raise ImageInactive('image_parent_inactive')
            if not await asyncio.to_thread(generation_budget_available,job,reservation):break
            digest=hashlib.sha256(data).hexdigest()
            if digest not in hashes:
                hashes[digest]=True;size+=len(data)
                images.append({'message_id':str(row['external_id']),'content':data,'content_type':mime,'detail':'low'})
            analyzed.add(str(row['external_id']))
        except ImageInactive:raise
        except Exception:
            logger.warning('Connect photo unavailable or outside permitted scope')
    return images,analyzed
