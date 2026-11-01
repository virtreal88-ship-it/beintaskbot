"""One owner-approved, saved-evidence-only draft. No media dispatch path exists here."""
import hashlib
import json
import os
import time
import uuid
from tenant_linear_policy import TenantLinearError
from tenant_platform import _connect,_fernet

APPROVED_SCOPE_DIGEST=None  # Set only after the parent verifies the authorized target.
CASE='owner-saved-draft-v1'
CAP=940000
OUTPUT=900
ENVELOPE_TOKENS=8192
PRICES=(2000000,8000000)
MODELS={'gpt-4.1','gpt-4.1-2025-04-14'}
NAMESPACE=uuid.UUID('b66e4399-9a4d-4e4d-9cf8-911c5f49cde9')


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,default=str,separators=(',',':')).encode()).hexdigest()


def scope_digest(tenant,command,actor):
    return digest([CASE,str(uuid.UUID(str(tenant))),str(actor),str(command['channel_id']),str(command['conversation_id']),int(command['lead_id'])])


def request_id(tenant,command):
    return str(uuid.uuid5(NAMESPACE,CASE+':'+str(uuid.UUID(str(tenant)))))


def _command(profile,data):
    from tenant_operator_copilot import _clean
    if not isinstance(data,dict) or not (data.get('saved_only') is True or data.get('saved_only')=='1' and isinstance(data.get('saved_only'),str)):raise TenantLinearError('Saved-only mode is required.',400)
    if set(data)-{'saved_only','mode','prompt','include_media','provider','lead_id','channel_id','conversation_id','request_id','cost_token','expected_tenant_id','expected_user_id'} or data.get('mode','reply')!='reply' or data.get('prompt','') or data.get('include_media',False) is not False or data.get('provider','connect')!='connect':raise TenantLinearError('Saved-only request parameters are invalid.',400)
    candidate={**data,'mode':'reply','prompt':'','include_media':False,'request_id':str(uuid.uuid4())}
    command=_clean(candidate)
    stable=request_id(profile['tenant_id'],command)
    if data.get('request_id') and str(data['request_id'])!=stable:raise TenantLinearError('The approved draft has one fixed request ID.',409)
    command.update(request_id=stable,_saved_only=True)
    return command


def _tariff(model):
    if model not in MODELS:raise TenantLinearError('The assigned model is not approved for this bounded draft.',409)
    from tenant_ai_spend_budget import price
    raw=os.environ.get('AI_MODEL_PRICES_MICROUSD_PER_MILLION','')
    if raw:
        try: configured=json.loads(raw)
        except (TypeError,ValueError):raise TenantLinearError('Invalid configured text tariff.',409) from None
        if not isinstance(configured,dict) or model in configured and price(model)!=PRICES:
            raise TenantLinearError('The configured text tariff does not match the approved bound.',409)
    return PRICES


def quote_payload(wire):
    if not isinstance(wire,dict) or set(wire)!={'model','store','max_output_tokens','instructions','input','service_tier'} or not isinstance(wire.get('instructions'),str) or wire.get('model') not in MODELS or wire.get('max_output_tokens')!=OUTPUT or wire.get('store') is not False or wire.get('service_tier')!='default' or not isinstance(wire.get('input'),str):
        raise TenantLinearError('Only the approved text-only provider payload can be quoted.',409)
    _tariff(wire['model'])
    from tenant_ai_spend_budget import _total_cost_upper
    input_upper=len(json.dumps(wire,ensure_ascii=True,allow_nan=False).encode())+ENVELOPE_TOKENS
    total_upper=input_upper+OUTPUT
    reserve=_total_cost_upper(PRICES,total_upper)
    if reserve>CAP:raise TenantLinearError('The complete saved draft exceeds the approved $0.94 maximum.',409)
    return {'input_upper':input_upper,'total_upper':total_upper,'reserve':reserve}


