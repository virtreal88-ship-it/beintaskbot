"""Focused, offline regressions; real isolated SQL, mocked provider."""
import json
import uuid
import pytest
from test_knowledge_candidates import ready,model_card,candidate
from test_tenant_support_inbox import database,db,A,B,CA,CB,AGENT,PROFILE
from tenant_linear_policy import TenantLinearError
import tenant_knowledge_candidates_service as service
import tenant_knowledge_candidates_store as store
import tenant_knowledge_improve as improve
from tenant_knowledge_candidates_policy import FIELDS


def payload(value,**extra):
    return {'request_id':str(uuid.uuid4()),'expected_version':value['version'],'language':'en',**extra}


@pytest.mark.parametrize('method,message',[('remote','AnyDesk'),('phone','Помощь по телефону')])
def test_remote_phone_without_actions_needs_clarification(ready,method,message):
    d,generation,data=ready
    d.execute("UPDATE saas_crm_messages SET body=%s WHERE external_id='response-0'",(message,));d.commit()
    raw=model_card();raw['resolution_method']=method;raw['operator_actions']=['Invented reset action']
    generation.return_value=(json.dumps({'cards':[raw]}),{'total_tokens':60})
    value=candidate(ready)
    assert value['resolution_method']==method and value['operator_actions']==[]
    assert value['resolution_steps']==[] and value['clarification_status']=='required'
    assert value['status']=='needs_clarification' and not value['can_approve']
    with pytest.raises(TenantLinearError):store.mutate(PROFILE,value['id'],{'expected_version':1,'agent_ids':[AGENT]},'approve')
    assert not d.execute('SELECT id FROM saas_ai_knowledge_sources')


def test_employee_can_clarify_remote_and_publish_with_evidence(ready):
    value=candidate(ready)
    changed=store.mutate(PROFILE,value['id'],{'expected_version':1,'resolution_method':'remote',
        'operator_actions':['Сотрудник проверил соединение'],'resolution_steps':['Проверить соединение']})['candidate']
    assert changed['version']==2 and changed['result_confirmed'] and changed['can_approve']
    result=store.mutate(PROFILE,value['id'],{'expected_version':2,'agent_ids':[AGENT]},'approve')['candidate']
    assert result['status']=='approved'


def test_informational_case_needs_no_technical_cause_or_steps(ready):
    value=candidate(ready)
    changed=store.mutate(PROFILE,value['id'],{'expected_version':1,'case_kind':'informational',
        'resolution_steps':[],'cause':{'status':'unknown','text':''}})['candidate']
    assert changed['can_approve'] and changed['clarification_status']=='not_required'
    assert store.mutate(PROFILE,value['id'],{'expected_version':2,'agent_ids':[AGENT]},'approve')['candidate']['status']=='approved'


@pytest.mark.parametrize('lang',['ru','az','en'])
def test_interface_language_does_not_change_extraction_language(ready,lang):
    d,generation,data=ready
    result=service.analyze(PROFILE,{**data,'language':lang})
    assert generation.call_args.kwargs['extra_context']['output_language']=='az'
    assert result['request']['route']['language']=='az'


def test_campaign_style_extraction_defaults_to_az_even_for_non_az_profile(ready):
    d,generation,data=ready
    profile={**PROFILE,'language':'en','language_code':'ru'}
    result=service.analyze(profile,{**data,'request_id':str(uuid.uuid4())})
    assert generation.call_args.kwargs['extra_context']['output_language']=='az'
    assert result['request']['route']['language']=='az'


def test_old_case_translation_covers_all_explanatory_fields_and_preserves_evidence(ready):
    d,generation,data=ready;value=candidate(ready)
    value=store.mutate(PROFILE,value['id'],{'expected_version':1,'resolution_method':'phone',
        'operator_actions':['Employee restarted the router'],'resolution_steps':['Restart the router']})['candidate']
    original=store.detail(PROFILE,value['id'])['candidate']
    generation.return_value=(json.dumps({'title':'Bağlantı problemi','symptoms':['Tətbiqdə xəta görünür'],
        'cause_text':'Router bağlantısı kəsilmişdi','resolution_steps':['Routeri yenidən başladın'],
        'operator_actions':['Əməkdaş routeri yenidən başlatdı'],'outcome_text':'Nəticə yazışmada təsdiqlənməyib','clarification_note':'Müştərinin terminal növünü dəqiqləşdirin.'}),{'total_tokens':100})
    result=improve.improve(PROFILE,value['id'],payload(value,mode='translate',language='az'))
    proposal=result['proposal']
    assert result['mode']=='translate' and result['language']=='az'
    assert proposal['title']=='Bağlantı problemi'
    assert proposal['cause']['status']==original['cause']['status']
    assert proposal['outcome']['status']==original['outcome']['status']
    assert proposal['outcome']['message_ids']==original['outcome']['message_ids']
    assert proposal['source_message_ids']==original['source_message_ids']
    assert proposal['resolution_steps']==['Routeri yenidən başladın']
    assert proposal['operator_actions']==['Əməkdaş routeri yenidən başlatdı']
    assert proposal['clarification_note']=='Müştərinin terminal növünü dəqiqləşdirin.'
    assert store.detail(PROFILE,value['id'])['candidate']==original
    saved=store.mutate(PROFILE,value['id'],{'expected_version':2,**proposal})['candidate']
    assert saved['operator_actions']==['Əməkdaş routeri yenidən başlatdı']
    assert saved['clarification_note']=='Müştərinin terminal növünü dəqiqləşdirin.'
    assert saved['source_message_ids']==original['source_message_ids']
    assert saved['outcome']['message_ids']==original['outcome']['message_ids']
    assert generation.call_args.kwargs['instructions'].find('Translate every explanatory support-case field')>=0


