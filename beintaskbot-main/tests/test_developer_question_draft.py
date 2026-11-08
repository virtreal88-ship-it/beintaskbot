"""Selected developer questions: isolated SQL and mocked AI only."""
import json
import uuid
from unittest.mock import Mock
import pytest
import tenant_developer_question_draft as question
import tenant_dialog_consultations as flow
import tenant_operator_copilot as copilot
from tenant_linear_policy import TenantLinearError
from test_assigned_operator_copilot import ready
from test_tenant_support_inbox import database, db, A, B, CA, AGENT, PROFILE, manual


@pytest.fixture
def setup(ready, monkeypatch):
    local, other, generate, _ = ready
    flow.schema(local)
    monkeypatch.setattr(flow, '_connect', lambda: local)
    monkeypatch.setattr(question, '_connect', lambda: local)
    monkeypatch.setattr('tenant_episode_media.prepare', Mock(side_effect=AssertionError('No media extraction')))
    monkeypatch.setattr(copilot, '_read_history', Mock(side_effect=AssertionError('No full history')))
    monkeypatch.setattr('tenant_ai_agent_run_store.knowledge_context', Mock(side_effect=AssertionError('No unrelated knowledge')))
    def respond(*args, **kwargs):
        kwargs['before_dispatch']({})
        return 'Could you check the reported connectivity issue?', {'total_tokens': 10}
    generate.side_effect = respond
    return local, other, generate


def payload(case_id=None):
    route = {'channel_id': CA, 'conversation_id': 'client'}
    if case_id: route['case_id'] = case_id
    messages = flow.preview(PROFILE, route)['messages']
    assert all('raw' not in item and 'external_id' not in item for item in messages)
    return {**route, 'request_id': str(uuid.uuid4()), 'message_ids': [item['id'] for item in messages],
            'media_ids': [], 'summary': 'Please investigate the symptom.',
            'source_versions': {item['id']: item['source_version'] for item in messages}}


def add_media(local, kind='image'):
    key = str(uuid.uuid4())
    local.execute("""INSERT INTO saas_crm_messages(tenant_id,id,kommo_lead_id,external_id,channel,direction,body,message_type,raw,happened_at)
        VALUES(%s::uuid,%s::uuid,41,%s,%s,'incoming','UNVERIFIED LEGACY BODY',%s,'{"mediaId":"v1"}',now()+interval '1 second')""",
        (A,key,'connect:'+key,'connect:'+CA,kind))
    local.commit()
    return key


def test_selected_only_draft_reuses_existing_ledger_and_never_sends(setup):
    local, _, generate = setup
    data = payload()
    local.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,happened_at) VALUES(%s::uuid,%s::uuid,41,'later',%s,'incoming','UNSELECTED SECRET',now()+interval '2 seconds')", (str(uuid.uuid4()),A,'connect:'+CA));local.commit()
    result = question.question(PROFILE, data)
    assert result['developer_question_draft'] and result['customer_message_sent'] is False
    assert question.question(PROFILE, {**data, 'request_id': str(uuid.uuid4())}) == result
    assert generate.call_count == 1
    args, kwargs = generate.call_args
    assert args[1] == 'agent-model'
    assert [row['text'] for row in args[3]] == ['Internet is down']
    assert kwargs['images'] == [] and 'ASSIGNED_AGENT_RULES' in kwargs['instructions']
    assert 'UNSELECTED SECRET' not in json.dumps([args,kwargs],default=str)
    for table in ('saas_internal_consultations','saas_public_case_shares','saas_consultation_group_deliveries','saas_operator_copilot_threads','saas_chat_send_receipts'):
        assert local.execute('SELECT count(*) AS n FROM '+table)[0]['n'] == 0
    assert local.execute('SELECT status,reserved_calls FROM saas_chat_ai_runs')[0] == {'status':'completed','reserved_calls':1}


@pytest.mark.parametrize('kind',['image','voice','audio','video','document'])
def test_missing_or_unselected_media_never_uses_legacy_body_or_downloads(setup,kind):
    local, _, generate = setup
    key = add_media(local,kind)
    data = payload();data['media_ids']=[key]
    result=question.question(PROFILE,data)
    assert result['context_usage']['missing_media']==[{'id':key,'kind':kind,'reason':'saved_evidence_unavailable'}]
    assert 'UNVERIFIED LEGACY BODY' not in json.dumps(generate.call_args.args[3])
    assert 'content unavailable' in generate.call_args.args[3][-1]['text']


