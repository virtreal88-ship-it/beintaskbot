"""Instruction authoring only. Published knowledge uses existing Knowledge Sources."""
import base64,hashlib,json,re,secrets,uuid,math
from tenant_platform import _connect,_json
from tenant_ai_agents_store import _authorize,_audit,_row,save_knowledge
from tenant_ai_agents_schema import ensure_schema
from tenant_linear_policy import TenantLinearError,identifier

STATES=('draft','pending_review','approved','published')
def document(value):
    if not isinstance(value,dict) or len(_json(value).encode())>60000: raise ValueError('document_size')
    v=json.loads(_json(value))
    if v.get('schema_version')!=1 or v.get('language') not in ('ru','az','en'): raise ValueError('language')
    if not isinstance(v.get('title'),str) or not 1<=len(v['title'].strip())<=180: raise ValueError('title')
    if not isinstance(v.get('operation'),str) or not re.fullmatch(r'[a-z0-9_.-]{1,100}',v['operation']): raise ValueError('operation')
    slides=v.get('slides');ids=set()
    if not isinstance(slides,list) or not 1<=len(slides)<=32: raise ValueError('slides')
    for i,s in enumerate(slides,1):
        if s.get('order')!=i or not isinstance(s.get('id'),str) or s['id'] in ids: raise ValueError('order')
        ids.add(s['id'])
        for key in ('title','explanation','note'):
            if not isinstance(s.get(key),str) or len(s[key])>2000: raise ValueError('text')
        im=s['image'];identifier(im['asset_id'].removesuffix('.jpg'))
        if not im['asset_id'].endswith('.jpg') or not re.fullmatch('[0-9a-f]{64}',im['sha256']): raise ValueError('asset')
        if any(type(im[k]) is not int or not 1<=im[k]<=4096 for k in ('width','height')): raise ValueError('dimensions')
        areas=s['areas'];known=set()
        if not isinstance(areas,list) or len(areas)>8: raise ValueError('areas')
        for a in areas:
            nums=[a[k] for k in ('x','y','width','height')]
            if any(type(n) not in (int,float) or not math.isfinite(n) for n in nums): raise ValueError('coordinates')
            x,y,w,h=nums
            if min(x,y)<0 or min(w,h)<=0 or x+w>im['width'] or y+h>im['height'] or a['id'] in known or a['intent'] not in ('focus','warning'): raise ValueError('bounds')
            known.add(a['id'])
        p=s['playback'];d=p['duration_ms']
        if type(d) is not int or not 4000<=d<=30000 or type(p['transition_ms']) is not int or not 0<=p['transition_ms']<=1000 or type(p['return_to_overview']) is not bool: raise ValueError('duration')
        prev=-1
        if len(p['focus'])>4: raise ValueError('focus')
        for f in p['focus']:
            if f['area_id'] not in known or type(f['at_ms']) is not int or not prev<f['at_ms']<=d-2000 or type(f['padding']) is not int or not 10<=f['padding']<=200: raise ValueError('focus')
            if 'only_below_scale' in f and (type(f['only_below_scale']) not in (int,float) or not 0<f['only_below_scale']<1): raise ValueError('focus')
            prev=f['at_ms']
    cues=v.get('subtitles',{}).get('slides',[])
    if len(cues)!=len(slides): raise ValueError('subtitles')
    for i,rows in enumerate(cues):
        prev=-1
        if not rows or len(rows)>12 or rows[0].get('at_ms')!=0: raise ValueError('subtitles')
        for c in rows:
            if type(c['at_ms']) is not int or not prev<c['at_ms']<slides[i]['playback']['duration_ms'] or not isinstance(c['text'],str) or not 1<=len(c['text'])<=120: raise ValueError('subtitle')
            prev=c['at_ms']
    v['review_status']='pending_review' # authoritative lifecycle is server-owned
    return v

def transition(state,action):
    expected={'submit':'draft','approve':'pending_review','publish':'approved'}
    if action not in expected or state!=expected[action]: raise TenantLinearError('Invalid review transition',409)
    return {'submit':'pending_review','approve':'approved','publish':'published'}[action]