@pytest.mark.parametrize('lang',['ru','az','en'])
def test_improve_proposal_only_explicit_save_and_idempotency(ready,lang):
    d,generation,data=ready;value=candidate(ready)
    before=store.detail(PROFILE,value['id'])
    generation.return_value=(json.dumps({'title':'Уточнённая проблема','symptoms':['Описание симптомов'],
        'cause_text':'Причина пока не установлена','resolution_steps':['Проверить настройки','Повторить синхронизацию'],
        'operator_actions':[],'outcome_text':'Результат не подтверждён'}),{'total_tokens':90})
    args=payload(value,language=lang,clarifications='user@example.com; пароль: synthetic-secret')
    result=improve.improve(PROFILE,value['id'],args)
    assert result['request']['status']=='completed' and result['requires_explicit_save']
    assert result['language']=='az' and result['expected_version']==1
    proposal=result['proposal']
    assert proposal['cause']['status']==value['cause']['status'] and proposal['cause']['text']=='Причина пока не установлена'
    assert proposal['resolution_steps']==['Проверить настройки','Повторить синхронизацию']
    assert proposal['operator_actions']==[]
    assert proposal['outcome']['status']==value['outcome']['status'] and proposal['outcome']['message_ids']==value['outcome']['message_ids']
    assert proposal['outcome']['text']=='Результат не подтверждён'
    assert proposal['source_message_ids']==value['source_message_ids'] and proposal['operator_action_evidence']==value['operator_action_evidence']
    assert store.detail(PROFILE,value['id'])==before
    assert not d.execute('SELECT id FROM saas_ai_knowledge_sources');d.commit()
    context=generation.call_args.kwargs['extra_context']
    assert context['output_language']=='az' and 'synthetic-secret' not in json.dumps(context)
    assert 'user@example.com' not in json.dumps(context)
    assert improve.improve(PROFILE,value['id'],args)['proposal']==result['proposal']
    assert improve.improve(PROFILE,value['id'],{**args,'request_id':str(uuid.uuid4())})['proposal']==result['proposal']
    assert generation.call_count==2  # one extraction and one improvement
    saved=store.mutate(PROFILE,value['id'],{'expected_version':1,**result['proposal']})['candidate']
    assert saved['version']==2 and saved['title']==result['proposal']['title']
    with pytest.raises(TenantLinearError):improve.improve(PROFILE,value['id'],args)
    assert generation.call_count==2


def test_improve_rejects_model_fact_injection_and_keeps_usage(ready):
    d,generation,data=ready;value=candidate(ready)
    generation.return_value=(json.dumps({'title':'Title','symptoms':['Symptom'],'cause':{'status':'confirmed','text':'Invented'}}),{'total_tokens':44})
    result=improve.improve(PROFILE,value['id'],payload(value))
    assert result['proposal'] is None and result['request']['status']=='failed'
    assert store.detail(PROFILE,value['id'])['candidate']==value
    assert d.execute('SELECT total_tokens FROM saas_ai_agent_token_usage WHERE run_id=%s::uuid',(result['request']['id'],))[0]['total_tokens']==44


@pytest.mark.parametrize('failure',['provider','revoke','version','stop'])
def test_improve_errors_and_changes_never_write_card(ready,failure):
    d,generation,data=ready;value=candidate(ready);args=payload(value)
    def response(*a,**kw):
        if failure=='provider':raise RuntimeError('synthetic failure')
        if failure=='revoke':d.execute("UPDATE saas_tenant_members SET role='manager' WHERE tenant_id=%s::uuid",(A,))
        if failure=='version':d.execute('UPDATE saas_knowledge_candidates SET version=version+1')
        if failure=='stop':d.execute('UPDATE saas_ai_agent_settings SET emergency_stop=true')
        d.commit()
        return json.dumps({'title':'Editorial','symptoms':['Symptom']}),{'total_tokens':20}
    generation.side_effect=response
    if failure in {'revoke','version'}:
        with pytest.raises(TenantLinearError):improve.improve(PROFILE,value['id'],args)
    else:assert improve.improve(PROFILE,value['id'],args)['proposal'] is None
    row=d.execute('SELECT content FROM saas_knowledge_candidates WHERE id=%s::uuid',(value['id'],))[0]
    assert row['content']['title']==value['title']
    assert not d.execute('SELECT id FROM saas_ai_knowledge_sources')


