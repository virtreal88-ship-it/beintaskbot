"""Isolated PostgreSQL/WASM; no production or paid provider access."""
import json
import uuid
from unittest.mock import Mock
import pytest
from test_tenant_support_inbox import database,db,A,B,CA,CB,AGENT,PROFILE,seed_job
import tenant_knowledge_candidates_store as store
import tenant_knowledge_candidates_service as service
import tenant_knowledge_candidates_policy as policy
import tenant_ai_agents_store as agents
import tenant_ai_agent_run_store as runs
from tenant_linear_policy import TenantLinearError
from tenant_ai_testing_budget import TOKEN_USAGE_SQL

EXPERT=str(uuid.UUID(int=401));GROUP='120363000000000@g.us'

def model_card(refs=('m1','m2','m3'),confirmed=True,title='Нет связи'):
    return {'title':title,'symptoms':['Синхронизация остановилась'],'cause':{'status':'suspected','text':'Соединение недоступно'},
        'resolution_steps':['Проверить соединение','Повторить синхронизацию'],'outcome':{'status':'confirmed' if confirmed else 'unconfirmed','text':'Клиент подтвердил результат' if confirmed else 'Нужна проверка','message_ids':[refs[-1]]},
        'source_message_ids':list(refs),'product':'Bein','product_version':'2.1'}

@pytest.fixture
def ready(db,monkeypatch):
    for module in (store,service,runs):monkeypatch.setattr(module,'_connect',lambda:db)
    seed_job(db)
    db.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,created_by,role) VALUES(%s::uuid,%s::uuid,'Expert',11,'expert')",(A,EXPERT))
    db.execute("UPDATE saas_crm_messages SET body='Синхронизация не работает' WHERE tenant_id=%s::uuid",(A,))
    for index,(body,direction) in enumerate([('Проверьте соединение и повторите синхронизацию','outgoing'),('Всё работает, помогло','incoming')]):
        db.execute("INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,body,happened_at) VALUES(%s::uuid,%s::uuid,41,%s,%s,%s,%s,now()+(%s * interval '1 second'))",
            (A,str(uuid.UUID(int=800+index)),'response-'+str(index),'connect:'+CA,direction,body,index+1))
    db.commit()
    monkeypatch.setattr(service,'choose_api_credential',lambda *a:('synthetic-key','platform'))
    monkeypatch.setattr(service,'platform_model_allowed',lambda *a:True)
    monkeypatch.setattr(service,'platform_token_limit',lambda *a:0)
    generation=Mock(return_value=(json.dumps({'cards':[model_card()]},ensure_ascii=False),{'input_tokens':40,'output_tokens':20,'total_tokens':60}))
    monkeypatch.setattr(service,'generate',generation)
    return db,generation,{'request_id':str(uuid.uuid4()),'provider':'connect','channel_id':CA,'conversation_id':'client','analysis_agent_id':AGENT}

def candidate(ready):
    db,generation,data=ready
    return service.analyze(PROFILE,data)['candidates'][0]

def test_private_pending_review_edit_approve_agent_scoped_idempotent(ready):
    db,generation,data=ready
    before=db.execute('SELECT enabled,mode,scopes,version FROM saas_ai_agents ORDER BY id');db.commit()
    first=service.analyze(PROFILE,data);value=first['candidates'][0]
    assert value['status']=='pending_review' and value['outcome']['status']=='confirmed'
    assert store.listing(PROFILE,data)['candidates'][0]['id']==value['id']
    assert not db.execute('SELECT id FROM saas_ai_knowledge_sources');db.commit()
    assert service.analyze(PROFILE,data)['request']['id']==first['request']['id']
    assert service.analyze(PROFILE,dict(reversed(list(data.items()))))['request']['id']==first['request']['id']
    assert service.analyze(PROFILE,{**data,'request_id':str(uuid.uuid4())})['request']['id']==first['request']['id']
    generation.assert_called_once()
    changed=store.mutate(PROFILE,value['id'],{'expected_version':1,'title':'Проверка синхронизации'})['candidate']
    assert changed['version']==2
    with pytest.raises(TenantLinearError):store.mutate(PROFILE,value['id'],{'expected_version':1,'title':'Old'})
    result=store.mutate(PROFILE,value['id'],{'expected_version':2,'agent_ids':[AGENT,EXPERT]},'approve')['candidate']
    assert result['status']=='approved' and len(result['publications'])==2
    again=store.mutate(PROFILE,value['id'],{'expected_version':2,'agent_ids':[EXPERT,AGENT]},'approve')['candidate']
    assert again['publications']==result['publications']
    assert len(db.execute('SELECT * FROM saas_ai_knowledge_sources'))==2
    assert all(row['provenance']['candidate_id']==value['id'] for row in db.execute('SELECT provenance FROM saas_ai_knowledge_sources'))
    assert runs.knowledge_context(A,'синхронизации',agent_id=AGENT)
    assert not runs.knowledge_context(B,'синхронизации',agent_id=AGENT)
    assert db.execute('SELECT enabled,mode,scopes,version FROM saas_ai_agents ORDER BY id')==before