def prepare_saved(authority,rows):
    from tenant_episode_media import (episode,reference,revalidate_sources,KINDS,initial_source_refs,normalize_media_row,apply_historical_context,apply_episode_receipt)
    from tenant_media_evidence import reusable,apply_reusable
    selected=episode(rows,latest_request=True)
    authority.source_refs=initial_source_refs(authority,rows,selected)
    selected_ids={str(row.get('external_id')) for row in selected}
    media={'images':[],'voice_transcriptions':set(),'documents':set(),'skipped':[]}
    for row in selected:row['_manual_request_source']=True
    for row in rows:
        kind=str(row.get('message_type') or 'text').lower()
        if kind not in KINDS:continue
        historical=normalize_media_row(row,authority.channel)
        if str(row.get('external_id')) not in selected_ids:
            apply_historical_context(row,historical)
            continue
        authority.check()
        if kind=='video':raise TenantLinearError('Full video coverage is unavailable. No paid analysis or draft was started.',409)
        if kind not in {'audio','voice','image','document'}:raise TenantLinearError('This attachment has no complete saved-evidence adapter.',409)
        ref=reference(row,authority.channel,authority.lead)
        from tenant_media_evidence import source_hash
        if not any(source_hash(saved)==source_hash(ref) and saved['external_id']==ref['external_id'] for saved in authority.source_refs):authority.source_refs.append(ref)
        saved=reusable({'tenant_id':authority.tenant},ref)
        if not saved or saved.get('status')!='processed' or (saved.get('coverage') or {}).get('complete') is False:
            raise TenantLinearError('Complete saved evidence is required for every current attachment. No analysis was started.',409)
        message={'text':'','ref':ref['id']};receipt={'id':ref['id'],'kind':ref['media_kind'],**apply_reusable(message,saved)}
        if kind in {'audio','voice'} and not str(receipt.get('transcript') or '').strip():raise TenantLinearError('Saved speech is incomplete.',409)
        if kind in {'image','document'} and not (receipt.get('observation') or receipt.get('observations')):raise TenantLinearError('Saved visual/document evidence is incomplete.',409)
        apply_episode_receipt(row,message,receipt)
        if kind in {'audio','voice'}:media['voice_transcriptions'].add(ref['external_id'])
        if kind=='document':
            text=str(receipt.get('observation') or '')
            if not text or len(message['text'])+len(row['_copilot_media_notice'])+2>1200:raise TenantLinearError('Saved document evidence does not fit completely in the context.',409)
            media['documents'].add(ref['external_id'])
    with _connect() as conn:
        with conn.cursor() as cur:revalidate_sources(cur,authority.tenant,authority.source_refs)
    return media