def test_improve_permissions_input_limits_and_request_binding(ready):
    d,generation,data=ready;value=candidate(ready)
    for args in [payload(value,language='de'),payload(value,clarifications='x'*2001),payload(value,expected_version=2)]:
        with pytest.raises(TenantLinearError):improve.improve(PROFILE,value['id'],args)
    with pytest.raises(TenantLinearError):improve.improve({'tenant_id':B,'telegram_id':22},value['id'],payload(value))
    generation.assert_called_once()
    generation.return_value=(json.dumps({'title':'Title','symptoms':['Symptom']}),{'total_tokens':30})
    args=payload(value);improve.improve(PROFILE,value['id'],args)
    with pytest.raises(TenantLinearError):improve.improve(PROFILE,value['id'],{**args,'clarifications':'different request'})
    assert generation.call_count==2


def test_interface_language_independent_dedup_and_company_quota(ready,monkeypatch):
    d,generation,data=ready
    first=service.analyze(PROFILE,{**data,'language':'ru'})
    second=service.analyze(PROFILE,{**data,'request_id':str(uuid.uuid4()),'language':'az'})
    assert first['request']['id']==second['request']['id']
    value=first['candidates'][0]
    monkeypatch.setattr(service,'platform_token_limit',lambda *a:1)
    with pytest.raises(TenantLinearError,match='бюджета'):improve.improve(PROFILE,value['id'],payload(value))
    assert generation.call_count==1


def test_remote_success_confirmed_but_unknown_actions_blocks_publication(ready):
    d,generation,data=ready
    raw=model_card();raw.update(resolution_method='remote',resolution_steps=[],operator_actions=[])
    generation.return_value=(json.dumps({'cards':[raw]}),{'total_tokens':20})
    value=candidate(ready)
    assert value['result_confirmed'] and value['clarification_status']=='required'
    assert value['approval_block_reason']=='operator_actions_required'
    assert not value['can_approve']


@pytest.mark.parametrize('message',['Понятно, спасибо за информацию','Thank you for the information','Məlumat üçün təşəkkür'])
def test_information_confirmation_not_technical_resolution(ready,message):
    d,generation,data=ready
    d.execute("UPDATE saas_crm_messages SET body=%s WHERE external_id='response-1'",(message,));d.commit()
    raw=model_card();raw.update(case_kind='informational',resolution_steps=[],cause={'status':'unknown','text':''})
    generation.return_value=(json.dumps({'cards':[raw]}),{'total_tokens':20})
    value=candidate(ready)
    assert value['can_approve']
    raw['case_kind']='technical';raw['resolution_steps']=['Проверить соединение']
    assert not store.enforce_confirmation(raw,[{'id':ref,'role':'customer','text':message} for ref in raw['source_message_ids']])['outcome']['status']=='confirmed'


def test_improve_channel_revocation_and_known_name_redaction(ready,monkeypatch):
    d,generation,data=ready
    d.execute("UPDATE saas_connect_conversations SET contact_name='SensitiveName' WHERE tenant_id=%s::uuid",(A,));d.commit()
    value=candidate(ready)
    generation.return_value=(json.dumps({'title':'Title','symptoms':['Symptom']}),{'total_tokens':20})
    improve.improve(PROFILE,value['id'],payload(value,clarifications='SensitiveName confirmed extra details'))
    assert 'SensitiveName' not in json.dumps(generation.call_args.kwargs['extra_context'])
    generation.reset_mock()
    def denied(*a,**kw):raise TenantLinearError('Denied',403)
    monkeypatch.setattr(store,'assert_route',denied)
    with pytest.raises(TenantLinearError):improve.improve(PROFILE,value['id'],payload(value))
    generation.assert_not_called()


def test_improve_timeout_unknown_no_retry_and_token_reservation(ready):
    d,generation,data=ready;value=candidate(ready);args=payload(value)
    generation.side_effect=TimeoutError('synthetic timeout')
    first=improve.improve(PROFILE,value['id'],args)
    assert first['request']['status']=='unknown' and first['proposal'] is None
    assert improve.improve(PROFILE,value['id'],args)['proposal'] is None
    assert generation.call_count==2
    assert d.execute('SELECT reserved_tokens FROM saas_ai_agent_token_usage WHERE run_id=%s::uuid',(first['request']['id'],))[0]['reserved_tokens']>0