@pytest.mark.parametrize('confirmation',['Предлагаю перезапустить','AI считает проблему решённой','Не всё работает','not fixed'])
def test_unconfirmed_and_ai_only_never_publish(ready,confirmation):
    db,generation,data=ready
    db.execute("UPDATE saas_crm_messages SET body=%s WHERE external_id='response-1'",(confirmation,));db.commit()
    value=candidate(ready)
    assert value['outcome']['status']=='unconfirmed' and not value['can_approve']
    with pytest.raises(TenantLinearError,match='confirmation_required'):store.mutate(PROFILE,value['id'],{'expected_version':1,'agent_ids':[AGENT]},'approve')
    assert not db.execute('SELECT id FROM saas_ai_knowledge_sources')

def test_ai_confirmation_does_not_prove_resolution(ready):
    db,generation,data=ready
    db.execute("UPDATE saas_crm_messages SET direction='outgoing',author_name='AI Agent',raw=%s::jsonb WHERE external_id='response-1'",(json.dumps({'run_id':str(uuid.uuid4())}),));db.commit()
    assert candidate(ready)['outcome']['status']=='unconfirmed'

def test_rejection_and_foreign_employee_permissions(ready):
    db,generation,data=ready;value=candidate(ready)
    foreign={'tenant_id':B,'telegram_id':22}
    for operation in (lambda:store.detail(foreign,value['id']),lambda:store.mutate(foreign,value['id'],{'expected_version':1},'reject'),lambda:service.analyze(PROFILE,{**data,'channel_id':CB})):
        with pytest.raises(TenantLinearError):operation()
    db.execute("UPDATE saas_tenant_members SET role='manager' WHERE tenant_id=%s::uuid",(A,));db.commit()
    with pytest.raises(TenantLinearError):store.detail(PROFILE,value['id'])
    db.execute("UPDATE saas_tenant_members SET role='owner' WHERE tenant_id=%s::uuid",(A,));db.commit()
    assert store.mutate(PROFILE,value['id'],{'expected_version':1},'reject')['candidate']['status']=='rejected'
    with pytest.raises(TenantLinearError):store.mutate(PROFILE,value['id'],{'expected_version':2,'agent_ids':[AGENT]},'approve')

def test_personal_data_and_secrets_removed_before_provider(ready):
    db,generation,data=ready
    sensitive='Иван Иванов +994501234567 user@example.com\nпароль: test-secret-password\nhttps://private.local/?token=abcdef\nадрес: Баку дом 99'
    db.execute("UPDATE saas_crm_messages SET body=%s,author_name='Иван Иванов' WHERE external_id='m1' AND tenant_id=%s::uuid",(sensitive,A));db.commit()
    candidate(ready)
    prompt=json.dumps(generation.call_args.args[3],ensure_ascii=False)
    for value in ('Иван','994501234567','user@example.com','test-secret-password','private.local','Баку'):
        assert value not in prompt
    assert '[SECRET]' in prompt and '[EMAIL]' in prompt
    assert all(set(m)=={'ref','role','text'} for m in generation.call_args.args[3])

def test_invalid_foreign_evidence_is_rejected_but_usage_recorded(ready):
    db,generation,data=ready
    value=model_card();value['source_message_ids']=['m999']
    generation.return_value=(json.dumps({'cards':[value]}),{'total_tokens':17})
    response=service.analyze(PROFILE,data)
    assert not response['candidates'] and response['request']['error_code']=='invalid_model_result'
    assert int(db.execute(TOKEN_USAGE_SQL,(A,A))[0]['tokens'])==17

def test_model_failure_unknown_budget_retained_without_retries(ready):
    db,generation,data=ready;generation.side_effect=RuntimeError('SECRET provider diagnostic')
    first=service.analyze(PROFILE,data)
    assert first['request']['status']=='unknown' and not first['candidates']
    service.analyze(PROFILE,data);generation.assert_called_once()
    assert int(db.execute(TOKEN_USAGE_SQL,(A,A))[0]['tokens'])==policy.MAX_TOKENS
    assert 'SECRET' not in json.dumps(first)

