"""Finite manual batches; no polling worker, scheduled scan, sends or publication."""
import uuid
from tenant_platform import _connect,_json
from tenant_linear_policy import TenantLinearError,identifier
from tenant_ai_group_coordination import group_jid
from tenant_knowledge_candidates_policy import period,language
import tenant_knowledge_candidates_store as store
from tenant_knowledge_candidates_service import analyze

MAX_FRAGMENTS=4
MAX_DIALOGS=4


def start(profile,data):
    allowed={'request_id','provider','channel_id','conversation_id','period_start','period_end','history_mode','analysis_agent_id','before','language','allow_external_media','allow_paid_asr'}
    if not isinstance(data,dict) or set(data)-allowed: raise TenantLinearError('Некорректный пакет.')
    if data.get('provider','connect')!='connect': raise TenantLinearError('Поддерживается только WhatsApp.')
    request_id=identifier(data.get('request_id'));channel=identifier(data.get('channel_id'));start,end=period(data,True)
    conversation=data.get('conversation_id')
    if conversation is not None and (not isinstance(conversation,str) or not conversation or len(conversation)>180): raise TenantLinearError('Некорректный диалог.')
    if data.get('before') and not conversation: raise TenantLinearError('Курсор требует конкретного диалога.')
    mode=data.get('history_mode','channel')
    if mode not in {'channel','unified_group'}: raise TenantLinearError('Некорректный режим истории.')
    params={'language':language(data.get('language'),profile),'channel_id':channel,'period_start':start,'period_end':end,'history_mode':mode}
    for flag in ('allow_external_media','allow_paid_asr'):
        if flag in data:
            if type(data[flag]) is not bool: raise TenantLinearError('Некорректное разрешение медиа.')
            params[flag]=data[flag]
    if params.get('allow_paid_asr') and not params.get('allow_external_media'): raise TenantLinearError('Требуется разрешение передачи медиа.')
    selected_language=params['language']
    if 'language' not in data and selected_language=='ru': params.pop('language')
    if conversation: params['conversation_id']=conversation
    if data.get('before'): params['before']=identifier(data['before'])
    if data.get('analysis_agent_id'): params['analysis_agent_id']=identifier(data['analysis_agent_id'])
    key=store.digest(params)
    with _connect() as conn:
        store.schema(conn)
        with conn.cursor() as cur:
            tenant,actor,policy=store.authorize(cur,profile)
            cur.execute('SELECT 1 FROM saas_connect_channels WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid',(tenant,channel))
            if not cur.fetchone(): raise TenantLinearError('Канал недоступен.',404)
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('knowledge-batches:'+tenant,))
            cur.execute('SELECT id,request_key FROM saas_knowledge_batches WHERE tenant_id=%s::uuid AND (id=%s::uuid OR request_key=%s) ORDER BY id=%s::uuid DESC LIMIT 1',(tenant,request_id,key,request_id))
            old=cur.fetchone()
            if old:
                if str(old['id'])==request_id and old['request_key']!=key: raise TenantLinearError('request_id уже используется для другого пакета.',409)
                return {'id':str(old['id']),'created':False}
            cur.execute("UPDATE saas_knowledge_batches SET status='interrupted',completed_at=now() WHERE tenant_id=%s::uuid AND status='running' AND deadline<now() AND (active_until IS NULL OR active_until<now())",(tenant,))
            cur.execute("SELECT 1 FROM saas_knowledge_batches WHERE tenant_id=%s::uuid AND status='running' LIMIT 1",(tenant,))
            if cur.fetchone(): raise TenantLinearError('Другой пакет этой компании ещё выполняется.',429)
            if conversation:
                store.assert_route(cur,tenant,policy,'connect',conversation,channel)
                conversations=[conversation];truncated=False
            else:
                cur.execute("""SELECT anchor.conversation_id,max(COALESCE(m.happened_at,m.created_at)) AS recent
                  FROM saas_connect_conversations anchor JOIN saas_connect_conversations c
                  ON c.tenant_id=anchor.tenant_id AND c.conversation_id=anchor.conversation_id
                  AND (c.connect_channel_id=anchor.connect_channel_id OR (%s AND anchor.conversation_id ~ '^[0-9]{5,22}(-[0-9]{5,22})?@g[.]us$'))
                  JOIN saas_crm_messages m ON m.tenant_id=c.tenant_id
                  AND m.kommo_lead_id=c.lead_id AND m.channel='connect:'||c.connect_channel_id::text
                  WHERE anchor.tenant_id=%s::uuid AND anchor.connect_channel_id=%s::uuid
                  AND COALESCE(m.happened_at,m.created_at)>=%s::timestamptz AND COALESCE(m.happened_at,m.created_at)<%s::timestamptz
                  GROUP BY anchor.conversation_id ORDER BY recent DESC,anchor.conversation_id LIMIT 5""",(mode=='unified_group',tenant,channel,start,end))
                found=cur.fetchall();conversations=[r['conversation_id'] for r in found[:MAX_DIALOGS]];truncated=len(found)>MAX_DIALOGS
            routes=[]
            for value in conversations:
                scoped={**params,'language':selected_language,'provider':'connect','conversation_id':value,'history_mode':'unified_group' if mode=='unified_group' and group_jid(value) else 'channel'}
                store.route(cur,profile,scoped);routes.append(scoped)
            cur.execute("INSERT INTO saas_knowledge_batches(tenant_id,id,created_by,params,request_key,status,routes,routes_truncated) VALUES(%s::uuid,%s::uuid,%s,%s::jsonb,%s,%s,%s::jsonb,%s)",
                (tenant,request_id,actor,_json(params),key,'running' if routes else 'completed',_json(routes),truncated))
            store._audit(cur,tenant,actor,'knowledge_batch_requested','knowledge_batch',request_id)
        conn.commit()
    return {'id':request_id,'created':bool(routes)}