def schema(conn):
    ensure_schema(conn)
    with conn.cursor() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS saas_instruction_assets(
          tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,id UUID NOT NULL,body BYTEA NOT NULL,
          width INT NOT NULL,height INT NOT NULL,sha256 TEXT NOT NULL,PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,sha256))''')
        c.execute('''CREATE TABLE IF NOT EXISTS saas_instruction_lessons(
          tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,id UUID NOT NULL,version INT NOT NULL DEFAULT 1,
          state TEXT NOT NULL DEFAULT 'draft',doc JSONB NOT NULL,published_doc JSONB,
          public_hash TEXT,updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),PRIMARY KEY(tenant_id,id))''')
        c.execute('CREATE UNIQUE INDEX IF NOT EXISTS saas_instruction_share_idx ON saas_instruction_lessons(public_hash) WHERE public_hash IS NOT NULL')

def _assets(c,tenant,doc):
    for s in doc['slides']:
        im=s['image'];c.execute('SELECT width,height,sha256 FROM saas_instruction_assets WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,im['asset_id'][:-4]));a=c.fetchone()
        if not a or any(a[k]!=im[k] for k in ('width','height','sha256')): raise TenantLinearError('Image unavailable',403)

def operation(profile,action,data=None,lesson_id=None):
    data=data or {}
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as c:
            tenant,actor=_authorize(c,profile)
            if action=='list':
                q=str(data.get('q',''))[:180];state=data.get('state','');offset=min(max(int(data.get('offset',0)),0),100000)
                c.execute('''SELECT id,version,state,doc->>'title' AS title,doc->>'language' AS language,updated_at
                  FROM saas_instruction_lessons WHERE tenant_id=%s::uuid AND (%s='' OR state=%s)
                  AND doc->>'title' ILIKE %s ORDER BY updated_at DESC,id LIMIT 51 OFFSET %s''',(tenant,state,state,'%'+q+'%',offset))
                rows=[_row(r) for r in c.fetchall()];return {'items':rows[:50],'next_offset':offset+50 if len(rows)>50 else None}
            if action=='upload':
                # Pillow re-encodes pixels only: EXIF, comments and metadata are discarded.
                import io,warnings
                from PIL import Image
                raw=base64.b64decode(data['image'],validate=True)
                if len(raw)>2_000_000: raise ValueError('image_size')
                with warnings.catch_warnings():
                    warnings.simplefilter('error',Image.DecompressionBombWarning)
                    with Image.open(io.BytesIO(raw)) as image:
                        if image.format not in ('JPEG','PNG') or max(image.size)>4096 or min(image.size)<1: raise ValueError('image')
                        image.load();clean=io.BytesIO();image.convert('RGB').save(clean,format='JPEG',quality=92);body=clean.getvalue();w,h=image.size
                if len(body)>2_000_000: raise ValueError('image_size')
                digest=hashlib.sha256(body).hexdigest();asset=str(uuid.uuid4())
                c.execute('SELECT count(*) AS n FROM saas_instruction_assets WHERE tenant_id=%s::uuid',(tenant,))
                if c.fetchone()['n']>=256: raise TenantLinearError('Image limit',409)
                c.execute('''INSERT INTO saas_instruction_assets(tenant_id,id,body,width,height,sha256) VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s)
                  ON CONFLICT(tenant_id,sha256) DO UPDATE SET sha256=EXCLUDED.sha256 RETURNING id,width,height,sha256''',(tenant,asset,body,w,h,digest))
                a=dict(c.fetchone());a['asset_id']=str(a.pop('id'))+'.jpg';conn.commit();return a
            key=identifier(lesson_id or data.get('id'))
            c.execute('SELECT * FROM saas_instruction_lessons WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,key));row=c.fetchone()
            if action=='read':
                if not row: raise TenantLinearError('Not found',404)
                return _row(row)
            if action=='save':
                v=document(data['doc']);_assets(c,tenant,v)
                if row:
                    if data.get('expected_version')!=row['version']: raise TenantLinearError('Version conflict; reload before saving',409)
                    c.execute("UPDATE saas_instruction_lessons SET doc=%s::jsonb,state='draft',public_hash=NULL,version=version+1,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",(_json(v),tenant,key))
                    c.execute("UPDATE saas_ai_knowledge_sources SET active=FALSE WHERE tenant_id=%s::uuid AND provenance->>'instruction_id'=%s",(tenant,key))
                else:
                    if data.get('expected_version')!=0: raise TenantLinearError('Not found',404)
                    c.execute('SELECT count(*) AS n FROM saas_instruction_lessons WHERE tenant_id=%s::uuid',(tenant,))
                    if c.fetchone()['n']>=200: raise TenantLinearError('Lesson limit',409)
                    c.execute('INSERT INTO saas_instruction_lessons(tenant_id,id,doc) VALUES(%s::uuid,%s::uuid,%s::jsonb)',(tenant,key,_json(v)))
            else:
                if not row: raise TenantLinearError('Not found',404)
                if data.get('expected_version')!=row['version']: raise TenantLinearError('Version conflict',409)
                if action=='revoke':
                    c.execute('UPDATE saas_instruction_lessons SET public_hash=NULL,version=version+1 WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,key))
                elif action=='share':
                    if row['state']!='published': raise TenantLinearError('Publish first',409)
                    token=secrets.token_urlsafe(32);digest=hashlib.sha256(token.encode()).hexdigest()
                    c.execute('UPDATE saas_instruction_lessons SET public_hash=%s,version=version+1 WHERE tenant_id=%s::uuid AND id=%s::uuid',(digest,tenant,key))
                    _audit(c,tenant,actor,'instruction_public_share','instruction',key);conn.commit();return {'token':token}
                else:
                    state=transition(row['state'],action)
                    if action=='approve' and data.get('review_confirmed') is not True: raise TenantLinearError('Explicit employee review required',400)
                    v=document(row['doc']);_assets(c,tenant,v)
                    if action=='publish':
                        agents=data.get('agent_ids',[])
                        if not isinstance(agents,list) or not 1<=len(agents)<=8 or len(set(agents))!=len(agents): raise ValueError('agents')
                        v['review_status']='approved'
                        for agent in agents:
                            agent=identifier(agent)
                            source=save_knowledge(profile,{'title':v['title'],'kind':'text','content':_json(v),'active':True,'agent_id':agent,'setup_key':'instruction_'+key.replace('-','')+'_'+str(row['version'])},connection=conn)
                            c.execute('UPDATE saas_ai_knowledge_sources SET provenance=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid',(_json({'instruction_id':key,'review_status':'approved','approved_by':actor,'lesson':{'operation':v['operation'],'language':v['language'],'ui_version':v.get('verification',{}).get('ui_version','unreported')}}),tenant,source['id']))
                    c.execute('UPDATE saas_instruction_lessons SET state=%s,doc=%s::jsonb,published_doc=%s::jsonb,version=version+1,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid',(state,_json(v),_json(v) if state=='published' else None,tenant,key))
            _audit(c,tenant,actor,'instruction_'+action,'instruction',key)
            c.execute('SELECT * FROM saas_instruction_lessons WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,key));result=_row(c.fetchone())
        conn.commit();return result

def shared(token,asset_id=None):
    if not re.fullmatch(r'[A-Za-z0-9_-]{43}',token or ''): raise TenantLinearError('Not found',404)
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as c:
            c.execute("SELECT l.* FROM saas_instruction_lessons l JOIN saas_tenants t ON t.id=l.tenant_id WHERE l.public_hash=%s AND l.state='published' AND t.status='active'",(hashlib.sha256(token.encode()).hexdigest(),));row=c.fetchone()
            if not row: raise TenantLinearError('Not found',404)
            if not asset_id:return {'doc':row['published_doc']}
            return asset(c,str(row['tenant_id']),row['published_doc'],asset_id)

def asset(c,tenant,doc,asset_id):
    if asset_id not in {s['image']['asset_id'] for s in doc['slides']}: raise TenantLinearError('Not found',404)
    c.execute('SELECT body FROM saas_instruction_assets WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,identifier(asset_id[:-4])));r=c.fetchone()
    if not r: raise TenantLinearError('Not found',404)
    return bytes(r['body'])

def private_asset(profile,lesson_id,asset_id):
    with _connect() as conn:
        schema(conn)
        with conn.cursor() as c:
            tenant,_=_authorize(c,profile);c.execute('SELECT doc FROM saas_instruction_lessons WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,identifier(lesson_id)));r=c.fetchone()
            if not r: raise TenantLinearError('Not found',404)
            return asset(c,tenant,r['doc'],asset_id)