@pytest.mark.parametrize('mutation',['partial','provenance','oversize','unselected','valid'])
def test_saved_media_provenance_complete_and_selected_only(setup,monkeypatch,mutation):
    local,_,generate=setup;key=add_media(local)
    import tenant_media_evidence as evidence
    from tenant_episode_media import reference
    row=local.execute('SELECT * FROM saas_crm_messages WHERE id=%s::uuid',(key,))[0]
    ref=reference(row,CA,41)
    saved={'status':'processed','coverage':{'complete':True},'provenance':{'source_version':evidence.source_hash(ref)},'result':{'observation':'SAVED PHOTO OBSERVATION'}}
    if mutation=='partial':saved['coverage']['complete']=False
    if mutation=='provenance':saved['provenance']['source_version']='wrong'
    if mutation=='oversize':saved['result']['observation']='Z'*4001
    reuse=Mock(return_value=saved);monkeypatch.setattr(evidence,'reusable',reuse)
    data=payload();data['media_ids']=[] if mutation=='unselected' else [key]
    result=question.question(PROFILE,data)
    text=json.dumps(generate.call_args.args[3])
    assert ('SAVED PHOTO OBSERVATION' in text)==(mutation=='valid')
    assert bool(result['context_usage']['missing_media'])==(mutation!='valid')
    if mutation=='unselected':reuse.assert_not_called()


@pytest.mark.parametrize('change',['edit','assignment','access','mode','consent','channel'])
def test_inflight_changes_hide_draft_and_never_retry_paid_call(setup,change):
    local, other, generate=setup;data=payload()
    def respond(*args,**kwargs):
        kwargs['before_dispatch']({})
        sql,params={
            'edit':("UPDATE saas_crm_messages SET body='edited' WHERE tenant_id=%s::uuid",(A,)),
            'assignment':("UPDATE saas_ai_conversation_state SET agent_id=%s::uuid,revision=revision+1 WHERE tenant_id=%s::uuid",(other,A)),
            'access':("UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s::uuid",(A,)),
            'mode':("UPDATE saas_ai_agents SET mode='automatic' WHERE tenant_id=%s::uuid",(A,)),
            'consent':("UPDATE saas_tenant_integrations SET metadata='{}',updated_at=now()+interval '1 second' WHERE tenant_id=%s::uuid",(A,)),
            'channel':("UPDATE saas_connect_channels SET active=FALSE WHERE tenant_id=%s::uuid",(A,)),
        }[change]
        local.execute(sql,params);local.commit()
        return 'STALE DRAFT',{'total_tokens':10}
    generate.side_effect=respond
    with pytest.raises(TenantLinearError):question.question(PROFILE,data)
    with pytest.raises(TenantLinearError):question.question(PROFILE,{**data,'request_id':str(uuid.uuid4())})
    assert generate.call_count==1
    assert local.execute('SELECT status FROM saas_chat_ai_runs')[0]['status']!='completed'


def test_timeout_new_client_uuid_cannot_repurchase_unknown(setup):
    local,_,generate=setup;data=payload();generate.side_effect=TimeoutError()
    with pytest.raises(TenantLinearError):question.question(PROFILE,data)
    with pytest.raises(TenantLinearError):question.question(PROFILE,{**data,'request_id':str(uuid.uuid4())})
    assert generate.call_count==1
    assert local.execute('SELECT status FROM saas_chat_ai_runs')[0]['status']=='unknown'


@pytest.mark.parametrize('change',['tenant','missing_version','wrong_version','foreign_message','extra_key','duplicate','media_outside','automatic','budget'])
def test_bad_scope_or_policy_block_before_any_ai(setup,change):
    local,_,generate=setup;data=payload();profile=PROFILE
    if change=='tenant':profile={'tenant_id':B,'telegram_id':22}
    if change=='missing_version':data.pop('source_versions')
    if change=='wrong_version':data['source_versions']={data['message_ids'][0]:'0'*64}
    if change=='foreign_message':data['message_ids']=[str(uuid.uuid4())]
    if change=='extra_key':data['include_media']=True
    if change=='duplicate':data['message_ids']*=2
    if change=='media_outside':data['media_ids']=[str(uuid.uuid4())]
    if change=='automatic':local.execute("UPDATE saas_ai_agents SET mode='automatic' WHERE tenant_id=%s::uuid",(A,));local.commit()
    if change=='budget':
        local.execute("UPDATE saas_tenant_integrations SET metadata=jsonb_set(metadata,'{daily_limit}','1') WHERE tenant_id=%s::uuid",(A,))
        local.execute("INSERT INTO saas_chat_ai_runs(tenant_id,request_id,requested_by,lead_id,input_hash,config_version,status,reserved_calls) VALUES(%s::uuid,%s::uuid,11,41,'prior','v1','unknown',1)",(A,str(uuid.uuid4())));local.commit()
    with pytest.raises(TenantLinearError):question.question(profile,data)
    generate.assert_not_called()


