"""Campaign control and atomic fragment reservations over existing extraction receipts.

Preparing a campaign NEVER authorizes paid analysis. Approval is deliberately not
exposed yet: media completeness and source discovery must be integrated first.
"""
import uuid
from datetime import datetime, timezone
from tenant_platform import _connect, _json
from tenant_linear_policy import TenantLinearError, identifier
from tenant_knowledge_candidates_policy import MAX_TOKENS, period
import tenant_knowledge_candidates_store as store
from tenant_knowledge_campaign_schema import create_schema


def prepare(profile, data):
    allowed={'request_id','channel_ids','analysis_agent_id','max_calls','max_tokens'}
    if not isinstance(data,dict) or set(data)-allowed:
        raise TenantLinearError('Некорректные параметры.')
    key=identifier(data.get('request_id'))
    channels=data.get('channel_ids')
    if not isinstance(channels,list) or not 1<=len(channels)<=100:
        raise TenantLinearError('Выберите источники.')
    channels=sorted(set(identifier(value) for value in channels))
    agent=identifier(data.get('analysis_agent_id'))
    calls=data.get('max_calls');tokens=data.get('max_tokens')
    if type(calls) is not int or not 1<=calls<=10000 or type(tokens) is not int or not MAX_TOKENS<=tokens<=160000000:
        raise TenantLinearError('Укажите ограниченный бюджет вызовов и токенов.')
    with _connect() as conn:
        store.schema(conn);create_schema(conn)
        with conn.cursor() as cur:
            tenant,actor,_=store.authorize(cur,profile)
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('knowledge-campaign:'+tenant,))
            cur.execute('SELECT * FROM saas_knowledge_campaigns WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,key))
            old=cur.fetchone()
            if old:
                if old['sources']!=channels or str(old['analysis_agent_id'])!=agent or old['max_calls']!=calls or old['max_tokens']!=tokens:
                    raise TenantLinearError('request_id уже используется.',409)
                return key
            for channel in channels:
                cur.execute('SELECT 1 FROM saas_connect_channels WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid',(tenant,channel))
                if not cur.fetchone(): raise TenantLinearError('Источник недоступен.',404)
            cur.execute('SELECT 1 FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,agent))
            if not cur.fetchone(): raise TenantLinearError('Агент недоступен.',404)
            cur.execute("SELECT 1 FROM saas_knowledge_campaigns WHERE tenant_id=%s::uuid AND status<>'stopped'",(tenant,))
            if cur.fetchone(): raise TenantLinearError('Процесс уже подготовлен.',409)
            cur.execute('''INSERT INTO saas_knowledge_campaigns
              (tenant_id,id,created_by,sources,analysis_agent_id,max_calls,max_tokens)
              VALUES(%s::uuid,%s::uuid,%s,%s::jsonb,%s::uuid,%s,%s)''',
              (tenant,key,actor,_json(channels),agent,calls,tokens))
            store._audit(cur,tenant,actor,'knowledge_campaign_prepared','knowledge_campaign',key)
        conn.commit()
    return key


def control(profile, campaign_id, expected_version, action):
    if action not in {'pause','resume','stop'} or type(expected_version) is not int:
        raise TenantLinearError('Некорректное действие.')
    with _connect() as conn:
        with conn.cursor() as cur:
            tenant,actor,_=store.authorize(cur,profile)
            cur.execute('SELECT * FROM saas_knowledge_campaigns WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,identifier(campaign_id)))
            row=cur.fetchone()
            if not row: raise TenantLinearError('Процесс не найден.',404)
            if row['version']!=expected_version: raise TenantLinearError('Обновите состояние процесса.',409)
            if action=='resume' and (row['status']!='paused' or not row['approved_at']):
                raise TenantLinearError('Нужно отдельное подтверждение бюджета и источников.',409)
            if action=='pause' and row['status']!='running': raise TenantLinearError('Процесс не запущен.',409)
            if row['status']=='stopped': raise TenantLinearError('Процесс остановлен.',409)
            state={'pause':'paused','resume':'running','stop':'stopped'}[action]
            cur.execute('UPDATE saas_knowledge_campaigns SET status=%s,version=version+1,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid',(state,tenant,campaign_id))
            store._audit(cur,tenant,actor,'knowledge_campaign_'+action,'knowledge_campaign',campaign_id)
        conn.commit()


def enqueue(cur, profile, campaign, route):
    """Internal discovery adapter only; route always re-authorized, no browser IDs trusted."""
    tenant,_,_,_,binding,_=store.route(cur,profile,route)
    if tenant!=str(campaign['tenant_id']): raise TenantLinearError('Нет доступа.',403)
    period(route,True)
    if binding['channel_id'] not in campaign['sources']:
        raise TenantLinearError('Источник не утверждён.',403)
    # Unified history may include another company channel. Require every source
    # explicitly, rather than silently widening the approved source selection.
    if binding['history_mode']=='unified_group':
        cur.execute('SELECT connect_channel_id FROM saas_connect_conversations WHERE tenant_id=%s::uuid AND conversation_id=%s',(tenant,binding['conversation_id']))
        if any(str(r['connect_channel_id']) not in campaign['sources'] for r in cur.fetchall()):
            raise TenantLinearError('Выберите все каналы этой группы.',409)
    key=store.digest(route);fragment=str(uuid.uuid5(uuid.UUID(str(campaign['id'])),key))
    cur.execute('''INSERT INTO saas_knowledge_campaign_fragments(tenant_id,campaign_id,id,source_key,route)
      VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s::jsonb) ON CONFLICT DO NOTHING''',
      (tenant,campaign['id'],fragment,key,_json(route)))
    return fragment


def claim(tenant, campaign_id):
    """One call reservation per tenant. Unknown attempts never auto-retry/refund."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('knowledge-campaign:'+tenant,))
            cur.execute('SELECT * FROM saas_knowledge_campaigns WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,campaign_id))
            campaign=cur.fetchone()
            if not campaign or campaign['status']!='running' or not campaign['approved_at']: return None
            profile={'tenant_id':tenant,'telegram_id':campaign['created_by']}
            store.authorize(cur,profile)
            cur.execute("UPDATE saas_knowledge_campaign_fragments SET status='unknown',error_code='interrupted',updated_at=now() WHERE tenant_id=%s::uuid AND status='running' AND lease_until<now()",(tenant,))
            cur.execute("SELECT 1 FROM saas_knowledge_campaign_fragments WHERE tenant_id=%s::uuid AND status IN ('running','unknown') LIMIT 1",(tenant,))
            if cur.fetchone():
                conn.commit();return None
            cur.execute("SELECT * FROM saas_knowledge_campaign_fragments WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid AND status='pending' ORDER BY created_at,id LIMIT 1 FOR UPDATE",(tenant,campaign_id))
            fragment=cur.fetchone()
            if not fragment: return None
            store.route(cur,profile,fragment['route'])
            if campaign['reserved_calls']+1>campaign['max_calls'] or campaign['reserved_tokens']+MAX_TOKENS>campaign['max_tokens']:
                cur.execute("UPDATE saas_knowledge_campaigns SET status='budget_exhausted',version=version+1 WHERE tenant_id=%s::uuid AND id=%s::uuid",(tenant,campaign_id))
                conn.commit();return None
            token=str(uuid.uuid4())
            cur.execute('UPDATE saas_knowledge_campaigns SET reserved_calls=reserved_calls+1,reserved_tokens=reserved_tokens+%s WHERE tenant_id=%s::uuid AND id=%s::uuid',(MAX_TOKENS,tenant,campaign_id))
            cur.execute("UPDATE saas_knowledge_campaign_fragments SET status='running',lease_token=%s::uuid,lease_until=now()+interval '180 seconds',updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",(token,tenant,fragment['id']))
        conn.commit()
    return {**fragment,'lease_token':token,'profile':profile}


def get(profile, campaign_id=None):
    with _connect() as conn:
        store.schema(conn);create_schema(conn)
        with conn.cursor() as cur:
            tenant,_,_=store.authorize(cur,profile)
            cur.execute('''SELECT * FROM saas_knowledge_campaigns WHERE tenant_id=%s::uuid
              AND (%s::uuid IS NULL OR id=%s::uuid) ORDER BY created_at DESC LIMIT 1''',(tenant,campaign_id,campaign_id))
            row=cur.fetchone()
            if not row:return {'campaign':None}
            cur.execute('''SELECT status,count(*) AS n FROM saas_knowledge_campaign_fragments
              WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid GROUP BY status''',(tenant,row['id']))
            counts={r['status']:r['n'] for r in cur.fetchall()}
            cur.execute('''SELECT count(DISTINCT c.id) AS cards FROM saas_knowledge_candidates c
              JOIN saas_knowledge_campaign_fragments f ON f.tenant_id=c.tenant_id AND f.extraction_id=c.extraction_id
              WHERE f.tenant_id=%s::uuid AND f.campaign_id=%s::uuid''',(tenant,row['id']))
            cards=cur.fetchone()['cards']
            cur.execute('''SELECT COALESCE(sum(u.total_tokens),0) AS tokens FROM saas_ai_agent_token_usage u
              WHERE u.tenant_id=%s::uuid AND u.run_id IN (SELECT extraction_id FROM saas_knowledge_campaign_fragments
              WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid)''',(tenant,tenant,row['id']))
            tokens=cur.fetchone()['tokens']
    return {'campaign':{'id':str(row['id']),'version':row['version'],'status':row['status'],
      'channel_ids':row['sources'],'fragments':counts,'cards':cards,
      'reserved_calls':row['reserved_calls'],'reserved_tokens':row['reserved_tokens'],
      'used_tokens':int(tokens),'cost':None,'max_calls':row['max_calls'],'max_tokens':row['max_tokens'],
      'progress':row['progress'],'media_processed':None,'media_unavailable':None,'processed_messages':None}}


def complete(claimed, response):
    """Fence completion by lease; partial media never becomes completed history."""
    request=response['request'];tenant=str(claimed['tenant_id'])
    media=request.get('media_results')
    missing=media is None or any(m.get('status')!='processed' for m in media)
    state=request['status']
    if state=='completed' and missing:state='partial'
    if state not in {'completed','partial','failed','unknown'}:state='unknown'
    code=request.get('error_code') or ('media_incomplete' if missing else '')
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute('''UPDATE saas_knowledge_campaign_fragments SET status=%s,extraction_id=%s::uuid,
              result=%s::jsonb,error_code=%s,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid
              AND status='running' AND lease_token=%s::uuid RETURNING id''',
              (state,request['id'],_json({'message_count':request.get('message_count'),'media_results':media}),
               code,tenant,claimed['id'],claimed['lease_token']))
            if not cur.fetchone():return False
            # Completed text can advance while unavailable attachments remain a
            # durable partial fragment for an explicit, budgeted later retry.
            if request.get('next_before') and state in {'completed','partial'}:
                cur.execute('SELECT * FROM saas_knowledge_campaigns WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,claimed['campaign_id']))
                campaign=cur.fetchone()
                route={**claimed['route'],'before':identifier(request['next_before'])}
                if route['before']==claimed['route'].get('before'):raise TenantLinearError('Курсор не изменился.',409)
                enqueue(cur,claimed['profile'],campaign,route)
        conn.commit()
    return True