def get(profile,batch_id):
    with _connect() as conn:
        store.schema(conn)
        with conn.cursor() as cur:
            tenant,_,_=store.authorize(cur,profile)
            cur.execute('SELECT *,deadline<now() AND (active_until IS NULL OR active_until<now()) AS expired FROM saas_knowledge_batches WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,identifier(batch_id)))
            row=cur.fetchone()
            if not row: raise TenantLinearError('Пакет не найден.',404)
            for route in row['routes']: store.route(cur,profile,route)
            cur.execute('SELECT ordinal,route,extraction_id,status,error_code,message_count FROM saas_knowledge_batch_items WHERE tenant_id=%s::uuid AND batch_id=%s::uuid ORDER BY ordinal',(tenant,row['id']))
            items=cur.fetchall()
            for item in items:
                if item['extraction_id']: item['extraction_id']=str(item['extraction_id'])
            cur.execute("""SELECT
              (SELECT count(DISTINCT e.id) FROM saas_knowledge_batch_items bi
               JOIN saas_knowledge_extractions e ON e.tenant_id=bi.tenant_id AND e.id=bi.extraction_id
               WHERE bi.tenant_id=%s::uuid AND bi.batch_id=%s::uuid AND e.created_at<%s) AS reused_results,
              (SELECT count(*) FROM saas_knowledge_candidates c
               WHERE c.tenant_id=%s::uuid AND c.extraction_id IN
                 (SELECT DISTINCT extraction_id FROM saas_knowledge_batch_items WHERE tenant_id=%s::uuid AND batch_id=%s::uuid AND extraction_id IS NOT NULL)
                 AND c.created_at>=%s) AS new_candidates,
              (SELECT count(*) FROM (SELECT DISTINCT extraction_id FROM saas_knowledge_batch_items
                WHERE tenant_id=%s::uuid AND batch_id=%s::uuid AND extraction_id IS NOT NULL) runs
               JOIN saas_ai_agent_token_usage u ON u.tenant_id=%s::uuid AND u.run_id=runs.extraction_id) AS usage_runs,
              (SELECT sum(u.input_tokens) FROM (SELECT DISTINCT extraction_id FROM saas_knowledge_batch_items
                WHERE tenant_id=%s::uuid AND batch_id=%s::uuid AND extraction_id IS NOT NULL) runs
               JOIN saas_ai_agent_token_usage u ON u.tenant_id=%s::uuid AND u.run_id=runs.extraction_id) AS input_tokens,
              (SELECT sum(u.output_tokens) FROM (SELECT DISTINCT extraction_id FROM saas_knowledge_batch_items
                WHERE tenant_id=%s::uuid AND batch_id=%s::uuid AND extraction_id IS NOT NULL) runs
               JOIN saas_ai_agent_token_usage u ON u.tenant_id=%s::uuid AND u.run_id=runs.extraction_id) AS output_tokens,
              (SELECT sum(u.total_tokens) FROM (SELECT DISTINCT extraction_id FROM saas_knowledge_batch_items
                WHERE tenant_id=%s::uuid AND batch_id=%s::uuid AND extraction_id IS NOT NULL) runs
               JOIN saas_ai_agent_token_usage u ON u.tenant_id=%s::uuid AND u.run_id=runs.extraction_id) AS total_tokens""",
              (tenant,row['id'],row['created_at'],tenant,tenant,row['id'],row['created_at'],
               tenant,row['id'],tenant,tenant,row['id'],tenant,tenant,row['id'],tenant,tenant,row['id'],tenant))
            usage=cur.fetchone()
    processed_routes={(str((item.get('route') or {}).get('channel_id') or ''),str((item.get('route') or {}).get('conversation_id') or '')) for item in items if isinstance(item.get('route'),dict)}
    failed_items=sum(1 for item in items if item.get('error_code'))
    usage_available=bool(usage and usage.get('usage_runs'))
    summary={'dialog_count':len(processed_routes),'failed_items':failed_items,
        'new_candidates':int((usage or {}).get('new_candidates') or 0),
        'reused_results':int((usage or {}).get('reused_results') or 0),
        'input_tokens':int(usage['input_tokens'] or 0) if usage_available else None,
        'output_tokens':int(usage['output_tokens'] or 0) if usage_available else None,
        'total_tokens':int(usage['total_tokens'] or 0) if usage_available else None,
        'duplicates_skipped':None,'unprocessed_messages':None}
    return {'batch':{'id':str(row['id']),'status':'interrupted' if row['status']=='running' and row['expired'] else row['status'],
        'params':row['params'],'items':items,'continuations':row['continuations'],'routes_truncated':row['routes_truncated'],
        'message_count':sum(i['message_count'] for i in items),'error_code':'batch_interrupted' if row['status']=='running' and row['expired'] else '','limits':{'max_dialogs':4,'max_fragments':2 if row['params'].get('allow_paid_asr') else 4,'max_messages':160 if row['params'].get('allow_paid_asr') else 320,'max_reserved_tokens':48000 if row['params'].get('allow_paid_asr') else 64000},
        'candidates_query':{'batch_id':str(row['id'])},'summary':summary}}


def process(profile,batch_id):
    """Called once by explicit POST; one process-local finite task, never auto-replayed."""
    state=get(profile,batch_id)['batch']
    if state['status']!='running': return
    with _connect() as conn:
        with conn.cursor() as cur:
            tenant,_,_=store.authorize(cur,profile)
            cur.execute('SELECT routes FROM saas_knowledge_batches WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,batch_id))
            queue=list(cur.fetchone()['routes'])
    errors=False;success=False
    fragment_limit=2 if state['params'].get('allow_paid_asr') else MAX_FRAGMENTS
    for ordinal in range(fragment_limit):
        if not queue: break
        route=queue.pop(0)
        payload={k:v for k,v in route.items() if k not in {'provider'}}
        payload['request_id']=str(uuid.uuid5(uuid.UUID(batch_id),store.digest(route)))
        extraction=None;count=0;code='';status='failed'
        try:
            # Recheck lifecycle, route, batch deadline before every paid fragment.
            live=get(profile,batch_id)['batch']
            if live['status']!='running': break
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE saas_knowledge_batches SET active_until=now()+interval '270 seconds' WHERE tenant_id=%s::uuid AND id=%s::uuid AND status='running' AND deadline>now() AND (active_until IS NULL OR active_until<now()) RETURNING id",(tenant,batch_id))
                    admitted=cur.fetchone()
                conn.commit()
            if not admitted:return
            result=analyze(profile,payload);request=result['request']
            extraction=request['id'];count=request['message_count'];status=request['status']
            success=success or status=='completed'
            if status!='completed': errors=True;code=request['error_code'] or 'analysis_unavailable'
            elif request.get('next_before'):
                cursor=request['next_before']
                if cursor==route.get('before'): code='cursor_no_progress';errors=True
                else: queue.append({**route,'before':cursor})
        except TenantLinearError as error:
            if error.status in {401,403,404}: code='access_denied'
            elif error.status==429: code='limit_exceeded'
            elif error.status==409 and 'нет доступного текста' in str(error):
                status='skipped';code='no_text'
                if getattr(error,'next_before',None): queue.append({**route,'before':error.next_before})
            else: code='analysis_unavailable'
            if status!='skipped': errors=True
        except Exception:
            code='analysis_unavailable';errors=True
        with _connect() as conn:
            with conn.cursor() as cur:
                # Save only task metadata; raw history never enters batch tables.
                cur.execute("INSERT INTO saas_knowledge_batch_items(tenant_id,batch_id,ordinal,route,extraction_id,status,error_code,message_count) VALUES(%s::uuid,%s::uuid,%s,%s::jsonb,%s::uuid,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (tenant,batch_id,ordinal,_json(route),extraction,status,code,count))
                cur.execute('UPDATE saas_knowledge_batches SET continuations=%s::jsonb,active_until=NULL WHERE tenant_id=%s::uuid AND id=%s::uuid',(_json(queue),tenant,batch_id))
            conn.commit()
        if code in {'access_denied','limit_exceeded'}: break
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE saas_knowledge_batches SET status=%s,continuations=%s::jsonb,completed_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND status='running'",
                ('partial' if queue or state['routes_truncated'] or (errors and success) else ('failed' if errors else 'completed'),_json(queue),tenant,batch_id))
        conn.commit()