def test_case_materials_not_full_history_and_closed_case_is_blocked(setup):
    local,_,generate=setup
    local.execute("ALTER TABLE saas_support_tickets ADD COLUMN IF NOT EXISTS history_scope TEXT NOT NULL DEFAULT 'legacy'");local.commit()
    case=manual()['ticket']['id'];data=payload(case)
    selected=data['message_ids'][0]
    local.execute("UPDATE saas_support_tickets SET history_scope='snapshot' WHERE id=%s::uuid",(case,))
    local.execute("INSERT INTO saas_support_ticket_messages(tenant_id,ticket_id,message_id,event_key) VALUES(%s::uuid,%s::uuid,%s::uuid,'selected') ON CONFLICT DO NOTHING",(A,case,selected));local.commit()
    result=question.question(PROFILE,data)
    assert result['question_binding']['selection']['case_id']==case
    local.execute("UPDATE saas_support_tickets SET status='resolved' WHERE id=%s::uuid",(case,));local.commit()
    with pytest.raises(TenantLinearError):question.question(PROFILE,data)
    assert generate.call_count==1


@pytest.mark.parametrize('stale', [False, True])
def test_edited_question_requires_explicit_prepare_and_stale_source_cannot_dispatch(setup,monkeypatch,stale):
    local,_,generate=setup;data=payload();result=question.question(PROFILE,data)
    local.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name,last_message_at) VALUES(%s::uuid,%s::uuid,'developer',99,'Developer',now())",(A,CA));local.commit()
    flow.developer_marker(PROFILE,{'channel_id':CA,'conversation_id':'developer','enabled':True})
    send={**data,'request_id':str(uuid.uuid4()),'question_draft_request_id':result['request_id'],
          'destination_channel_id':CA,'destination_conversation_id':'developer','summary':'Employee edited question','confirm':True,'reuse_existing':True}
    prepared=flow.prepare(PROFILE,send,'https://crm.test')
    assert local.execute('SELECT reason FROM saas_internal_consultations')[0]['reason']=='Employee edited question'
    assert local.execute('SELECT public_identity FROM saas_public_case_shares')[0]['public_identity']=={}
    assert flow.prepare(PROFILE,send,'https://crm.test')['created'] is False
    sender=Mock(return_value={'message_id':'mock-connect-id','external_message_id':'MOCK_PROVIDER'})
    monkeypatch.setattr('tenant_connect_transport.send_consultation_with_receipt',sender)
    monkeypatch.setattr('tenant_connect_transport.connect_route', lambda cur,tenant,lead,**kw: {'channel_id':CA,'conversation_id':'developer' if lead==99 else 'client','api_key':'synthetic'})
    if stale:
        local.execute("UPDATE saas_crm_messages SET body='EDITED' WHERE tenant_id=%s::uuid",(A,));local.commit()
        with pytest.raises(TenantLinearError):flow.dispatch(PROFILE,prepared['id'],send['request_id'])
        sender.assert_not_called()
    else:
        assert flow.dispatch(PROFILE,prepared['id'],send['request_id'])['state'] == 'sent'
        sender.assert_called_once()
        assert 'Employee edited question' in str(sender.call_args)

    assert generate.call_count==1

@pytest.mark.parametrize('change', ['assignment', 'config'])
def test_completed_stale_draft_explains_explicit_recovery(setup, change):
    local, other, generate = setup
    data = payload()
    question.question(PROFILE, data)
    if change == 'assignment':
        local.execute("UPDATE saas_ai_conversation_state SET agent_id=%s::uuid,revision=revision+1 WHERE tenant_id=%s::uuid", (other,A))
    else:
        local.execute("UPDATE saas_tenant_integrations SET updated_at=now()+interval '1 second' WHERE tenant_id=%s::uuid AND provider='chat_ai'", (A,))
    local.commit()
    with pytest.raises(TenantLinearError, match='Измените описание вопроса'):
        question.question(PROFILE, data)
    assert generate.call_count == 1
    revised = {**data, 'summary': 'Deliberately revised diagnostic question'}
    question.question(PROFILE, revised)
    assert generate.call_count == 2
