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
from pathlib import Path
from tenant_platform import _json
from tenant_linear_policy import TenantLinearError, identifier
from tenant_ai_agents_store import _authorize, _audit

MAX_BYTES = 100 * 1024 * 1024
MAX_SECONDS = 120

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
    if not body or len(body)>MAX_BYTES: raise ValueError('video_size_limit')
    # Decode metadata locally; do not trust a filename, MIME or client duration.
    with tempfile.TemporaryDirectory(prefix='instruction-video-') as folder:
        source=Path(folder)/'video.mp4';source.write_bytes(body)
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
      'duration_seconds':row['duration_seconds'],'active':row['active'],
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
        c.execute('SELECT id,version,sha256,duration_seconds,active,reusable_approved,metadata FROM saas_instruction_videos WHERE tenant_id=%s::uuid AND lesson_id=%s::uuid ORDER BY created_at,id',(tenant,lesson))
        return {'items':[safe(r) for r in c.fetchall()],'production_send_enabled':False}
    if data.get('expected_version')!=row['version']:raise TenantLinearError('Instruction version changed',409)
    if action=='video_upload':
        # Native file upload only. No WhatsApp import or URL download authority.
        if data.get('source_kind')!='owner_upload':raise TenantLinearError('Explicit reviewed import is required for CRM media',403)
        if (data.get('metadata') or {}).get('source_ref'):raise TenantLinearError('CRM imports are not enabled',403)
        duration=validate_file(body);meta=metadata(data['metadata']);key=str(uuid.uuid4())
        c.execute('''INSERT INTO saas_instruction_videos(tenant_id,lesson_id,id,body,sha256,duration_seconds,metadata)
          VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s::jsonb) RETURNING *''',
          (tenant,lesson,key,body,hashlib.sha256(body).hexdigest(),duration,_json(meta)))
    else:
        key=identifier(data['asset_id'])
        c.execute('SELECT * FROM saas_instruction_videos WHERE tenant_id=%s::uuid AND lesson_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,lesson,key))
        asset=c.fetchone()
        if not asset:raise TenantLinearError('Video unavailable',404)
        if data.get('asset_version')!=asset['version']:raise TenantLinearError('Video version changed',409)
        if action=='video_approve':
            _owner(c,tenant,actor)
            if data.get('client_safe_reviewed') is not True or data.get('sha256')!=asset['sha256'] or not asset['active']:
                raise TenantLinearError('Review the exact video and visible personal data first',409)
            c.execute('''UPDATE saas_instruction_videos SET reusable_approved=TRUE,approved_by=%s,version=version+1
              WHERE tenant_id=%s::uuid AND id=%s::uuid RETURNING *''',(actor,tenant,key))
        elif action=='video_revoke':
            c.execute('''UPDATE saas_instruction_videos SET active=FALSE,reusable_approved=FALSE,approved_by=NULL,version=version+1
              WHERE tenant_id=%s::uuid AND id=%s::uuid RETURNING *''',(tenant,key))
        elif action=='video_metadata':
            meta=metadata(data['metadata'])
            if meta.get('source_ref')!=asset['metadata'].get('source_ref'):raise ValueError('source_ref_immutable')
            c.execute('''UPDATE saas_instruction_videos SET metadata=%s::jsonb,reusable_approved=FALSE,approved_by=NULL,version=version+1
              WHERE tenant_id=%s::uuid AND id=%s::uuid RETURNING *''',(_json(meta),tenant,key))
        else:raise ValueError('video_action')
    result=safe(c.fetchone())
    _audit(c,tenant,actor,action,'instruction',lesson,{'asset_id':key,'version':result['version'],'sha256':result['sha256']})
    return result

def candidates(c,tenant,agent,query,product_version=None,product=None):
    """Exact task vocabulary, bounded lexical matching; never guesses a version."""
    tenant=identifier(tenant);agent=identifier(agent)
    c.execute("SELECT to_regclass('saas_instruction_videos') IS NOT NULL AS available")
    if not c.fetchone()['available']:return {'status':'not_found','items':[]}
    c.execute('''SELECT DISTINCT v.created_at,v.id,v.lesson_id,v.version,v.sha256,v.duration_seconds,v.active,v.reusable_approved,v.metadata,l.doc->>'title' AS title FROM saas_instruction_videos v
      JOIN saas_instruction_lessons l ON l.tenant_id=v.tenant_id AND l.id=v.lesson_id
      JOIN saas_ai_knowledge_sources s ON s.tenant_id=l.tenant_id AND s.provenance->>'instruction_id'=l.id::text
      JOIN saas_ai_agents a ON a.tenant_id=s.tenant_id AND a.id=s.agent_id
      WHERE v.tenant_id=%s::uuid AND s.agent_id=%s::uuid AND a.archived_at IS NULL
        AND s.active=TRUE AND s.provenance->>'review_status'='approved' AND l.state='published' AND v.active=TRUE AND v.reusable_approved=TRUE
        AND v.approved_by IS NOT NULL AND octet_length(v.body)>0
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
    if offer['status']=='superseded':raise TenantLinearError('A newer offer replaced this offer',409)
    if offer['status']=='unknown':raise TenantLinearError('Unknown delivery outcome; do not retry',409)
    if data.get('simulated_client_yes') is not True:raise TenantLinearError('Specific client confirmation required',409)
    c.execute('''SELECT v.*,l.version AS lesson_version,l.state FROM saas_instruction_videos v
      JOIN saas_instruction_lessons l ON l.tenant_id=v.tenant_id AND l.id=v.lesson_id
      WHERE v.tenant_id=%s::uuid AND v.id=%s::uuid AND v.lesson_id=%s::uuid FOR SHARE OF v,l''',(tenant,offer['asset_id'],lesson))
    asset=c.fetchone()
    if (not asset or not offer['fresh'] or not asset['active'] or not asset['reusable_approved'] or
        asset['version']!=offer['asset_version'] or asset['sha256']!=offer['asset_hash'] or
        hashlib.sha256(bytes(asset['body'])).hexdigest()!=offer['asset_hash'] or
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