def _read(profile,data):
    from tenant_operator_access import access,assert_route
    from tenant_operator_copilot import _assigned_context,_agent_binding,_read_history,_draft_context
    command=_command(profile,data)
    with _connect() as conn:
        with conn.cursor() as cur:
            tenant,actor,policy=access(cur,profile,'dialogs')
            cur.execute('SELECT role,active FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s',(tenant,actor))
            member=cur.fetchone() or {}
            if member.get('role')!='owner' or member.get('active') is not True or not policy.can_use_chat_ai():raise TenantLinearError('Active owner access is required.',403)
            route=assert_route(cur,tenant,policy,'connect',command['conversation_id'],command['channel_id'],command['lead_id'])
            if int(route.get('lead_id') or 0)!=command['lead_id']:raise TenantLinearError('The selected dialog changed.',409)
            if not APPROVED_SCOPE_DIGEST or scope_digest(tenant,command,actor)!=APPROVED_SCOPE_DIGEST:raise TenantLinearError('No approved saved-only target is configured.',403)
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",('tenant-chat-ai:'+tenant,))
            cur.execute("SELECT secrets,metadata,updated_at FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='chat_ai' FOR SHARE",(tenant,))
            connection=cur.fetchone() or {};config=connection.get('metadata') or {}
            if not connection.get('secrets') or any(config.get(key) is not True for key in ('enabled','consent')) or config.get('copilot_enabled',True) is not True:raise TenantLinearError('Company Chat AI consent is required.',409)
            agent=_assigned_context(cur,tenant,command);model=str(agent.get('model') or '')
            _tariff(model);command.update(_agent_binding=_agent_binding(agent),_agent_id=str(agent['agent_id']))
            try:secret=json.loads(_fernet().decrypt(bytes(connection['secrets'])))
            except Exception:raise TenantLinearError('Company credentials are unavailable.',503) from None
            if secret.get('tenant_id')!=tenant or secret.get('provider')!='chat_ai' or not secret.get('api_key'):raise TenantLinearError('Company credentials are unavailable.',503)
            rows,turns=_read_history(cur,tenant,actor,command)
            cur.execute('SELECT requested_by,input_hash,status,result FROM saas_chat_ai_runs WHERE tenant_id=%s::uuid AND request_id=%s::uuid',(tenant,command['request_id']))
            existing=cur.fetchone()
            cur.execute("SELECT to_regclass('public.saas_ai_spend_reservations') AS relation")
            orphan=False
            if not existing and cur.fetchone()['relation']:
                cur.execute('SELECT id FROM saas_ai_spend_reservations WHERE tenant_id=%s::uuid AND call_key=%s',(tenant,'saved-only:'+command['request_id']))
                orphan=bool(cur.fetchone())
            if existing and int(existing['requested_by'])!=actor:raise TenantLinearError('This approved request belongs to another owner.',403)
        conn.commit()
    context=_draft_context(tenant,actor,command,config,connection,secret,model,agent,rows,turns,saved_only=True)
    if context['images']:raise TenantLinearError('Image inputs are forbidden in saved-only mode.',409)
    from tenant_chat_ai_provider import request_payload
    wire=request_payload(model,'reply',context['lines'],context['request'],instructions=context['instructions'],extra_context=context['extra_context'],max_output_tokens=OUTPUT,service_tier='default')
    bound=quote_payload(wire)
    input_upper,total_upper,reserve=bound['input_upper'],bound['total_upper'],bound['reserve']
    knowledge_versions=[];publications=[]
    with _connect() as conn:
        with conn.cursor() as cur:
            ids=[str(item['source_id']) for item in context['knowledge']]
            if ids:
                cur.execute('SELECT id::text,content_hash,updated_at,provenance,active,agent_id::text FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid AND id=ANY(%s::uuid[]) ORDER BY id',(tenant,ids))
                knowledge_versions=[dict(row) for row in cur.fetchall()]
                if len(knowledge_versions)!=len(set(ids)) or any(not item['active'] or item['agent_id'] not in {None,command['_agent_id']} for item in knowledge_versions):raise TenantLinearError('Knowledge publication changed.',409)
            from tenant_media_evidence import publication_fingerprint
            from tenant_episode_media import reference
            from tenant_dialog_audio_store import current_ref
            for row in rows:
                if str(row.get('message_type') or 'text').lower() not in {'','text'}:
                    publications.append([str(row.get('external_id')),publication_fingerprint(cur,tenant,row),current_ref(cur,tenant,reference(row,command['channel_id'],command['lead_id']))])
        conn.commit()
    fingerprint=digest([CASE,tenant,actor,command['_agent_binding'],str(connection['updated_at']),
        hashlib.sha256(bytes(connection['secrets'])).hexdigest(),command['_source_versions'],
        [row.get('_episode_receipt') for row in rows],knowledge_versions,publications,wire,PRICES])
    from tenant_episode_media import reference
    from tenant_media_evidence import source_hash
    by_source={str(row.get('external_id')):row for row in rows}
    audit={'model':model,'service_tier':'default','fingerprint':fingerprint,'context_usage':context['context_usage'],
        'sources':[{'source_id':line['source_id'],'version':source_hash(reference(by_source[line['source_id']],command['channel_id'],command['lead_id'])),
                    'kind':line['media_kind'],'scope':line['evidence_scope']} for line in context['lines']],
        'knowledge':[{'source_id':item['id'],'version':digest(item)} for item in knowledge_versions]}
    return dict(tenant=tenant,actor=actor,command=command,config=config,connection=connection,key=secret['api_key'],model=model,context=context,
        wire=wire,fingerprint=fingerprint,audit=audit,input_upper=input_upper,total_upper=total_upper,reserve=reserve,existing=existing,orphan=orphan)


