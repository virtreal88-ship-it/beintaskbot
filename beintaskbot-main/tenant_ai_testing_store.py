"""Private virtual conversations. The only external operation is the existing AI provider."""
import hashlib
import json
import uuid
from tenant_platform import _connect,_fernet
from tenant_ai_agents_schema import ensure_schema
from tenant_chat_ai_schema import ensure_schema as ensure_chat_schema
from tenant_ai_agents_store import _authorize
from tenant_ai_agent_actions import matching_triggers,should_handoff
from tenant_ai_agent_crm_actions import phone_candidate
from tenant_ai_agent_schedule import is_open
from tenant_ai_agent_safety import sanitize_draft,server_instructions
from tenant_ai_agent_runtime_flags import enabled as runtime_flag
from tenant_ai_agent_key_policy import choose_api_credential,platform_service_ready,platform_model,platform_model_allowed,platform_token_limit
from tenant_ai_testing_budget import TOKEN_USAGE_SQL
from tenant_ai_spend_budget import reserve as reserve_spend, settle as settle_spend, release as release_spend, price
from tenant_chat_ai_provider import INSTRUCTIONS
from tenant_linear_policy import TenantLinearError,identifier


def public(row):
    return {k:v.isoformat() if hasattr(v,'isoformat') else str(v) if isinstance(v,uuid.UUID) else v for k,v in dict(row).items() if k not in {'snapshot','input_hash','api_key','secrets'}}

def setup(conn):ensure_schema(conn);ensure_chat_schema(conn)

def session(cur,tenant,actor,id,lock=False):
    cur.execute('SELECT * FROM saas_ai_test_sessions WHERE tenant_id=%s::uuid AND id=%s::uuid AND created_by=%s'+(' FOR UPDATE' if lock else ''),(tenant,identifier(id),actor))
    row=cur.fetchone()
    if not row:raise TenantLinearError('Виртуальная сессия недоступна.',404)
    return dict(row)

def config(cur,tenant,agent):
    cur.execute('''SELECT a.*,s.enabled AS company_enabled,s.emergency_stop,s.company_instructions,s.max_replies_per_chat,
      s.updated_at AS settings_version,i.metadata,i.secrets,i.updated_at AS config_version FROM saas_ai_agents a
      JOIN saas_ai_agent_settings s ON s.tenant_id=a.tenant_id LEFT JOIN saas_tenant_integrations i
      ON i.tenant_id=a.tenant_id AND i.provider='chat_ai' WHERE a.tenant_id=%s::uuid AND a.id=%s::uuid
      AND a.archived_at IS NULL FOR SHARE OF a,s''',(tenant,agent))
    row=cur.fetchone()
    if not row:raise TenantLinearError('Агент или настройки компании недоступны.',404)
    return dict(row)

def gate(row):
    if not runtime_flag('AI_AGENTS_ENABLED'):return 'server_ai_disabled'
    if row.get('company_enabled') is not True:return 'company_ai_disabled'
    if row.get('emergency_stop') is True:return 'emergency_stop'
    if row.get('enabled') is not True:return 'agent_disabled'
    return ''

def scope_key(scope):return tuple(str(scope.get(k) or ('kommo' if k=='provider' else '')) for k in ('provider','channel_id','pipeline_id','stage_id'))

def selected_scope(row,current):
    if not current:return {}
    match=next((s for s in row.get('scopes') or [] if scope_key(s)==scope_key(current)),None)
    if match is None:raise TenantLinearError('Область работы агента изменилась. Создайте новую тестовую сессию.',409)
    return match

