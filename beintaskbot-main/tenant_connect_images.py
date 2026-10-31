"""Connect photo inputs: read-only route, bounded cost, no send authority."""
import asyncio,hashlib,json,os,re,uuid,time
from datetime import datetime,timedelta
from tenant_platform import _connect
from tenant_connect_transport import connect_route
from tenant_connect_media import download_media,IMAGE_TYPES
from tenant_connect_voice import active,generation_budget_available
from tenant_ai_group_coordination import job_allowed

# Exact IDs only; no prefix inference. Operator overrides are server-owned.
KNOWN_VISION={'gpt-4o','gpt-4o-mini','gpt-4.1','gpt-4.1-2025-04-14','gpt-4.1-mini','gpt-4.1-nano'}
MAX_IMAGES=2
MAX_TOTAL_BYTES=8*1024*1024
TURN_SECONDS=35
_download_slots=asyncio.Semaphore(2)

class ImageInactive(ValueError):pass

def _case_image_refs(job,case_id):
    """Find only incoming images explicitly selected into this exact CASE."""
    try:case_id=str(uuid.UUID(str(case_id)))
    except (ValueError,TypeError,AttributeError):return []
    channel=str(uuid.UUID(str(job['channel_id'])))
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute('''SELECT t.channel_id::text AS channel_id,t.conversation_id,c.lead_id
              FROM saas_support_tickets t JOIN saas_connect_conversations c
                ON c.tenant_id=t.tenant_id AND c.connect_channel_id=t.channel_id AND c.conversation_id=t.conversation_id
              WHERE t.tenant_id=%s::uuid AND t.id=%s::uuid''',(job['tenant_id'],case_id))
            route=cur.fetchone()
            if not route or route['channel_id']!=channel or route['conversation_id']!=job.get('conversation_id'):
                return []
            cur.execute('''SELECT m.external_id,m.body,m.raw,m.message_type,m.happened_at
              FROM saas_support_ticket_messages cm JOIN saas_crm_messages m
                ON m.tenant_id=cm.tenant_id AND m.id=cm.message_id
              WHERE cm.tenant_id=%s::uuid AND cm.ticket_id=%s::uuid
                AND m.kommo_lead_id=%s AND m.channel=%s AND m.direction='incoming' AND m.message_type='image'
                AND m.external_id LIKE 'connect:%%'
              ORDER BY COALESCE(m.happened_at,m.created_at) DESC,m.id DESC LIMIT %s''',
              (job['tenant_id'],case_id,int(route['lead_id']),'connect:'+channel,MAX_IMAGES))
            rows=cur.fetchall()
    from tenant_media_evidence import _body_hash,media_version,source_hash
    result=[]
    for row in rows:
        external=str(row.get('external_id') or '')
        message_id=external.removeprefix('connect:')
        if external=='connect:'+message_id and re.fullmatch(r'[A-Za-z0-9_-]{5,200}',message_id):
            ref={'external_id':external,'channel_id':channel,'body_hash':_body_hash(row.get('body')),
                 'media_kind':'image','media_version':media_version(row.get('raw'))}
            result.append({'message_id':message_id,'ref':ref,'source_hash':source_hash(ref),
                           'case_id':case_id,'happened_at':row.get('happened_at')})
    return result

def _cached_case_observation(receipt,case_id):
    result=receipt.get('result') if isinstance(receipt,dict) and isinstance(receipt.get('result'),dict) else {}
    observations=result.get('case_observations') if isinstance(result.get('case_observations'),list) else []
    exact=next((item for item in observations if isinstance(item,dict) and item.get('case_id')==case_id),None)
    if exact and exact.get('relevance') in {'relevant','irrelevant','unclear'} and isinstance(exact.get('text'),str):
        return {**exact,'source':'case_receipt'}
    # Older reusable media receipts can supply content, but carry no CASE
    # relevance classification. Keep them explicitly unclassified.
    if receipt.get('status') in {'processed','partial'} and result:
        raw=[]
        if isinstance(result.get('observation'),str) and result['observation'].strip():raw.append(result['observation'])
        raw.extend(x.get('text') for x in result.get('observations',[]) if isinstance(x,dict) and isinstance(x.get('text'),str))
        if raw:return {'case_id':case_id,'relevance':'unclear','text':' '.join(raw)[:500],
                       'source':'existing_unclassified_receipt'}
    return None

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
            cur.execute("""SELECT raw FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id=%s
              AND channel=%s AND external_id=%s AND direction='incoming' AND message_type='image'""",
              (job['tenant_id'],int(job['lead_id']),'connect:'+channel,'connect:'+key))
            row=cur.fetchone()
            if not row:raise ValueError('image_scope_mismatch')
    return {'route':route,'message_id':key,'row':{'message_type':'image','raw':row.get('raw') or {}}}

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