@pytest.mark.parametrize('kind',['tokens','daily_calls','emergency','archived'])
def test_company_limits_and_stop_before_model(ready,monkeypatch,kind):
    db,generation,data=ready
    if kind=='tokens':monkeypatch.setattr(service,'platform_token_limit',lambda *a:100)
    elif kind=='daily_calls':
        db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status,metadata) VALUES(%s::uuid,'chat_ai','connected',%s::jsonb)",(A,json.dumps({'daily_limit':1})))
        db.execute("INSERT INTO saas_ai_agent_runs(tenant_id,id,agent_id,event_fingerprint,revision,model,state) VALUES(%s::uuid,%s::uuid,%s::uuid,'evt',1,'test','completed')",(A,str(uuid.uuid4()),AGENT))
    elif kind=='emergency':db.execute('UPDATE saas_ai_agent_settings SET emergency_stop=TRUE')
    else:db.execute("UPDATE saas_tenants SET status='archived'")
    db.commit()
    with pytest.raises(TenantLinearError):service.analyze(PROFILE,data)
    generation.assert_not_called()

def test_revoked_access_during_model_discards_cards_but_charges_usage(ready):
    db,generation,data=ready
    def revoke(*a,**kw):
        db.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s::uuid',(A,));db.commit()
        return json.dumps({'cards':[model_card()]}),{'total_tokens':19}
    generation.side_effect=revoke
    with pytest.raises(TenantLinearError):service.analyze(PROFILE,data)
    assert not db.execute('SELECT id FROM saas_knowledge_candidates')
    assert int(db.execute(TOKEN_USAGE_SQL,(A,A))[0]['tokens'])==19

def test_atomic_publication_rollback_foreign_agent(ready):
    db,generation,data=ready;value=candidate(ready)
    with pytest.raises(TenantLinearError):store.mutate(PROFILE,value['id'],{'expected_version':1,'agent_ids':[AGENT,str(uuid.uuid4())]},'approve')
    assert not db.execute('SELECT id FROM saas_ai_knowledge_sources')
    assert store.detail(PROFILE,value['id'])['candidate']['status']=='pending_review'

def test_source_mutation_blocks_publish_and_cross_route_selection(ready):
    db,generation,data=ready;value=candidate(ready)
    with pytest.raises(TenantLinearError):service.analyze(PROFILE,{**data,'request_id':str(uuid.uuid4()),'message_ids':[str(uuid.uuid4())]})
    db.execute("UPDATE saas_crm_messages SET body='Changed' WHERE external_id='response-1'");db.commit()
    with pytest.raises(TenantLinearError):store.mutate(PROFILE,value['id'],{'expected_version':1,'agent_ids':[AGENT]},'approve')

def test_unified_group_two_channels_dedup_multiple_problems(ready):
    db,generation,data=ready
    db.execute('DELETE FROM saas_crm_messages WHERE tenant_id=%s::uuid',(A,))
    db.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'second')",(A,CB))
    for channel,lead in ((CA,1001),(CB,1002)):
        db.execute('INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id) VALUES(%s::uuid,%s::uuid,%s,%s)',(A,channel,GROUP,lead))
        for index,body in enumerate(['Проблема синхронизации','Проверьте соединение','Всё работает, помогло']):
            raw={'channelId':channel,'conversationId':GROUP,'externalMessageId':'EVENT_'+str(index),'participantJid':'994500000001@s.whatsapp.net','participantName':'Иван Иванов'}
            db.execute('INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,body,message_type,raw,happened_at) VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s,%s::jsonb,now()+(%s * interval \'1 second\'))',
                (A,str(uuid.uuid4()),lead,str(uuid.uuid4()),'connect:'+channel,'outgoing' if index==1 else 'incoming',body,'text',json.dumps(raw),index))
    db.commit()
    generation.return_value=(json.dumps({'cards':[model_card(),model_card(title='Ошибка кассы')]}),{'total_tokens':30})
    response=service.analyze(PROFILE,{**data,'conversation_id':GROUP,'history_mode':'unified_group'})
    assert len(response['candidates'])==2
    assert len(generation.call_args.args[3])==3
    assert set(response['request']['route']['source_channels'])=={CA,CB}
    assert all(len(c['source_message_ids'])==3 for c in response['candidates'])
    assert len(db.execute('SELECT id FROM saas_crm_messages WHERE tenant_id=%s::uuid',(A,)))==6
    # Channel-only remains a separate exact route, never an implicit cross-channel merge.
    generation.reset_mock()
    response=service.analyze(PROFILE,{**data,'request_id':str(uuid.uuid4()),'conversation_id':GROUP})
    assert response['request']['route']['source_channels']==[CA]