def test_improve_deadline_blocks_proposal(ready):
    d,generation,data=ready;value=candidate(ready)
    def late(*a,**kw):
        d.execute("UPDATE saas_knowledge_extractions SET deadline=now()-interval '1 second'");d.commit()
        return json.dumps({'title':'Late','symptoms':['Symptom']}),{'total_tokens':20}
    generation.side_effect=late
    result=improve.improve(PROFILE,value['id'],payload(value))
    assert result['request']['status']=='failed' and result['proposal'] is None
    assert store.detail(PROFILE,value['id'])['candidate']['version']==1


def test_improve_api_origin_identity_and_dispatch(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import Mock
    import tenant_knowledge_candidates_api as api
    async def scenario():
        operation=Mock(return_value={'proposal':None,'requires_explicit_save':True})
        monkeypatch.setattr(api,'improve',operation)
        data={'expected_tenant_id':A,'expected_user_id':11,'request_id':str(uuid.uuid4()),'expected_version':1,'language':'az'}
        async def body():return data
        request=SimpleNamespace(method='POST',content_type='application/json',headers={'Origin':'https://test.local'},json=body,match_info={'candidate_id':'candidate'})
        handle=api.handler(lambda _:PROFILE,'https://test.local',Mock(),'improve')
        response=await handle(request)
        assert response.status==200 and response.headers['Cache-Control']=='no-store'
        assert operation.call_args.args[1]=='candidate' and operation.call_args.args[2]['language']=='az'
        assert 'expected_tenant_id' not in operation.call_args.args[2]
        request.headers={'Origin':'https://foreign.local'}
        assert (await handle(request)).status==403
        request.headers={'Origin':'https://test.local'};data['expected_tenant_id']=B
        assert (await handle(request)).status==409
        assert operation.call_count==1
    asyncio.run(scenario())


def test_legacy_extraction_request_id_replay_remains_free(ready):
    d,generation,data=ready
    first=service.analyze(PROFILE,data)
    legacy={**data,'provider':'connect','history_mode':'channel'};legacy.pop('request_id')
    d.execute('UPDATE saas_knowledge_extractions SET request_key=%s WHERE tenant_id=%s::uuid AND id=%s::uuid',
        (store.digest(legacy),A,first['request']['id']));d.commit()
    assert service.analyze(PROFILE,data)['request']['id']==first['request']['id']
    generation.assert_called_once()


def test_ai_message_cannot_supply_performed_operator_actions(ready):
    d,generation,data=ready
    d.execute("UPDATE saas_crm_messages SET author_name='AI Agent' WHERE external_id='response-0'");d.commit()
    raw=model_card();raw.update(resolution_method='remote',operator_actions=['Проверьте соединение и повторите синхронизацию'])
    generation.return_value=(json.dumps({'cards':[raw]}),{'total_tokens':20})
    value=candidate(ready)
    assert value['operator_actions']==[] and not value['can_approve']


def test_clarification_note_persists_without_changing_source_evidence(ready):
    value=candidate(ready);before=store.detail(PROFILE,value['id'])
    note='Confirm that the customer can reconnect after the restart.'
    changed=store.mutate(PROFILE,value['id'],{'expected_version':1,'clarification_note':note},'clarify')['candidate']
    reopened=store.detail(PROFILE,value['id'])
    assert changed['status']=='needs_clarification' and changed['clarification_note']==note
    assert reopened['candidate']['clarification_note']==note
    assert [item['id'] for item in reopened['evidence']]==[item['id'] for item in before['evidence']]
    assert not d_has_publications(ready)


def d_has_publications(ready):
    db,_,_=ready
    return bool(db.execute('SELECT id FROM saas_ai_knowledge_sources'))


def test_evidence_uses_existing_customer_and_multiple_employee_names(ready):
    db,_,_=ready
    db.execute("UPDATE saas_connect_conversations SET contact_name='Nigar' WHERE tenant_id=%s::uuid AND conversation_id='client'",(PROFILE['tenant_id'],))
    db.execute("UPDATE saas_crm_messages SET author_name='Leyla' WHERE external_id='response-0'")
    db.execute("UPDATE saas_crm_messages SET direction='outgoing',author_name='Kamran' WHERE external_id='response-1'")
    db.commit()
    value=candidate(ready);evidence=store.detail(PROFILE,value['id'])['evidence']
    roles_names={(item['role'],item['sender_name']) for item in evidence}
    assert ('customer','Nigar') in roles_names
    assert ('operator','Leyla') in roles_names
    assert ('operator','Kamran') in roles_names
    assert len({item['id'] for item in evidence})==len(evidence)