async def prepare_case(job,reservation,case_id,model,logger):
    """Prepare only CASE-member photos; cache hits avoid provider downloads."""
    images,analyzed,artifacts=[],set(),[]
    if not supports_images(model) or not await asyncio.to_thread(generation_budget_available,job,reservation):
        return images,analyzed,artifacts
    try:refs=await asyncio.to_thread(_case_image_refs,job,case_id)
    except Exception:
        logger.warning('CASE photo references unavailable; no unscoped media fallback')
        return images,analyzed,artifacts
    from tenant_media_evidence import reusable
    deadline=time.monotonic()+TURN_SECONDS;size=0
    for selected in refs[:MAX_IMAGES]:
        if deadline-time.monotonic()<=0:break
        ref=selected['ref'];message_id=selected['message_id'];receipt=None
        try:receipt=await asyncio.to_thread(reusable,{'tenant_id':job['tenant_id']},ref)
        except Exception:pass
        if receipt is not None:
            cached=_cached_case_observation(receipt,selected['case_id'])
            if cached:
                analyzed.add(message_id)
                artifacts.append({**selected,'state':'cached_observation','cached_observation':cached,
                                  # A legacy receipt has text, not its original
                                  # image bytes. It may be shown as an
                                  # unclassified note, but a new model turn must
                                  # not invent a fresh visual observation from it.
                                  'analysis_expected':False,
                                  'content_sha256':cached.get('content_sha256','')})
            else:
                # A terminal receipt without usable visuals is not retried
                # automatically. An explicit later retry can be budgeted.
                artifacts.append({**selected,'state':'receipt_without_visual','analysis_expected':False})
            continue
        try:
            item=await asyncio.to_thread(resolve,job,reservation,message_id)
            async def bounded_download():
                async with _download_slots:return await download_media(item,max_bytes=MAX_TOTAL_BYTES-size)
            data,mime=await asyncio.wait_for(bounded_download(),timeout=max(.01,deadline-time.monotonic()))
            if (mime not in IMAGE_TYPES or not data or size+len(data)>MAX_TOTAL_BYTES
                    or not signature(data,mime)):
                artifacts.append({**selected,'state':'unavailable','analysis_expected':False})
                continue
            if not await asyncio.to_thread(live,job,reservation):raise ImageInactive('image_parent_inactive')
            if not await asyncio.to_thread(generation_budget_available,job,reservation):break
            size+=len(data);analyzed.add(message_id)
            digest=hashlib.sha256(data).hexdigest()
            images.append({'message_id':message_id,'content':data,'content_type':mime,'detail':'low'})
            artifacts.append({**selected,'state':'image_attached','analysis_expected':True,'content_sha256':digest})
        except ImageInactive:raise
        except Exception:
            logger.warning('CASE-linked photo unavailable or outside permitted scope')
            artifacts.append({**selected,'state':'unavailable','analysis_expected':False})
    return images,analyzed,artifacts