def _token(state,expires):
    body={'case':CASE,'request_id':state['command']['request_id'],'actor':state['actor'],'tenant':state['tenant'],
          'fingerprint':state['fingerprint'],'reserve':state['reserve'],'expires':expires}
    return _fernet().encrypt(json.dumps(body,sort_keys=True).encode()).decode()


def preflight(profile,data):
    state=_read(profile,data);old=state['existing'];matches=not old or old['input_hash']==state['fingerprint']
    return {'request_id':state['command']['request_id'],'state':old['status'] if old else 'blocked' if state['orphan'] else 'ready','context_current':matches,
        'result':old.get('result') if old and old['status']=='completed' and matches else None,
        'model':state['model'],'input_upper':state['input_upper'],'max_output_tokens':OUTPUT,'reserve_microusd':state['reserve'],
        'cap_microusd':CAP,'audit':state['audit'],'cost_token':_token(state,int(time.time())+300) if not old and not state['orphan'] else None,'saved_only':True}


def _verify_token(state,token):
    try:value=json.loads(_fernet().decrypt(str(token).encode()))
    except Exception:raise TenantLinearError('Request a fresh saved-only cost preview.',409) from None
    expected={'case':CASE,'request_id':state['command']['request_id'],'actor':state['actor'],'tenant':state['tenant'],
              'fingerprint':state['fingerprint'],'reserve':state['reserve']}
    if any(value.get(key)!=val for key,val in expected.items()) or not isinstance(value.get('expires'),int) or value['expires']<time.time():raise TenantLinearError('The saved-only source, context or cost changed. Request a fresh preview.',409)


def _daily(cur,state):
    tenant,actor,command,config=state['tenant'],state['actor'],state['command'],state['config']
    cur.execute("SELECT COALESCE(sum(reserved_calls),0) AS n FROM saas_chat_ai_runs WHERE tenant_id=%s::uuid AND usage_day=(now() AT TIME ZONE 'UTC')::date",(tenant,))
    if int(cur.fetchone()['n'])>=int(config.get('daily_limit') or 50):raise TenantLinearError('Company daily call limit exhausted.',429)
    cur.execute("SELECT COALESCE(sum(reserved_calls),0) AS n FROM saas_chat_ai_runs WHERE tenant_id=%s::uuid AND provider='connect' AND usage_day=(now() AT TIME ZONE 'UTC')::date",(tenant,))
    if int(cur.fetchone()['n'])>=int(config.get('copilot_daily_limit') or 50):raise TenantLinearError('Copilot daily call limit exhausted.',429)
    cur.execute("SELECT count(*) AS n FROM saas_chat_ai_runs WHERE tenant_id=%s::uuid AND requested_by=%s AND channel_id=%s AND conversation_id=%s AND usage_day=(now() AT TIME ZONE 'UTC')::date",(tenant,actor,command['channel_id'],command['conversation_id']))
    if int(cur.fetchone()['n'])>=20:raise TenantLinearError('Dialog daily call limit exhausted.',429)


