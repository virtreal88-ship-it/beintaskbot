"""Campaign control and atomic fragment reservations over existing extraction receipts.

Preparing a campaign NEVER authorizes paid analysis. Explicit versioned approval
binds selected sources and bounded call/token reservations before the worker runs.
"""
import uuid
from datetime import datetime, timezone
from tenant_platform import _connect, _json
from tenant_linear_policy import TenantLinearError, identifier
from tenant_knowledge_candidates_policy import MAX_TOKENS, period
import tenant_knowledge_candidates_store as store
from tenant_knowledge_campaign_schema import create_schema
from tenant_knowledge_campaign_policy import FIELDS, validate, reservation, fits


def prepare(profile, data):
    allowed={'request_id','channel_ids','analysis_agent_id','max_calls','max_tokens','case_id'} | FIELDS
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
    policy=validate(data,calls)
    with _connect() as conn:
        store.schema(conn);create_schema(conn)
        with conn.cursor() as cur:
            tenant,actor,_=store.authorize(cur,profile)
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('knowledge-campaign:'+tenant,))
            case_plan=None
            if data.get('case_id'):
                from tenant_case_media_admission import plan, routes
                case_plan=plan(cur,profile,data['case_id'])
                if case_plan['status']!='pending_consent':
                    raise TenantLinearError(case_plan['reason'],409)
                if channels!=[case_plan['channel_id']] or not case_plan['media']:
                    raise TenantLinearError('Case source or attachments unavailable.',409)
                eligible_count=len(list(routes(case_plan,agent)))
                if not eligible_count:
                    raise TenantLinearError('No unprocessed eligible attachments; inspect saved case media status.',409)
            cur.execute('SELECT * FROM saas_knowledge_campaigns WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,key))
            old=cur.fetchone()
            if old:
                old_case=(old.get('progress') or {}).get('case_id')
                if old['media_policy']!=policy or old['sources']!=channels or str(old['analysis_agent_id'])!=agent or old['max_calls']!=calls or old['max_tokens']!=tokens or old_case!=(case_plan or {}).get('case_id') or (case_plan and old['progress'].get('plan_hash')!=case_plan['plan_hash']):
                    raise TenantLinearError('request_id уже используется.',409)
                return key
            for channel in channels:
                cur.execute('SELECT 1 FROM saas_connect_channels WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid',(tenant,channel))
                if not cur.fetchone(): raise TenantLinearError('Источник недоступен.',404)
            cur.execute('SELECT 1 FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,agent))
            if not cur.fetchone(): raise TenantLinearError('Агент недоступен.',404)
            cur.execute("SELECT 1 FROM saas_knowledge_campaigns WHERE tenant_id=%s::uuid AND status<>'stopped'",(tenant,))
            if cur.fetchone(): raise TenantLinearError('Процесс уже подготовлен.',409)
            progress={'mode':'case_media','case_id':case_plan['case_id'],
              'plan_hash':case_plan['plan_hash'],'case_version':case_plan['case_version'],
              'attachment_count':len(case_plan['media']), 'eligible_count':eligible_count,
              'unavailable_count':sum(not item['complete'] for item in case_plan['media'])-eligible_count} if case_plan else {}
            cur.execute('''INSERT INTO saas_knowledge_campaigns
              (tenant_id,id,created_by,sources,analysis_agent_id,max_calls,max_tokens,media_policy,progress)
              VALUES(%s::uuid,%s::uuid,%s,%s::jsonb,%s::uuid,%s,%s,%s::jsonb,%s::jsonb)''',
              (tenant,key,actor,_json(channels),agent,calls,tokens,_json(policy),_json(progress)))
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
            if action=='resume':
                cur.execute('''UPDATE saas_knowledge_campaign_fragments f SET status='pending',error_code=''
                  WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid AND status='blocked' AND error_code='limit_exceeded'
                  AND NOT EXISTS(SELECT 1 FROM saas_knowledge_extractions e WHERE e.tenant_id=f.tenant_id AND e.id=f.id)''',(tenant,campaign_id))
                if (row.get('progress') or {}).get('mode') not in {'case_media','single_message'}:
                    cur.execute('''SELECT f.* FROM saas_knowledge_campaign_fragments f JOIN saas_knowledge_extractions e
                  ON e.tenant_id=f.tenant_id AND e.id=f.extraction_id WHERE f.tenant_id=%s::uuid AND f.campaign_id=%s::uuid
                  AND f.status IN ('partial','failed') AND NOT f.result ? 'retry_fragment_id' AND e.status IN ('completed','failed')
                  AND (e.status='failed' OR EXISTS(SELECT 1 FROM jsonb_array_elements(e.media_results) m WHERE m->>'status'<>'processed' OR m->>'audio_status'='unavailable_or_error'))
                  ORDER BY f.created_at,f.id LIMIT 4''',(tenant,campaign_id))
                    for failed in cur.fetchall():
                        retry=enqueue(cur,profile,row,{**failed['route'],'retry_of':str(failed['extraction_id'])})
                        cur.execute("UPDATE saas_knowledge_campaign_fragments SET result=result||%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",(_json({'retry_fragment_id':retry}),tenant,failed['id']))
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
    route={**route,'approved_channel_ids':sorted(campaign['sources']),
      **{k:campaign['media_policy'].get(k,False) for k in ('allow_external_media','allow_paid_asr')}}
    route['allow_paid_asr']=bool(route['allow_paid_asr'] and campaign['media_policy'].get('max_asr_calls',0)>0)
    route['allow_vision']=bool(campaign['media_policy'].get('max_vision_calls',0) and route['allow_external_media'])
    key=store.digest(route);fragment=str(uuid.uuid5(uuid.UUID(str(campaign['id'])),key))
    cur.execute('''INSERT INTO saas_knowledge_campaign_fragments(tenant_id,campaign_id,id,source_key,route)
      VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s::jsonb) ON CONFLICT DO NOTHING''',
      (tenant,campaign['id'],fragment,key,_json(route)))
    return fragment


def _unknown_conflicts(cur, tenant, fragment, campaign):
    """Keep an uncertain call fenced unless both sources are provably disjoint.

    The spend ledger retains unknown reservations. This check only removes the
    tenant-wide scheduling hold for an independently identified attachment.
    """
    route=fragment['route']
    cur.execute('''SELECT f.route,e.source_refs FROM saas_knowledge_campaign_fragments f
      LEFT JOIN saas_knowledge_extractions e ON e.tenant_id=f.tenant_id
        AND e.id=COALESCE(f.extraction_id,f.id)
      WHERE f.tenant_id=%s::uuid AND f.status='unknown' ''',(tenant,))
    unknown=cur.fetchall()
    if not unknown:
        return False
    ids=route.get('message_ids')
    if ((campaign.get('progress') or {}).get('mode') not in {'single_message','case_media'}
        or not isinstance(ids,list) or len(ids)!=1 or not route.get('source_version')):
        return True
    for old in unknown:
        old_route=old['route'] or {}
        old_ids=old_route.get('message_ids')
        if isinstance(old_ids,list) and old_ids:
            if old_route.get('channel_id')!=route.get('channel_id'):
                continue
            if ids[0] in old_ids:
                return True  # Same attachment, including a changed media version.
            continue
        refs=old['source_refs']
        if not isinstance(refs,list) or not refs:
            return True  # A legacy/unresolved source cannot prove disjointness.
        for ref in refs:
            if not isinstance(ref,dict) or not ref.get('id') or not ref.get('channel_id'):
                return True
            if str(ref['id'])==str(ids[0]) and str(ref['channel_id'])==str(route.get('channel_id')):
                return True
    return False


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
            cur.execute("SELECT 1 FROM saas_knowledge_campaign_fragments WHERE tenant_id=%s::uuid AND status='running' LIMIT 1",(tenant,))
            if cur.fetchone():
                conn.commit();return None
            cur.execute("SELECT 1 FROM saas_knowledge_quality_checks q JOIN saas_ai_test_turns t ON t.tenant_id=q.tenant_id AND t.id=q.id WHERE q.tenant_id=%s::uuid AND t.state IN ('generating','unknown') LIMIT 1",(tenant,))
            if cur.fetchone():
                conn.commit();return None
            cur.execute("SELECT * FROM saas_knowledge_campaign_fragments WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid AND status='pending' ORDER BY created_at,id LIMIT 1 FOR UPDATE",(tenant,campaign_id))
            fragment=cur.fetchone()
            if not fragment: return None
            if _unknown_conflicts(cur,tenant,fragment,campaign):
                conn.commit();return None
            store.route(cur,profile,fragment['route'])
            amount=reservation(campaign['media_policy'])
            if not fits(campaign['media_policy'],campaign['reserved_kinds'],amount) or campaign['reserved_calls']+amount['calls']>campaign['max_calls'] or campaign['reserved_tokens']+amount['tokens']>campaign['max_tokens']:
                cur.execute("UPDATE saas_knowledge_campaigns SET status='budget_exhausted',version=version+1 WHERE tenant_id=%s::uuid AND id=%s::uuid",(tenant,campaign_id))
                conn.commit();return None
            token=str(uuid.uuid4())
            cur.execute('UPDATE saas_knowledge_campaigns SET reserved_calls=reserved_calls+%s,reserved_tokens=reserved_tokens+%s,reserved_kinds=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid',(amount['calls'],amount['tokens'],_json({k:campaign['reserved_kinds'].get(k,0)+amount[k] for k in ('main','asr','vision')}),tenant,campaign_id))
            cur.execute("UPDATE saas_knowledge_campaign_fragments SET status='running',reservation=%s::jsonb,lease_token=%s::uuid,lease_until=now()+interval '360 seconds',updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",(_json(amount),token,tenant,fragment['id']))
        conn.commit()
    return {**fragment,'reservation':amount,'lease_token':token,'profile':profile}


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
            cur.execute("SELECT count(*) AS n FROM saas_knowledge_campaign_sources WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid AND status='pending'",(tenant,row['id']))
            sources_pending=cur.fetchone()['n']
            progress=dict(row['progress'])
            if progress.get('mode')=='case_media':
                cur.execute("""SELECT count(*) AS n FROM saas_knowledge_campaign_fragments
                  WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid AND status='completed'
                    AND extraction_id IS NOT NULL AND error_code=''""",(tenant,row['id']))
                succeeded=int(cur.fetchone()['n'])
                eligible=int(progress.get('eligible_count') or 0)
                if row['status']=='stopped' and (counts.get('pending') or counts.get('running')):
                    progress['phase']='cancelled'
                elif (progress.get('unavailable_count') or any(counts.get(state) for state in ('unknown','blocked','failed','partial'))
                      or row['status']=='stopped' and (eligible==0 or succeeded!=eligible)):
                    progress['phase']='incomplete'
                elif row['status']=='stopped':progress['phase']='completed'
                else:progress['phase']='pending_consent' if not row['approved_at'] else 'processing'
            elif progress and not sources_pending and not counts.get('pending') and not counts.get('running'):
                progress['phase']='waiting_completion'
            cur.execute('''SELECT count(DISTINCT c.id) AS cards FROM saas_knowledge_candidates c
              JOIN saas_knowledge_campaign_fragments f ON f.tenant_id=c.tenant_id AND f.extraction_id=c.extraction_id
              WHERE f.tenant_id=%s::uuid AND f.campaign_id=%s::uuid''',(tenant,row['id']))
            cards=cur.fetchone()['cards']
            cur.execute('''SELECT COALESCE(sum(u.total_tokens),0) AS tokens FROM saas_ai_agent_token_usage u
              WHERE u.tenant_id=%s::uuid AND u.run_id IN (SELECT extraction_id FROM saas_knowledge_campaign_fragments
              WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid)''',(tenant,tenant,row['id']))
            tokens=cur.fetchone()['tokens']
            cur.execute('''SELECT count(DISTINCT r->>'id') AS messages FROM saas_knowledge_extractions e,
              LATERAL jsonb_array_elements(e.source_refs) r WHERE e.tenant_id=%s::uuid AND e.status='completed'
              AND e.id IN (SELECT extraction_id FROM saas_knowledge_campaign_fragments WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid)''',(tenant,tenant,row['id']))
            messages=cur.fetchone()['messages']
            cur.execute('''SELECT count(*) FILTER(WHERE m->>'status'='processed') AS processed,
              count(*) FILTER(WHERE m->>'status'<>'processed') AS unavailable FROM
              (SELECT DISTINCT ON (m->>'channel_id',m->>'id') m FROM saas_knowledge_extractions e,
              LATERAL jsonb_array_elements(e.media_results) m WHERE e.tenant_id=%s::uuid
              AND e.id IN (SELECT extraction_id FROM saas_knowledge_campaign_fragments WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid)
              ORDER BY m->>'channel_id',m->>'id',e.created_at DESC) evidence''',(tenant,tenant,row['id']))
            media=cur.fetchone()
            cur.execute('''SELECT c.status,count(DISTINCT c.id) AS n FROM saas_knowledge_candidates c
              WHERE c.tenant_id=%s::uuid AND c.extraction_id IN (SELECT extraction_id FROM saas_knowledge_campaign_fragments
              WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid) GROUP BY c.status''',(tenant,tenant,row['id']))
            reviews={r['status']:r['n'] for r in cur.fetchall()}
            from tenant_knowledge_quality import schema as quality_schema
            quality_schema(cur)
            cur.execute('''SELECT retrieval_state,response_state,count(*) AS n FROM saas_knowledge_quality_checks
              WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid GROUP BY retrieval_state,response_state''',(tenant,row['id']))
            quality=cur.fetchall()
            cur.execute('''SELECT COALESCE(sum(total_tokens),0) AS tokens FROM saas_ai_test_turns
              WHERE tenant_id=%s::uuid AND id IN (SELECT id FROM saas_knowledge_quality_checks
              WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid)''',(tenant,tenant,row['id']))
            tokens=int(tokens)+int(cur.fetchone()['tokens'])


    return {'campaign':{'id':str(row['id']),'version':row['version'],'status':row['status'],
      'channel_ids':row['sources'],'fragments':counts,'cards':cards,
      'reserved_calls':row['reserved_calls'],'reserved_tokens':row['reserved_tokens'],
      'media_policy':row['media_policy'],'reserved_kinds':row['reserved_kinds'],
      'used_tokens':int(tokens),'cost':None,'max_calls':row['max_calls'],'max_tokens':row['max_tokens'],
      'progress':progress,'media_processed':media['processed'],'media_unavailable':media['unavailable'],'processed_messages':messages,'reviews':reviews,'quality_checks':quality}}


def complete(claimed, response):
    """Fence completion by lease; partial media never becomes completed history."""
    request=response['request'];tenant=str(claimed['tenant_id'])
    media=request.get('media_results')
    missing=media is None or any(m.get('status')!='processed' or m.get('complete') is False or m.get('kind')=='video' or m.get('audio_status')=='unavailable_or_error' for m in media)
    state=request['status']
    if state=='completed' and missing:state='partial'
    if state not in {'completed','partial','failed','unknown'}:state='unknown'
    code=request.get('error_code') or ('media_incomplete' if missing else '')
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute('''UPDATE saas_knowledge_campaign_fragments SET status=%s,extraction_id=%s::uuid,
              result=%s::jsonb,error_code=%s,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid
              AND status IN ('running','unknown') AND lease_token=%s::uuid RETURNING id''',
              (state,request['id'],_json({'message_count':request.get('message_count'),'media_results':media}),
               code,tenant,claimed['id'],claimed['lease_token']))
            if not cur.fetchone():return False
            cur.execute('SELECT progress FROM saas_knowledge_campaigns WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,claimed['campaign_id']))
            campaign=cur.fetchone()
            mode=(campaign or {}).get('progress',{}).get('mode')
            single_message=mode=='single_message'
            if single_message:
                # One exact owner consent must never grow into discovery,
                # follow-up pages or quality work.  Terminal result remains
                # stored on the fragment; the campaign itself is closed.
                cur.execute("UPDATE saas_knowledge_campaigns SET status='stopped',version=version+1,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",(tenant,claimed['campaign_id']))
            elif mode=='case_media':
                cur.execute("SELECT 1 FROM saas_knowledge_campaign_fragments WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid AND status IN ('pending','running','unknown') LIMIT 1",(tenant,claimed['campaign_id']))
                if not cur.fetchone():
                    cur.execute("UPDATE saas_knowledge_campaigns SET status='stopped',version=version+1,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid",(tenant,claimed['campaign_id']))
            # Completed text can advance while unavailable attachments remain a
            # durable partial fragment for an explicit, budgeted later retry.
            if mode not in {'single_message','case_media'} and request.get('next_before') and state in {'completed','partial'}:
                cur.execute('SELECT * FROM saas_knowledge_campaigns WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,claimed['campaign_id']))
                campaign=cur.fetchone()
                route={**claimed['route'],'before':identifier(request['next_before'])}
                if route['before']==claimed['route'].get('before'):raise TenantLinearError('Курсор не изменился.',409)
                enqueue(cur,claimed['profile'],campaign,route)
        conn.commit()
    return True


def approve(profile,campaign_id,data):
    """Explicit source/budget confirmation, never called by deployment/startup."""
    required={'expected_version','channel_ids','max_calls','max_tokens','confirm_external_processing','media_policy'}
    if not isinstance(data,dict) or set(data) not in (required,required|{'case_id','case_plan_hash'}) or data['confirm_external_processing'] is not True:
        raise TenantLinearError('Подтвердите источники и бюджет внешней обработки.')
    with _connect() as conn:
        with conn.cursor() as cur:
            tenant,actor,_=store.authorize(cur,profile)
            cur.execute('SELECT * FROM saas_knowledge_campaigns WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,identifier(campaign_id)))
            row=cur.fetchone()
            if not row:raise TenantLinearError('Процесс не найден.',404)
            if row['status'] not in {'awaiting_budget','budget_exhausted'} or row['version']!=data['expected_version']:
                raise TenantLinearError('Обновите план перед подтверждением.',409)
            if not isinstance(data['channel_ids'],list) or row['sources']!=sorted(set(data['channel_ids'])):
                raise TenantLinearError('Источники изменились.',409)
            calls,tokens=data['max_calls'],data['max_tokens']
            if type(calls) is not int or not 2<=calls<=10000 or type(tokens) is not int or not 24000<=tokens<=160000000:
                raise TenantLinearError('Некорректный бюджет.')
            if not isinstance(data['media_policy'],dict) or set(data['media_policy'])!=FIELDS:raise TenantLinearError('Некорректные разрешения.')
            new_policy=validate(data['media_policy'],calls)
            if row['status']=='awaiting_budget' and new_policy!=row['media_policy']:raise TenantLinearError('Разрешения изменились.',409)
            if row['status']=='budget_exhausted':
                for flag in ('allow_external_media','allow_paid_asr'):
                    if new_policy[flag]!=row['media_policy'].get(flag,False):raise TenantLinearError('Подготовьте новую кампанию для изменения разрешений.',409)
                for kind in ('main','asr','vision'):
                    if new_policy['max_'+kind+'_calls']<row['media_policy'].get('max_'+kind+'_calls',0):raise TenantLinearError('Лимит нельзя уменьшить.',409)
            if row['status']=='awaiting_budget' and (row['max_calls']!=calls or row['max_tokens']!=tokens):
                raise TenantLinearError('Бюджет изменился.',409)
            if row['status']=='budget_exhausted' and (calls<row['max_calls'] or tokens<row['max_tokens'] or (calls==row['max_calls'] and tokens==row['max_tokens'] and new_policy==row['media_policy'])):
                raise TenantLinearError('Новый общий бюджет должен быть больше прежнего.',409)

            amount=reservation(new_policy)
            if not fits(new_policy,row['reserved_kinds'],amount) or calls<amount['calls'] or tokens<amount['tokens']:raise TenantLinearError('Бюджет не покрывает фрагмент.')
            case_mode=(row.get('progress') or {}).get('mode')=='case_media'
            case_plan=None
            if case_mode:
                if (set(data)!=required|{'case_id','case_plan_hash'}
                    or data['case_id']!=row['progress']['case_id']
                    or data['case_plan_hash']!=row['progress']['plan_hash']):
                    raise TenantLinearError('Confirm this exact case and attachment plan.',409)
                from tenant_case_media_admission import plan, routes
                case_plan=plan(cur,profile,row['progress']['case_id'])
                if case_plan['status']!='pending_consent' or case_plan['plan_hash']!=row['progress']['plan_hash']:
                    raise TenantLinearError('Case boundary or attachment changed; prepare a new plan.',409)
                pending=list(routes(case_plan,row['analysis_agent_id']))
                if not pending:
                    raise TenantLinearError('Case media already complete.',409)
                if not new_policy['allow_external_media']:
                    raise TenantLinearError('External media consent required.',409)
                pending_ids={route['message_ids'][0] for route in pending}
                visual=any(item['kind'] in {'image','video','document'} for item in case_plan['media'] if item['id'] in pending_ids)
                audio=any(item['kind'] in {'audio','voice','video'} for item in case_plan['media'] if item['id'] in pending_ids)
                if (new_policy['max_main_calls']<len(pending)
                    or visual and not amount['vision']
                    or audio and not amount['asr']
                    or any(new_policy['max_'+kind+'_calls']<len(pending)*amount[kind]
                           for kind in ('main','asr','vision'))
                    or calls<len(pending)*amount['calls'] or tokens<len(pending)*amount['tokens']):
                    raise TenantLinearError('Budget does not cover all case attachments.',409)
            else:
                if set(data)!=required:raise TenantLinearError('Некорректные параметры.',400)
                from tenant_knowledge_campaign_discovery import seed
                if row['status']=='awaiting_budget':seed(cur,profile,row)
            cur.execute("UPDATE saas_knowledge_campaigns SET status='running',max_calls=%s,max_tokens=%s,media_policy=%s::jsonb,approved_by=%s,approved_at=now(),version=version+1 WHERE tenant_id=%s::uuid AND id=%s::uuid",(calls,tokens,_json(new_policy),actor,tenant,campaign_id))
            if case_plan:
                approved={**row,'media_policy':new_policy}
                for fragment_route in pending:enqueue(cur,profile,approved,fragment_route)
            from tenant_knowledge_quality import schema as quality_schema
            quality_schema(cur)
            cur.execute('''UPDATE saas_knowledge_quality_checks q SET state='pending' WHERE tenant_id=%s::uuid
              AND campaign_id=%s::uuid AND state='blocked' AND NOT EXISTS(SELECT 1 FROM saas_ai_test_turns t WHERE t.tenant_id=q.tenant_id AND t.id=q.id)''',(tenant,campaign_id))
            store._audit(cur,tenant,actor,'knowledge_campaign_budget_approved','knowledge_campaign',campaign_id)
        conn.commit()
    return get(profile,campaign_id)

def preview(profile,channels):
    if not isinstance(channels,list) or not 1<=len(channels)<=100:raise TenantLinearError('Выберите источники.')
    channels=sorted(set(identifier(c) for c in channels))
    with _connect() as conn:
        with conn.cursor() as cur:
            tenant,_,_=store.authorize(cur,profile)
            cur.execute('SELECT connect_channel_id,name FROM saas_connect_channels WHERE tenant_id=%s::uuid AND connect_channel_id=ANY(%s::uuid[])',(tenant,channels))
            sources=[{'channel_id':str(r['connect_channel_id']),'name':r['name']} for r in cur.fetchall()]
            if len(sources)!=len(channels):raise TenantLinearError('Источник недоступен.',404)
            cur.execute('''SELECT m.message_type,count(*) AS copies,min(COALESCE(m.happened_at,m.created_at)) AS earliest,
              max(COALESCE(m.happened_at,m.created_at)) AS latest FROM saas_crm_messages m
              JOIN saas_connect_conversations c ON c.tenant_id=m.tenant_id AND c.lead_id=m.kommo_lead_id
              AND m.channel='connect:'||c.connect_channel_id::text WHERE c.tenant_id=%s::uuid
              AND c.connect_channel_id=ANY(%s::uuid[]) GROUP BY m.message_type''',(tenant,channels))
            types=[{'type':r['message_type'],'stored_copies':r['copies'],'earliest':str(r['earliest']),'latest':str(r['latest'])} for r in cur.fetchall()]
    return {'sources':sources,'message_types':types,'counts_include_channel_copies':True,
      'media_availability':'not_probed','currency_estimate':None,'calls_per_fragment_max':2,'reserved_tokens_per_fragment':24000,
      'messages_per_fragment_max':80,'warning':'Actual availability, unique group events and fragment counts require bounded discovery; no external media has been accessed.'}