def test_empty_result_is_valid_and_no_training_or_send_imports(ready):
    from pathlib import Path
    db,generation,data=ready;generation.return_value=('{"cards":[]}',{'total_tokens':7})
    assert service.analyze(PROFILE,data)['candidates']==[]
    source=Path(service.__file__).read_text(encoding='utf-8-sig')
    assert not any(word in source for word in ('send_ai_reply','requests.post','subprocess','fine_tun'))

def test_approval_publication_is_one_transaction(ready,monkeypatch):
    db,generation,data=ready;value=candidate(ready);original=store.save_knowledge;count=[0]
    def save(*a,**kw):
        count[0]+=1
        if count[0]==2:raise RuntimeError('synthetic second-source failure')
        return original(*a,**kw)
    monkeypatch.setattr(store,'save_knowledge',save)
    with pytest.raises(RuntimeError):store.mutate(PROFILE,value['id'],{'expected_version':1,'agent_ids':[AGENT,EXPERT]},'approve')
    assert not db.execute('SELECT id FROM saas_ai_knowledge_sources')
    assert store.detail(PROFILE,value['id'])['candidate']['status']=='pending_review'

@pytest.mark.parametrize('value',['Проверьте, теперь работает?','Check if it works','Возможно, помогло','if fixed','unresolved','заработало бы'])
def test_hypotheses_and_questions_not_confirmation(value):
    assert not policy.positive_confirmation(value)

def test_extraction_daily_cap_and_concurrency(ready):
    db,generation,data=ready
    item=service.prepare(PROFILE,data)
    db.execute("UPDATE saas_crm_messages SET body='Changed fragment' WHERE external_id='m1' AND tenant_id=%s::uuid",(A,));db.commit()
    with pytest.raises(TenantLinearError,match='завершения'):service.prepare(PROFILE,{**data,'request_id':str(uuid.uuid4())})
    db.execute("UPDATE saas_knowledge_extractions SET status='unknown'");db.commit()
    # Unknown paid attempts still consume daily extraction capacity.
    for n in range(9):
        rid=str(uuid.uuid4());event='test-cap-'+str(n)
        db.execute("INSERT INTO saas_tenant_webhook_events(tenant_id,fingerprint,provider) VALUES(%s::uuid,%s,'connect')",(A,event))
        db.execute("INSERT INTO saas_ai_agent_runs(tenant_id,id,agent_id,event_fingerprint,revision,model,state) VALUES(%s::uuid,%s::uuid,%s::uuid,%s,1,'test','unknown')",(A,rid,AGENT,event))
        db.execute("INSERT INTO saas_knowledge_extractions(tenant_id,id,created_by,route,source_refs,source_digest,fingerprint,request_key,status,deadline) VALUES(%s::uuid,%s::uuid,11,'{}','[]','hash',%s,'test-request-key','unknown',now())",(A,rid,event))
    db.commit()
    with pytest.raises(TenantLinearError,match='10 в сутки'):service.prepare(PROFILE,{**data,'request_id':str(uuid.uuid4())})
    generation.assert_not_called()

def test_deactivate_published_source_stops_agent_retrieval(ready):
    db,generation,data=ready;value=candidate(ready)
    result=store.mutate(PROFILE,value['id'],{'expected_version':1,'agent_ids':[AGENT]},'approve')['candidate']
    assert runs.knowledge_context(A,'Симптомы',agent_id=AGENT)
    agents.save_knowledge(PROFILE,{'active':False},source_id=result['publications'][0]['source_id'])
    assert not runs.knowledge_context(A,'Симптомы',agent_id=AGENT)