def persist_case_observations(job,case_id,artifacts,observations,model):
    """Persist validated visual notes and return only IDs durably saved for this CASE/version."""
    try:case_id=str(uuid.UUID(str(case_id)))
    except (ValueError,TypeError,AttributeError):return []
    from tenant_media_evidence import create_schema,media_version,source_hash,_body_hash,_safe_text
    from tenant_platform import _json
    selected={str(item.get('message_id')):item for item in artifacts or []
              if isinstance(item,dict) and item.get('analysis_expected') is True}
    pending=[]
    for observation in observations or []:
        if not isinstance(observation,dict):return []
        source=selected.get(str(observation.get('message_id') or ''))
        relevance=observation.get('relevance');text=str(observation.get('observation') or '').strip()
        if not source or relevance not in {'relevant','irrelevant','unclear'} or not text or len(text)>500:
            return []
        pending.append((source,relevance,_safe_text(text)[:500]))
    if not pending:return []
    tenant=str(job['tenant_id']);channel=str(job['channel_id']);conversation=str(job['conversation_id'])
    persisted=[]
    try:
        with _connect() as conn:
            create_schema(conn)
            with conn.cursor() as cur:
                for source,relevance,text in pending:
                    external=source['ref']['external_id']
                    cur.execute('''SELECT m.external_id,m.body,m.raw,m.message_type
                      FROM saas_support_tickets t JOIN saas_connect_conversations c
                        ON c.tenant_id=t.tenant_id AND c.connect_channel_id=t.channel_id AND c.conversation_id=t.conversation_id
                      JOIN saas_support_ticket_messages cm ON cm.tenant_id=t.tenant_id AND cm.ticket_id=t.id
                      JOIN saas_crm_messages m ON m.tenant_id=cm.tenant_id AND m.id=cm.message_id
                      WHERE t.tenant_id=%s::uuid AND t.id=%s::uuid AND t.channel_id=%s::uuid AND t.conversation_id=%s
                        AND c.lead_id=%s AND m.external_id=%s AND m.channel='connect:'||t.channel_id::text
                        AND m.direction='incoming' AND m.message_type='image' FOR UPDATE OF t,cm,m''',
                      (tenant,case_id,channel,conversation,int(job['lead_id']),external))
                    current=cur.fetchone()
                    if not current:continue
                    current_ref={'external_id':current['external_id'],'body_hash':_body_hash(current.get('body')),
                      'media_kind':'image','media_version':media_version(current.get('raw'))}
                    version=source_hash(current_ref)
                    if version!=source['source_hash']:continue
                    cur.execute('''SELECT result,provenance FROM saas_media_evidence_receipts
                      WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND external_id=%s AND source_hash=%s FOR UPDATE''',
                      (tenant,channel,external,version))
                    prior=cur.fetchone() or {};result=prior.get('result') if isinstance(prior.get('result'),dict) else {}
                    provenance=prior.get('provenance') if isinstance(prior.get('provenance'),dict) else {}
                    case_items=[item for item in result.get('case_observations',[]) if isinstance(item,dict)
                      and item.get('case_id')!=case_id] if isinstance(result.get('case_observations'),list) else []
                    note={'case_id':case_id,'message_id':source['message_id'],'source_hash':version,
                          'relevance':relevance,'text':text,'untrusted':True}
                    if source.get('content_sha256'):note['content_sha256']=source['content_sha256']
                    case_items.append(note);result['case_observations']=case_items[-8:]
                    case_sources=[item for item in provenance.get('case_followups',[]) if isinstance(item,dict)
                      and item.get('case_id')!=case_id] if isinstance(provenance.get('case_followups'),list) else []
                    source_note={'case_id':case_id,'message_id':source['message_id'],'source_hash':version,
                      'model':str(model or '')[:100],'untrusted':True}
                    if source.get('content_sha256'):source_note['content_sha256']=source['content_sha256']
                    case_sources.append(source_note);provenance['case_followups']=case_sources[-8:]
                    provenance.setdefault('processor','pilot_case_followup_vision')
                    cur.execute('''INSERT INTO saas_media_evidence_receipts
                      (tenant_id,channel_id,external_id,source_hash,media_kind,direction,status,code,coverage,result,provenance)
                      VALUES(%s::uuid,%s,%s,%s,'image','incoming','processed','case_visual_observation',%s::jsonb,%s::jsonb,%s::jsonb)
                      ON CONFLICT(tenant_id,channel_id,external_id,source_hash) DO UPDATE SET
                        status='processed',code='case_visual_observation',coverage=EXCLUDED.coverage,
                        result=EXCLUDED.result,provenance=EXCLUDED.provenance,updated_at=now()''',
                      (tenant,channel,external,version,_json({'kind':'image','complete':True}),
                       _json(result),_json(provenance)))
                    persisted.append(source['message_id'])
            conn.commit()
        return persisted
    except Exception:
        return []
