"""Private reusable library videos and a simulation-only offer protocol.

No provider import, public URL or outbound transport is available in this phase.
PostgreSQL BYTEA follows the existing instruction image storage contract.
"""
import hashlib
import json
import re
import subprocess
import tempfile
import uuid
import io
import shutil
import threading
from pathlib import Path
from tenant_platform import _json
from tenant_linear_policy import TenantLinearError, identifier
from tenant_ai_agents_store import _authorize, _audit

MAX_BYTES = 100 * 1024 * 1024
MAX_SECONDS = 120
# Operational storage bound, independent of knowledge/publication quotas.
TENANT_STORAGE_BYTES = 500 * 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
UPLOAD_SLOT = threading.BoundedSemaphore(1)
ASSET_COLUMNS = 'id,version,sha256,duration_seconds,active,reusable_approved,metadata,size_bytes'


def schema(conn):
    with conn.cursor() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS saas_instruction_videos(
          tenant_id UUID NOT NULL, lesson_id UUID NOT NULL, id UUID NOT NULL,
          version INT NOT NULL DEFAULT 1, body BYTEA NOT NULL, sha256 TEXT NOT NULL,
          duration_seconds DOUBLE PRECISION NOT NULL, metadata JSONB NOT NULL,
          reusable_approved BOOLEAN NOT NULL DEFAULT FALSE, active BOOLEAN NOT NULL DEFAULT TRUE,
          approved_by BIGINT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,id),
          FOREIGN KEY(tenant_id,lesson_id) REFERENCES saas_instruction_lessons(tenant_id,id),
          CHECK(octet_length(body)>0 AND octet_length(body)<=104857600),
          CHECK(duration_seconds>0 AND duration_seconds<=120))''')
        c.execute('ALTER TABLE saas_instruction_videos ADD COLUMN IF NOT EXISTS size_bytes BIGINT')
        c.execute("SELECT attstorage FROM pg_attribute WHERE attrelid='saas_instruction_videos'::regclass AND attname='body'")
        if c.fetchone()['attstorage']!='e':c.execute('ALTER TABLE saas_instruction_videos ALTER COLUMN body SET STORAGE EXTERNAL')
        c.execute('UPDATE saas_instruction_videos SET size_bytes=octet_length(body) WHERE size_bytes IS NULL')
        c.execute('ALTER TABLE saas_instruction_videos ALTER COLUMN size_bytes SET NOT NULL')
        c.execute("SELECT 1 FROM pg_constraint WHERE conrelid='saas_instruction_videos'::regclass AND conname='instruction_video_size'")
        if not c.fetchone():c.execute('ALTER TABLE saas_instruction_videos ADD CONSTRAINT instruction_video_size CHECK(size_bytes=octet_length(body))')
        c.execute("""CREATE OR REPLACE FUNCTION instruction_video_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN
            IF NEW.body IS DISTINCT FROM OLD.body OR NEW.sha256 IS DISTINCT FROM OLD.sha256
               OR NEW.size_bytes IS DISTINCT FROM OLD.size_bytes THEN
              RAISE EXCEPTION 'Video content is immutable';
            END IF;
            RETURN NEW;
          END $$""")
        c.execute("SELECT 1 FROM pg_trigger WHERE tgrelid='saas_instruction_videos'::regclass AND tgname='instruction_video_immutable'")
        if not c.fetchone():
            c.execute('CREATE TRIGGER instruction_video_immutable BEFORE UPDATE OF body,sha256,size_bytes ON saas_instruction_videos FOR EACH ROW EXECUTE FUNCTION instruction_video_immutable()')
        c.execute('''CREATE TABLE IF NOT EXISTS saas_instruction_video_offers(
          tenant_id UUID NOT NULL, id UUID NOT NULL, actor BIGINT NOT NULL,
          agent_id UUID NOT NULL, dialog_id UUID NOT NULL, asset_id UUID NOT NULL,
          asset_version INT NOT NULL, asset_hash TEXT NOT NULL, lesson_version INT NOT NULL,
          expires_at TIMESTAMPTZ NOT NULL DEFAULT now()+interval '10 minutes',
          status TEXT NOT NULL DEFAULT 'offered', request_hash TEXT NOT NULL,
          PRIMARY KEY(tenant_id,id),
          FOREIGN KEY(tenant_id,asset_id) REFERENCES saas_instruction_videos(tenant_id,id),
          CHECK(status IN ('offered','virtual_sent','unknown','superseded')))''')

def validate_file(body):
    stream=io.BytesIO(body) if isinstance(body,bytes) else body
    stream.seek(0,2);size=stream.tell();stream.seek(0)
    if not 0<size<=MAX_BYTES: raise ValueError('video_size_limit')
    # Decode metadata locally; do not trust a filename, MIME or client duration.
    with tempfile.TemporaryDirectory(prefix='instruction-video-') as folder:
        source=Path(folder)/'video.mp4'
        with source.open('wb') as output:shutil.copyfileobj(stream,output,DOWNLOAD_CHUNK_BYTES)
        stream.seek(0)
        try:
            probe=subprocess.run(['ffprobe','-v','error','-show_entries',
          'format=format_name,duration:stream=codec_type','-of','json',str(source)],
              capture_output=True,timeout=15,check=True)
        except (subprocess.CalledProcessError,subprocess.TimeoutExpired,OSError):
            raise ValueError('Video metadata could not be verified') from None
    result=json.loads(probe.stdout)
    duration=float(result['format']['duration'])
    if ('mp4' not in result['format']['format_name'].split(',') or
        not any(s.get('codec_type')=='video' for s in result.get('streams',[])) or
        not 0<duration<=MAX_SECONDS): raise ValueError('video_format_or_duration')
    return duration

def metadata(value):
    if not isinstance(value,dict) or set(value)-{'task','product','product_version','when_offer','source_ref'}:
        raise ValueError('video_metadata')
    clean={}
    for key in ('task','product','product_version','when_offer'):
        text=str(value.get(key) or '').strip()
        if not text or len(text)>240 or re.search(r'https?://|@',text):raise ValueError('video_metadata')
        clean[key]=text
    if value.get('source_ref'):clean['source_ref']=identifier(value['source_ref'])
    return clean

def safe(row):
    return {'id':str(row['id']),'version':row['version'],'sha256':row['sha256'],
      'size_bytes':row['size_bytes'],'duration_seconds':row['duration_seconds'],'active':row['active'],
      'reusable_approved':row['reusable_approved'],
      'metadata':{k:v for k,v in row['metadata'].items() if k!='source_ref'}}

def _owner(c,tenant,actor):
    c.execute("SELECT role FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s AND active=TRUE FOR SHARE",(tenant,actor))
    if (c.fetchone() or {}).get('role')!='owner':raise TenantLinearError('Owner review required',403)

def manage(c,profile,lesson_id,action,data,body=None):
    tenant,actor=_authorize(c,profile);lesson=identifier(lesson_id)
    c.execute('SELECT version FROM saas_instruction_lessons WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,lesson))
    row=c.fetchone()
    if not row:raise TenantLinearError('Instruction unavailable',404)
    if action=='video_list':
        c.execute('SELECT '+ASSET_COLUMNS+' FROM saas_instruction_videos WHERE tenant_id=%s::uuid AND lesson_id=%s::uuid ORDER BY created_at,id',(tenant,lesson))
        items=[safe(r) for r in c.fetchall()]
        c.execute('SELECT COALESCE(sum(size_bytes),0) AS used FROM saas_instruction_videos WHERE tenant_id=%s::uuid',(tenant,))
        return {'items':items,'production_send_enabled':False,'storage_used_bytes':int(c.fetchone()['used']),
          'storage_limit_bytes':TENANT_STORAGE_BYTES}
    if data.get('expected_version')!=row['version']:raise TenantLinearError('Instruction version changed',409)
    if action=='video_upload':
        # Native file upload only. No WhatsApp import or URL download authority.
        if data.get('source_kind')!='owner_upload':raise TenantLinearError('Explicit reviewed import is required for CRM media',403)
        if (data.get('metadata') or {}).get('source_ref'):raise TenantLinearError('CRM imports are not enabled',403)
        if not UPLOAD_SLOT.acquire(blocking=False):raise TenantLinearError('Another video upload is in progress; retry later',429)
        try:
            stream=io.BytesIO(body) if isinstance(body,bytes) else body
            duration=validate_file(stream);meta=metadata(data['metadata']);key=str(uuid.uuid4())
            digest=hashlib.sha256();size=0;stream.seek(0)
            while chunk:=stream.read(DOWNLOAD_CHUNK_BYTES):digest.update(chunk);size+=len(chunk)
            sha=digest.hexdigest()
            # Across all lessons and workers in this tenant, including inactive files.
            c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('instruction-video-storage:'+tenant,))
            c.execute('SELECT '+ASSET_COLUMNS+' FROM saas_instruction_videos WHERE tenant_id=%s::uuid AND lesson_id=%s::uuid AND sha256=%s ORDER BY created_at LIMIT 1',(tenant,lesson,sha))
            existing=c.fetchone()
            if existing:
                if existing['metadata']!=meta:raise TenantLinearError('This file is already attached; edit its metadata instead',409)
                return safe(existing)
            c.execute('SELECT COALESCE(sum(size_bytes),0) AS used FROM saas_instruction_videos WHERE tenant_id=%s::uuid',(tenant,))
            if int(c.fetchone()['used'])+size>TENANT_STORAGE_BYTES:
                raise TenantLinearError('Private video storage is full (500 MiB per company, including revoked files); no files were removed',413)
            stream.seek(0)
            c.execute('INSERT INTO saas_instruction_videos(tenant_id,lesson_id,id,body,sha256,duration_seconds,metadata,size_bytes) VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s::jsonb,%s) RETURNING '+ASSET_COLUMNS,
              (tenant,lesson,key,stream.read(MAX_BYTES+1),sha,duration,_json(meta),size))
        finally:UPLOAD_SLOT.release()

    else:
        key=identifier(data['asset_id'])
        c.execute('SELECT '+ASSET_COLUMNS+' FROM saas_instruction_videos WHERE tenant_id=%s::uuid AND lesson_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,lesson,key))
        asset=c.fetchone()
        if not asset:raise TenantLinearError('Video unavailable',404)
        if data.get('asset_version')!=asset['version']:raise TenantLinearError('Video version changed',409)
        if action=='video_approve':
            _owner(c,tenant,actor)
            if data.get('client_safe_reviewed') is not True or data.get('sha256')!=asset['sha256'] or not asset['active']:
                raise TenantLinearError('Review the exact video and visible personal data first',409)
            c.execute('''UPDATE saas_instruction_videos SET reusable_approved=TRUE,approved_by=%s,version=version+1
              WHERE tenant_id=%s::uuid AND id=%s::uuid RETURNING '''+ASSET_COLUMNS,(actor,tenant,key))
        elif action=='video_revoke':
            c.execute('''UPDATE saas_instruction_videos SET active=FALSE,reusable_approved=FALSE,approved_by=NULL,version=version+1
              WHERE tenant_id=%s::uuid AND id=%s::uuid RETURNING '''+ASSET_COLUMNS,(tenant,key))
        elif action=='video_metadata':
            meta=metadata(data['metadata'])
            if meta.get('source_ref')!=asset['metadata'].get('source_ref'):raise ValueError('source_ref_immutable')
            c.execute('''UPDATE saas_instruction_videos SET metadata=%s::jsonb,reusable_approved=FALSE,approved_by=NULL,version=version+1
              WHERE tenant_id=%s::uuid AND id=%s::uuid RETURNING '''+ASSET_COLUMNS,(_json(meta),tenant,key))
        else:raise ValueError('video_action')
    result=safe(c.fetchone())
    _audit(c,tenant,actor,action,'instruction',lesson,{'asset_id':key,'version':result['version'],'sha256':result['sha256']})
    return result

def candidates(c,tenant,agent,query,product_version=None,product=None):
    """Exact task vocabulary, bounded lexical matching; never guesses a version."""
    tenant=identifier(tenant);agent=identifier(agent)
    c.execute("SELECT to_regclass('saas_instruction_videos') IS NOT NULL AS available")
    if not c.fetchone()['available']:return {'status':'not_found','items':[]}
    c.execute('''SELECT DISTINCT v.created_at,v.id,v.lesson_id,v.version,v.sha256,v.size_bytes,v.duration_seconds,v.active,v.reusable_approved,v.metadata,l.doc->>'title' AS title FROM saas_instruction_videos v
      JOIN saas_instruction_lessons l ON l.tenant_id=v.tenant_id AND l.id=v.lesson_id
      JOIN saas_ai_knowledge_sources s ON s.tenant_id=l.tenant_id AND s.provenance->>'instruction_id'=l.id::text
      JOIN saas_ai_agents a ON a.tenant_id=s.tenant_id AND a.id=s.agent_id
      WHERE v.tenant_id=%s::uuid AND s.agent_id=%s::uuid AND a.archived_at IS NULL
        AND s.active=TRUE AND s.provenance->>'review_status'='approved' AND l.state='published' AND v.active=TRUE AND v.reusable_approved=TRUE
        AND v.approved_by IS NOT NULL AND v.size_bytes>0
        AND l.published_doc->>'review_status'='approved'
        AND to_tsvector('simple',%s) @@ plainto_tsquery('simple',v.metadata->>'task')
        AND (%s='' OR lower(v.metadata->>'product')=lower(%s))
        AND (%s='' OR v.metadata->>'product_version'=%s)
      ORDER BY v.created_at,v.id LIMIT 9''',(tenant,agent,str(query)[:1000],product or '',product or '',product_version or '',product_version or ''))
    rows=c.fetchall()
    terms=set(re.findall(r'[^\W_]+',str(query).casefold()))
    items=[];seen=set()
    for row in rows:
        task=set(re.findall(r'[^\W_]+',row['metadata']['task'].casefold()))
        if not task or not task<=terms or row['id'] in seen:continue
        if product and row['metadata']['product'].casefold()!=str(product).casefold():continue
        if product_version and row['metadata']['product_version']!=product_version:continue
        seen.add(row['id']);items.append({**safe(row),'title':row['title'],
          'match_reason':'Exact task terms: '+row['metadata']['task'],
          'lesson_id':str(row['lesson_id'])})
    # No arbitrary truncation to one when more than one instruction matches.
    return {'status':'clarify' if len(rows)>=9 or len(items)>1 or items and (not product_version or not product) else 'selected' if items else 'not_found',
      'items':items[:8],'production_send_enabled':False,
      'instruction':'Simulation only. Do not promise video delivery to real customers.'}

def virtual_action(c,profile,lesson_id,action,data):
    """Authenticated owner sandbox only; client_confirmation is explicitly simulated.

    Never accepts a real customer event or calls a provider. Future live delivery
    must use existing Connect stop/pilot/manual fences, not this sandbox receipt.
    """
    tenant,actor=_authorize(c,profile);_owner(c,tenant,actor)
    lesson=identifier(lesson_id);agent=identifier(data['agent_id'])
    if action=='video_find':return candidates(c,tenant,agent,data['query'],data.get('product_version'),data.get('product'))
    dialog=identifier(data['dialog_id'])
    key=identifier(data['offer_id'])
    # Serialize concurrent duplicate requests in the same ledger transaction.
    c.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',(tenant+agent+dialog,))
    c.execute('SELECT *,expires_at>now() AS fresh FROM saas_instruction_video_offers WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,key))
    offer=c.fetchone()
    if offer and (str(offer['dialog_id'])!=dialog or str(offer['agent_id'])!=agent or offer['actor']!=actor):
        raise TenantLinearError('Offer belongs to a different sandbox dialog',403)
    if action=='video_offer':
        digest=hashlib.sha256(_json({k:data[k] for k in ('agent_id','dialog_id','asset_id','asset_version','query','product','product_version','text_answer')}).encode()).hexdigest()
        if offer:
            if digest!=offer['request_hash']:raise TenantLinearError('Offer id already used',409)
            return {'offer_id':key,'status':offer['status'],'simulation':True,'customer_sent':False}
        if not str(data['text_answer']).strip():raise ValueError('Answer the question before offering a video')
        found=candidates(c,tenant,agent,data['query'],data['product_version'],data['product'])
        if found['status']!='selected':raise TenantLinearError('Clarify task and product version first',409)
        item=found['items'][0]
        if item['id']!=data['asset_id'] or item['version']!=data['asset_version'] or item['lesson_id']!=lesson:
            raise TenantLinearError('Selected video changed',409)
        c.execute('SELECT version FROM saas_instruction_lessons WHERE tenant_id=%s::uuid AND id=%s::uuid FOR SHARE',(tenant,lesson))
        version=c.fetchone()['version']
        c.execute("UPDATE saas_instruction_video_offers SET status='superseded' WHERE tenant_id=%s::uuid AND actor=%s AND agent_id=%s::uuid AND dialog_id=%s::uuid AND status='offered'",(tenant,actor,agent,dialog))
        c.execute('''INSERT INTO saas_instruction_video_offers(tenant_id,id,actor,agent_id,dialog_id,asset_id,
          asset_version,asset_hash,lesson_version,request_hash) VALUES(%s::uuid,%s::uuid,%s,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s)''',
          (tenant,key,actor,agent,dialog,item['id'],item['version'],item['sha256'],version,digest))
        _audit(c,tenant,actor,'instruction_video_virtual_offer','instruction',lesson,{'offer_id':key})
        return {'offer_id':key,'status':'offered','simulation':True,'customer_sent':False,
          'offer_text':'Would you like the video: '+item['title']+' ('+item['metadata']['task']+')?'}
    if action!='video_confirm' or not offer:raise TenantLinearError('No matching offer',409)
    if offer['status']=='virtual_sent':
        c.execute('SELECT lesson_id FROM saas_instruction_videos WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,offer['asset_id']))
        if str((c.fetchone() or {}).get('lesson_id'))!=lesson:raise TenantLinearError('Offer belongs to another instruction',403)
        return {'offer_id':key,'asset_id':str(offer['asset_id']),'asset_version':offer['asset_version'],
          'sha256':offer['asset_hash'],'status':'virtual_sent','simulation':True,'customer_sent':False}
    if offer['status']=='superseded':raise TenantLinearError('A newer offer replaced this offer',409)
    if offer['status']=='unknown':raise TenantLinearError('Unknown delivery outcome; do not retry',409)
    if data.get('simulated_client_yes') is not True:raise TenantLinearError('Specific client confirmation required',409)
    c.execute('''SELECT v.id,v.version,v.sha256,v.active,v.reusable_approved,l.version AS lesson_version,l.state FROM saas_instruction_videos v
      JOIN saas_instruction_lessons l ON l.tenant_id=v.tenant_id AND l.id=v.lesson_id
      WHERE v.tenant_id=%s::uuid AND v.id=%s::uuid AND v.lesson_id=%s::uuid FOR SHARE OF v,l''',(tenant,offer['asset_id'],lesson))
    asset=c.fetchone()
    if (not asset or not offer['fresh'] or not asset['active'] or not asset['reusable_approved'] or
        asset['version']!=offer['asset_version'] or asset['sha256']!=offer['asset_hash'] or
        asset['lesson_version']!=offer['lesson_version'] or asset['state']!='published'):
        raise TenantLinearError('Offer expired or video changed/revoked',409)
    # Revalidate current agent/source authority at the virtual delivery boundary.
    c.execute('''SELECT 1 FROM saas_ai_knowledge_sources s JOIN saas_ai_agents a ON a.tenant_id=s.tenant_id AND a.id=s.agent_id
      WHERE s.tenant_id=%s::uuid AND s.agent_id=%s::uuid AND a.archived_at IS NULL AND s.active=TRUE
      AND s.provenance->>'instruction_id'=%s FOR SHARE OF s,a''',(tenant,agent,lesson))
    if not c.fetchone():raise TenantLinearError('Instruction no longer available to agent',409)
    if offer['status']!='virtual_sent':
        c.execute("UPDATE saas_instruction_video_offers SET status='virtual_sent' WHERE tenant_id=%s::uuid AND id=%s::uuid",(tenant,key))
        _audit(c,tenant,actor,'instruction_video_virtual_send','instruction',lesson,{'offer_id':key,'asset_id':str(asset['id'])})
    return {'offer_id':key,'asset_id':str(asset['id']),'asset_version':asset['version'],
      'sha256':asset['sha256'],'status':'virtual_sent','simulation':True,'customer_sent':False}