def create(profile,data):
    if set(data)-{'id','agent_id','scope_index'}:raise TenantLinearError('Некорректные поля.')
    id,agent=identifier(data.get('id')),identifier(data.get('agent_id'))
    with _connect() as conn:
        setup(conn)
        with conn.cursor() as cur:
            tenant,actor=_authorize(cur,profile);row=config(cur,tenant,agent)
            scopes=row.get('scopes') or [];index=data.get('scope_index');scope={}
            if index is not None:
                if type(index) is not int or not 0<=index<len(scopes):raise TenantLinearError('Выберите область этого агента.')
                scope=scopes[index]
            cur.execute('SELECT * FROM saas_ai_test_sessions WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,id))
            existing=cur.fetchone()
            if existing:
                if existing['created_by']!=actor or str(existing['agent_id'])!=agent or scope_key(existing['scope'])!=scope_key(scope):raise TenantLinearError('Код сессии уже использован.',409)
            else:
                cur.execute('SELECT count(*) AS n FROM saas_ai_test_sessions WHERE tenant_id=%s::uuid AND created_by=%s AND created_at>now()-interval \'1 day\'',(tenant,actor))
                if int(cur.fetchone()['n'])>=100:raise TenantLinearError('Лимит тестовых сессий за день.',429)
                cur.execute('INSERT INTO saas_ai_test_sessions(tenant_id,id,agent_id,created_by,scope) VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s::jsonb)',(tenant,id,agent,actor,json.dumps(scope)))
        conn.commit()
    return read(profile,id)

def read(profile,id):
    with _connect() as conn:
        setup(conn)
        with conn.cursor() as cur:
            tenant,actor=_authorize(cur,profile);value=session(cur,tenant,actor,id)
            row=config(cur,tenant,value['agent_id']);value['agent_name']=row['name'];value['block_reason']=gate(row)
            cur.execute('SELECT * FROM saas_ai_test_turns WHERE tenant_id=%s::uuid AND session_id=%s::uuid ORDER BY created_at,id LIMIT 100',(tenant,id))
            turns=[public(r) for r in cur.fetchall()]
    return {'session':public(value),'turns':turns,'simulation':True}

def listing(profile):
    with _connect() as conn:
        setup(conn)
        with conn.cursor() as cur:
            tenant,actor=_authorize(cur,profile)
            cur.execute('SELECT s.id,s.agent_id,a.name AS agent_name,s.state,s.updated_at FROM saas_ai_test_sessions s JOIN saas_ai_agents a ON a.tenant_id=s.tenant_id AND a.id=s.agent_id WHERE s.tenant_id=%s::uuid AND s.created_by=%s ORDER BY s.updated_at DESC,s.id LIMIT 20',(tenant,actor))
            return [public(r) for r in cur.fetchall()]

def control(profile,id,data):
    if set(data)-{'action','expected_revision'} or data.get('action') not in {'pause','handoff','resume'}:raise TenantLinearError('Некорректное действие.')
    with _connect() as conn:
        setup(conn)
        with conn.cursor() as cur:
            tenant,actor=_authorize(cur,profile);value=session(cur,tenant,actor,id,True)
            if type(data.get('expected_revision')) is not int or data['expected_revision']!=value['revision']:raise TenantLinearError('Сессия изменилась. Обновите историю.',409)
            action=data['action'];state={'pause':'paused','handoff':'human','resume':'active'}[action]
            if action=='resume':
                reason=gate(config(cur,tenant,value['agent_id']))
                if reason:raise TenantLinearError(reason,409)
                if value['state'] not in {'human','paused'}:raise TenantLinearError('Сессия уже активна.',409)
            if value.get('pending_turn'):
                cur.execute("UPDATE saas_ai_test_turns SET state='superseded',updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND state='generating'",(tenant,value['pending_turn']))
            cur.execute('UPDATE saas_ai_test_sessions SET state=%s,reason=%s,revision=revision+1,pending_turn=NULL,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid',(state,'virtual_'+action,tenant,id))
        conn.commit()
    return read(profile,id)

def reserve(profile,id,data,*,campaign_id=None):
    if set(data)-{'request_id','text'} or not isinstance(data.get('text'),str) or not 1<=len(data['text'].strip())<=8000:raise TenantLinearError('Введите текст до 8000 символов.')
    request=identifier(data.get('request_id'));text=data['text'].strip();digest=hashlib.sha256((str(id)+'\n'+text).encode()).hexdigest()
    with _connect() as conn:
        setup(conn)
        with conn.cursor() as cur:
            tenant,actor=_authorize(cur,profile)
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('tenant-chat-ai:'+tenant,))
            value=session(cur,tenant,actor,id,True);row=config(cur,tenant,value['agent_id'])
            cur.execute('SELECT session_id,input_hash,state FROM saas_ai_test_turns WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,request));previous=cur.fetchone()
            if previous:
                if str(previous['session_id'])!=str(id) or previous['input_hash']!=digest:raise TenantLinearError('Код сообщения уже использован.',409)
                return {'cached':True}
            if value.get('pending_turn'):
                cur.execute("SELECT id FROM saas_ai_test_turns WHERE tenant_id=%s::uuid AND id=%s::uuid AND updated_at<now()-interval '5 minutes'",(tenant,value['pending_turn']))
                if not cur.fetchone():raise TenantLinearError('Дождитесь текущего ответа или приостановите сессию.',409)
                cur.execute("UPDATE saas_ai_test_turns SET state='unknown',updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND state='generating'",(tenant,value['pending_turn']))
                cur.execute("UPDATE saas_ai_test_sessions SET pending_turn=NULL,revision=revision+1 WHERE tenant_id=%s::uuid AND id=%s::uuid RETURNING revision",(tenant,id));value['revision']=cur.fetchone()['revision']
            cur.execute('SELECT count(*) AS n FROM saas_ai_test_turns WHERE tenant_id=%s::uuid AND session_id=%s::uuid',(tenant,id))
            turn_count=int(cur.fetchone()['n'])
            if turn_count>=100:raise TenantLinearError('История теста заполнена. Создайте новую сессию.',429)
            scope=selected_scope(row,value['scope'])
            cur.execute('SELECT id,name,trigger_type,condition,actions FROM saas_ai_agent_triggers WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND enabled=TRUE ORDER BY created_at,id',(tenant,value['agent_id']))
            triggers=matching_triggers([dict(r) for r in cur.fetchall()],text,first_inquiry=turn_count==0,phone=phone_candidate(text))
            actions=[{'trigger':t['name'],'action':a,'parameters':t.get('condition') or {},'state':'simulated','simulation':True} for t in triggers for a in t.get('actions') or []]
            trace={'simulation':True,'triggers':[{'id':str(t['id']),'name':t['name']} for t in triggers],'actions':actions,'knowledge':[], 'delay_seconds':row['delay_seconds'],'agent_mode':row['mode'],'real_actions':[]}
            reason=gate(row) or (value['state'] if value['state']!='active' else '')
            if not reason and value['reply_count']>=row['max_replies_per_chat']:reason='reply_limit'
            if not reason and not is_open(row.get('schedule') or {}):reason='schedule_closed'
            handoff=not reason and (should_handoff(text) or any('handoff' in (t.get('actions') or []) for t in triggers))
            if handoff:reason='customer_requested_manager';trace['actions'].append({'action':'handoff','state':'simulated','simulation':True})
            if reason:
                trace['reason']=reason
                cur.execute("INSERT INTO saas_ai_test_turns(tenant_id,id,session_id,input_hash,client_text,state,trace) VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s,'blocked',%s::jsonb)",(tenant,request,id,digest,text,json.dumps(trace)))
                if handoff or reason=='reply_limit':cur.execute("UPDATE saas_ai_test_sessions SET state='human',reason=%s,revision=revision+1 WHERE tenant_id=%s::uuid AND id=%s::uuid",(reason,tenant,id))
                conn.commit();return {'cached':True}
            metadata=row.get('metadata') or {};model=row.get('model') or (platform_model() if platform_service_ready() else '') or metadata.get('model') or ''
            trace['model']=model
            company_key=''
            if row.get('secrets'):
                try:
                    secret=json.loads(_fernet().decrypt(bytes(row['secrets'])))
                    if secret.get('tenant_id')!=tenant or secret.get('provider')!='chat_ai':raise ValueError()
                    company_key=secret.get('api_key') or ''
                except Exception:raise TenantLinearError('Проверьте ключ AI компании.',503) from None
            key,source=choose_api_credential(tenant,value['agent_id'],scope.get('provider') or 'connect',company_key,model)
            if not key or not model:raise TenantLinearError('Настройте существующий AI-провайдер.',409)
            if source=='platform':
                if not platform_model_allowed(model):raise TenantLinearError('Модель не разрешена платформой.',409)
            elif metadata.get('enabled') is not True or metadata.get('consent') is not True or model not in list(metadata.get('models') or [])+[metadata.get('model')]:raise TenantLinearError('Модель или ключ компании не разрешены.',409)
            cur.execute("SELECT COALESCE(sum(reserved_calls),0) AS calls FROM saas_chat_ai_runs WHERE tenant_id=%s::uuid AND usage_day=(now() AT TIME ZONE 'UTC')::date",(tenant,));calls=int(cur.fetchone()['calls'])
            cur.execute("SELECT COALESCE(sum(reserved_calls),0) AS calls FROM saas_ai_agent_runs WHERE tenant_id=%s::uuid AND created_at>=(now() AT TIME ZONE 'UTC')::date AT TIME ZONE 'UTC' AND (state IN ('generating','completed','unknown') OR reserved_calls>1 OR generated_at IS NOT NULL)",(tenant,));calls+=int(cur.fetchone()['calls'])
            if calls+1>int(metadata.get('daily_limit') or 50):raise TenantLinearError('Дневной лимит AI компании исчерпан.',429)
            cur.execute("SELECT client_text,response FROM saas_ai_test_turns WHERE tenant_id=%s::uuid AND session_id=%s::uuid AND state='completed' ORDER BY created_at DESC,id DESC LIMIT 15",(tenant,id))
            lines=[]
            for turn in reversed(cur.fetchall()):
                lines.append({'speaker':'CLIENT','text':turn['client_text'][:2000]})
                if turn.get('response'):lines.append({'speaker':'AGENT','text':turn['response'][:2000]})
            lines.append({'speaker':'CLIENT','text':text})
            # Reuse bounded multilingual lexical expansion on this transaction.
            from tenant_knowledge_search import tsquery, patterns
            query=tsquery(text[:1000]); query_patterns=patterns(text[:1000])
            cur.execute('''SELECT s.id AS source_id,s.title,c.content FROM saas_ai_knowledge_chunks c JOIN saas_ai_knowledge_sources s
              ON s.tenant_id=c.tenant_id AND s.id=c.source_id WHERE c.tenant_id=%s::uuid AND s.active=TRUE AND (s.agent_id IS NULL OR s.agent_id=%s::uuid)
              AND (c.search_vector @@ to_tsquery('simple',%s) OR c.content ILIKE ANY(%s))
              ORDER BY ts_rank(c.search_vector,to_tsquery('simple',%s)) DESC,s.updated_at DESC,c.chunk_no LIMIT 6''',(tenant,value['agent_id'],query,query_patterns,query))
            knowledge=[{'source_id':str(r['source_id']),'title':r['title'],'content':r['content'][:1800]} for r in cur.fetchall()];trace['knowledge']=knowledge
            rules='\n'.join((row['company_instructions'],row['instructions'],str(scope.get('instructions') or ''))).strip()
            prompt=INSTRUCTIONS+'\n\nBusiness instructions:\n'+rules+'\n'+server_instructions()+'\nThis is a virtual test. All proposed actions are simulated. Never claim a real message, CRM change, task, or webhook was executed.'
            extra={'language':row['language'],'style':row['style'],'knowledge':knowledge,'latest_client_message':text,'trigger_actions':actions,'simulation':True}
            tokens=len(json.dumps({'prompt':prompt,'lines':lines,'extra':extra},ensure_ascii=False).encode())+1800
            cap=platform_token_limit(tenant) if source=='platform' else 0
            if cap:
                cur.execute(TOKEN_USAGE_SQL,(tenant,tenant))
                if int(cur.fetchone()['tokens'])+tokens>cap:raise TenantLinearError('Лимит токенов платформы не допускает этот тест.',429)
            if campaign_id:
                from tenant_knowledge_quality import reserve_budget
                reserve_budget(cur,tenant,campaign_id,tokens)
            snapshot={'agent_version':row['version'],'settings_version':str(row['settings_version']),'config_version':str(row.get('config_version')),'session_revision':value['revision']}
            cur.execute('SELECT 1 FROM saas_chat_ai_runs WHERE tenant_id=%s::uuid AND request_id=%s::uuid',(tenant,request))
            if cur.fetchone():raise TenantLinearError('Код запроса уже использован другой AI-операцией.',409)
            spend_key='testing:'+request
            # Byte length bounds text input tokens; include the provider JSON
            # envelope and fixed output maximum. Pin the settlement tariff.
            reserved_prices=price(model)
            reserve_spend(cur,tenant,spend_key,'testing_primary',model,total_upper=tokens+256)
            cur.execute("INSERT INTO saas_chat_ai_runs(tenant_id,request_id,requested_by,lead_id,input_hash,config_version,status,reserved_calls) VALUES(%s::uuid,%s::uuid,%s,0,%s,'virtual-test','sending',1)",(tenant,request,actor,digest))
            cur.execute("INSERT INTO saas_ai_test_turns(tenant_id,id,session_id,input_hash,client_text,state,trace,reserved_tokens,snapshot) VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s,'generating',%s::jsonb,%s,%s::jsonb)",(tenant,request,id,digest,text,json.dumps(trace),tokens,json.dumps(snapshot)))
            cur.execute('UPDATE saas_ai_test_sessions SET pending_turn=%s::uuid,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid',(request,tenant,id))
        conn.commit()
    return {'tenant_id':tenant,'session_id':id,'request_id':request,'profile':profile,'api_key':key,'model':model,'spend_call_key':spend_key,'reserved_prices':reserved_prices,'lines':lines,'instructions':prompt,'extra':extra,'language':row['language'],'snapshot':snapshot,'handoff_after_reply':any('handoff_after_reply' in (t.get('actions') or []) for t in triggers)}

def finish(item,response=None,usage=None,*,attempted=True):
    # Usage is charged even if live settings changes discard the response.
    # Ambiguous attempts retain their full monetary hold.
    if item.get('spend_call_key'):
        if not attempted:
            release_spend(item['tenant_id'],item['spend_call_key'])
        else:
            known=isinstance(usage,dict) and int(usage.get('total_tokens') or 0)>0
            settle_spend(item['tenant_id'],item['spend_call_key'],usage or {},
                         unknown=not known,reserved_prices=item.get('reserved_prices'))
    with _connect() as conn:
        setup(conn)
        with conn.cursor() as cur:
            tenant=item['tenant_id'];request=item['request_id'];usage=usage or {};actual=max(0,int(usage.get('total_tokens') or 0))
            cur.execute('SELECT id,agent_id FROM saas_ai_test_sessions WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,item['session_id']));session_row=cur.fetchone()
            cur.execute('SELECT state,snapshot,total_tokens FROM saas_ai_test_turns WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,request));turn=cur.fetchone()
            if not turn or turn['state']=='completed':return
            actual=max(actual,int(turn['total_tokens']))
            state='unknown' if attempted and response is None else 'superseded';valid=False
            if response is not None and turn['state']=='generating':
                try:
                    _,actor=_authorize(cur,item['profile']);value=session(cur,tenant,actor,item['session_id'],True);row=config(cur,tenant,value['agent_id'])
                    snap={'agent_version':row['version'],'settings_version':str(row['settings_version']),'config_version':str(row.get('config_version')),'session_revision':value['revision']}
                    valid=not gate(row) and is_open(row.get('schedule') or {}) and snap==item['snapshot'] and value['state']=='active' and str(value.get('pending_turn'))==request
                except TenantLinearError:valid=False
            if valid:state='completed'
            if turn['state']!='generating':state=turn['state'];response=None
            cur.execute('UPDATE saas_ai_test_turns SET state=%s,response=%s,total_tokens=%s,reserved_tokens=CASE WHEN %s THEN 0 ELSE reserved_tokens END,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid',(state,sanitize_draft(response,item['language']) if valid else '',actual,actual>0 or not attempted,tenant,request))
            cur.execute('UPDATE saas_chat_ai_runs SET status=%s,updated_at=now() WHERE tenant_id=%s::uuid AND request_id=%s::uuid',('completed' if valid else 'unknown' if state=='unknown' else 'skipped',tenant,request))
            cur.execute("UPDATE saas_ai_test_sessions SET pending_turn=NULL,reply_count=reply_count+%s,state=CASE WHEN %s THEN 'human' ELSE state END,reason=CASE WHEN %s THEN 'trigger_reply_then_handoff' ELSE reason END,revision=revision+1,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND pending_turn=%s::uuid",(int(valid),bool(valid and item['handoff_after_reply']),bool(valid and item['handoff_after_reply']),tenant,item['session_id'],request))
            if session_row and state in {'unknown','superseded'}:
                from tenant_ai_administrator_observations import ensure_schema as ensure_observation_schema, record_ai_testing_diagnostic
                ensure_observation_schema(conn)
                record_ai_testing_diagnostic(cur,tenant,item['session_id'],request,session_row['agent_id'],state)
        conn.commit()