def execute(profile,data):
    state=_read(profile,data);command=state['command'];stable=command['request_id'];call_key='saved-only:'+stable
    from tenant_ai_spend_budget import ensure_schema,reserve,settle,release
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",('tenant-chat-ai:'+state['tenant'],))
            cur.execute('SELECT requested_by,input_hash,status,result FROM saas_chat_ai_runs WHERE tenant_id=%s::uuid AND request_id=%s::uuid FOR UPDATE',(state['tenant'],stable))
            old=cur.fetchone()
            if old:
                if int(old['requested_by'])!=state['actor']:raise TenantLinearError('This approved request belongs to another owner.',403)
                if old['status']=='completed' and old['input_hash']==state['fingerprint']:return old['result']
                error=TenantLinearError('The single approved request is already admitted or its result is unknown. It will not run again.',409);error.keep_request=True;raise error
            _verify_token(state,data.get('cost_token'));_daily(cur,state)
            cur.execute('SELECT id FROM saas_ai_spend_reservations WHERE tenant_id=%s::uuid AND call_key=%s',(state['tenant'],call_key))
            if cur.fetchone():raise TenantLinearError('An existing financial hold prevents redispatch.',409)
            reserve(cur,state['tenant'],call_key,'saved_only_copilot',state['model'],state['input_upper'],OUTPUT,total_upper=state['total_upper'],track_uncapped=True,_bounded_prices=PRICES)
            cur.execute('''INSERT INTO saas_chat_ai_runs(tenant_id,request_id,requested_by,lead_id,input_hash,config_version,status,reserved_calls,provider,channel_id,conversation_id)
              VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,'sending',1,'connect',%s,%s)''',(state['tenant'],stable,state['actor'],command['lead_id'],state['fingerprint'],str(state['connection']['updated_at']),command['channel_id'],command['conversation_id']))
        conn.commit()
    dispatched=False
    def guard(wire):
        nonlocal dispatched
        current=_read(profile,data);_verify_token(current,data.get('cost_token'))
        if current['fingerprint']!=state['fingerprint'] or digest(wire)!=digest(state['wire']):raise TenantLinearError('Saved-only context changed before dispatch.',409)
        old=current['existing']
        if not old or old['status']!='sending' or old['input_hash']!=state['fingerprint']:raise TenantLinearError('The approved request is no longer dispatchable.',409)
        dispatched=True
    try:
        from tenant_chat_ai_provider import generate
        context=state['context']
        text,usage=generate(state['key'],state['model'],'reply',context['lines'],context['request'],instructions=context['instructions'],extra_context=context['extra_context'],images=None,with_usage=True,max_output_tokens=OUTPUT,before_dispatch=guard,service_tier='default',strict_usage=True)
        if not isinstance(text,str) or not text.strip():raise ValueError('Invalid provider result')
        result={'text':text,'mode':'reply','usage':usage,'saved_only':True,'source_versions':command['_source_versions'],
                'context_usage':context['context_usage'],'audit':state['audit'],'reserve_microusd':state['reserve'],'request_id':stable}
        # Save a known result before settlement/publication checks. It never
        # mutates message bodies, media receipts or the ordinary private thread.
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE saas_chat_ai_runs SET status='completed',result=%s::jsonb,updated_at=now() WHERE tenant_id=%s::uuid AND request_id=%s::uuid AND status='sending' RETURNING request_id",(json.dumps(result,ensure_ascii=False),state['tenant'],stable))
                if not cur.fetchone():raise TenantLinearError('The known result could not be durably recorded.',409)
            conn.commit()
        try:settle(state['tenant'],call_key,usage,unknown=not (isinstance(usage,dict) and isinstance(usage.get('total_tokens'),int) and usage['total_tokens']>0),reserved_prices=PRICES,track_uncapped=True)
        except Exception:pass  # Keep the financial hold; never discard known text.
        current=_read(profile,data)
        old=current['existing']
        if current['fingerprint']!=state['fingerprint'] or not old or old['status']!='completed' or old['input_hash']!=state['fingerprint'] or old['result']!=result:raise TenantLinearError('The saved result is not durably available in the current context.',409)
        return result
    except Exception:
        with _connect() as conn:
            with conn.cursor() as cur:cur.execute("UPDATE saas_chat_ai_runs SET status='unknown',updated_at=now() WHERE tenant_id=%s::uuid AND request_id=%s::uuid AND status='sending'",(state['tenant'],stable))
            conn.commit()
        if dispatched:settle(state['tenant'],call_key,{},unknown=True,reserved_prices=PRICES,track_uncapped=True)
        else:release(state['tenant'],call_key,track_uncapped=True)
        error=TenantLinearError('The approved draft did not complete in the current context. No automatic repeat is permitted.',409);error.keep_request=True;raise error from None