def test_http_origin_identity_scope_and_no_store(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    import tenant_knowledge_candidates_api as api
    async def scenario():
        operation=Mock(return_value={'request':{'status':'completed'},'candidates':[]})
        monkeypatch.setattr(api,'analyze',operation)
        payload={'expected_tenant_id':A,'expected_user_id':11,'request_id':str(uuid.uuid4()),'channel_id':CA,'conversation_id':'client'}
        async def body():return payload
        request=SimpleNamespace(method='POST',content_type='application/json',headers={'Origin':'https://test.local'},json=body,match_info={})
        handle=api.handler(lambda _:PROFILE,'https://test.local',Mock())
        result=await handle(request)
        assert result.status==200 and result.headers['Cache-Control']=='no-store'
        assert operation.call_args.args[1].get('expected_tenant_id') is None
        request.headers={'Origin':'https://evil.local'}
        assert (await handle(request)).status==403
        request.headers={'Origin':'https://test.local'};payload['expected_tenant_id']=B
        assert (await handle(request)).status==409
        assert operation.call_count==1
        request.method='GET';request.query={'expected_tenant_id':A,'expected_user_id':'11'}
        read=Mock(return_value={'candidates':[]});monkeypatch.setattr(api,'listing',read)
        assert (await handle(request)).status==200
        assert read.call_count==1
    asyncio.run(scenario())

def test_model_cannot_certify_its_own_cause(ready):
    db,generation,data=ready
    value=model_card();value['cause']['status']='confirmed'
    generation.return_value=(json.dumps({'cards':[value]}),{'total_tokens':5})
    result=candidate(ready)
    assert result['cause']['status']=='suspected'
    edited=store.mutate(PROFILE,result['id'],{'expected_version':1,'cause':{'status':'confirmed','text':'Сотрудник проверил причину по исходным сообщениям'}})['candidate']
    assert edited['cause']['status']=='confirmed'

def test_group_ai_authored_flag_never_human_confirmation(ready):
    db,generation,data=ready
    db.execute("UPDATE saas_crm_messages SET raw=%s::jsonb WHERE external_id='response-1'",(json.dumps({'ai_authored':True}),));db.commit()
    assert candidate(ready)['outcome']['status']=='unconfirmed'

def test_expired_request_reports_unknown_without_replay(ready):
    db,generation,data=ready
    item=service.prepare(PROFILE,data)
    db.execute("UPDATE saas_knowledge_extractions SET deadline=now()-interval '1 second'");db.commit()
    response=store.request_detail(PROFILE,item['id'])
    assert response['request']['status']=='unknown' and response['request']['error_code']=='provider_timeout'
    assert service.analyze(PROFILE,data)['request']['id']==item['id']
    generation.assert_not_called()

def test_previewed_message_bounds_and_same_request_other_fragment(ready):
    db,generation,data=ready
    result=service.analyze(PROFILE,data)
    assert result['request']['message_count']==3
    assert len(result['request']['source_message_ids'])==3
    assert result['request']['limits']['max_messages']==80
    with pytest.raises(TenantLinearError):service.analyze(PROFILE,{**data,'message_ids':[result['request']['source_message_ids'][0]]})
    generation.assert_called_once()


def test_same_request_replay_keeps_original_window_after_new_messages(ready):
    db,generation,data=ready
    first=service.analyze(PROFILE,data)
    db.execute("INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,body) VALUES(%s::uuid,%s::uuid,41,'new-message',%s,'incoming','Новый вопрос')",(A,str(uuid.uuid4()),'connect:'+CA));db.commit()
    replay=service.analyze(PROFILE,data)
    assert replay['request']['id']==first['request']['id']
    assert replay['request']['source_message_ids']==first['request']['source_message_ids']
    generation.assert_called_once()


@pytest.mark.parametrize('steps',[[],['Проверить соединение']])
def test_unresolved_problem_retained_for_clarification_without_publication(ready,steps):
    db,generation,data=ready
    value=model_card(confirmed=False)
    value['resolution_steps']=steps
    value['outcome']={'status':'unknown','text':'Результат неизвестен','message_ids':[]}
    generation.return_value=(json.dumps({'cards':[value]}),{'input_tokens':40,'output_tokens':20,'total_tokens':60})
    first=service.analyze(PROFILE,data)
    result=first['candidates'][0]
    assert first['request']['status']=='completed'
    assert result['status']=='needs_clarification'
    assert result['resolution_steps']==steps and not result['can_approve']
    assert service.analyze(PROFILE,data)['candidates'][0]['id']==result['id']
    generation.assert_called_once()
    with pytest.raises(TenantLinearError,match='confirmation_required'):
        store.mutate(PROFILE,result['id'],{'expected_version':1,'agent_ids':[AGENT]},'approve')
    assert not db.execute('SELECT id FROM saas_ai_knowledge_sources')


def test_confirmed_card_without_solution_rejected():
    value=model_card();value['resolution_steps']=[]
    ids=[str(uuid.UUID(int=i)) for i in (1,2,3)]
    value['source_message_ids']=ids;value['outcome']['message_ids']=[ids[-1]]
    with pytest.raises(TenantLinearError):policy.card(value,ids)


def test_empty_model_result_completes_without_persistence(ready):
    db,generation,data=ready
    generation.return_value=('{'+'"cards":[]}',{'input_tokens':399,'output_tokens':5,'total_tokens':404})
    result=service.analyze(PROFILE,data)
    assert result['request']['status']=='completed' and result['candidates']==[]
    assert service.analyze(PROFILE,data)['candidates']==[]
    generation.assert_called_once()

def test_knowledge_extraction_defaults_to_az_and_preserves_explicit_languages():
    assert policy.language(None,{})=='az'
    assert policy.language(None,{'language':'en','language_code':'ru'})=='az'
    assert policy.language('en',{})=='en'
    assert policy.language('ru',{'language':'az'})=='ru'


def test_card_content_language_is_persistable_and_validated():
    value=model_card();ids=[str(uuid.uuid4()) for _ in value['source_message_ids']];value['source_message_ids']=ids;value['outcome']['message_ids']=[ids[-1]]
    assert policy.card(value,ids)['content_language']=='az'
    assert policy.card({**value,'content_language':'en'},ids)['content_language']=='en'
    with pytest.raises(TenantLinearError):policy.card({**value,'content_language':'fr'},ids)


def test_legacy_card_language_uses_extraction_then_saved_az_is_returned(ready):
    db,_,data=ready;value=candidate(ready)
    db.execute("UPDATE saas_knowledge_candidates SET content=content-'content_language' WHERE id=%s::uuid",(value['id'],))
    db.execute("UPDATE saas_knowledge_extractions SET route=jsonb_set(route,'{language}','\"en\"'::jsonb) WHERE id=%s::uuid",(value['extraction_id'],));db.commit()
    assert store.detail(PROFILE,value['id'])['candidate']['content_language']=='en'
    assert store.listing(PROFILE,{'limit':25})['candidates'][0]['content_language']=='en'
    saved=store.mutate(PROFILE,value['id'],{'expected_version':1,'content_language':'az'})['candidate']
    assert saved['content_language']=='az'
    assert store.listing(PROFILE,{'limit':25})['candidates'][0]['content_language']=='az'


def test_remote_case_without_employee_actions_cannot_publish():
    value=model_card(confirmed=True)
    value['resolution_method']='remote';value['operator_actions']=[]
    ids=[str(uuid.UUID(int=i)) for i in (1,2,3)]
    value['source_message_ids']=ids;value['outcome']['message_ids']=[ids[-1]]
    normalized=policy.card(value,ids)
    assert policy.approval_reason(normalized)=='operator_actions_required'
    assert normalized['resolution_steps']


def test_informational_case_does_not_require_technical_resolution_steps():
    value=model_card(confirmed=True)
    value['case_kind']='informational';value['resolution_steps']=[]
    ids=[str(uuid.UUID(int=i)) for i in (1,2,3)]
    value['source_message_ids']=ids;value['outcome']['message_ids']=[ids[-1]]
    normalized=policy.card(value,ids)
    assert policy.approval_reason(normalized) is None


def test_azerbaijani_action_summary_keeps_exact_original_quote_as_evidence():
    message_id=str(uuid.UUID(int=77))
    item={'messages':[{'ref':'m1','role':'operator','text':'Routeri yenidən başladım'}],
          'refs':[{'id':message_id}]}
    value=model_card(refs=('m1',),confirmed=False)
    value.update(resolution_method='remote',operator_actions=['Əməkdaş routeri yenidən başlatdı'],
        operator_action_evidence=[{'action_index':0,'message_ref':'m1','quote':'Routeri yenidən başladım'}])
    parsed=service.parse(json.dumps({'cards':[value]},ensure_ascii=False),item)[0]
    assert parsed['operator_actions']==['Əməkdaş routeri yenidən başlatdı']
    assert parsed['operator_action_evidence']==[{'action_index':0,'message_id':message_id,'quote':'Routeri yenidən başladım'}]
    value['operator_action_evidence'][0]['quote']='Invented quote'
    parsed=service.parse(json.dumps({'cards':[value]},ensure_ascii=False),item)[0]
    assert parsed['operator_actions']==[] and parsed['operator_action_evidence']==[]
