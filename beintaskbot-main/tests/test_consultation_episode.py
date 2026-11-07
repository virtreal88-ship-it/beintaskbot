"""Synthetic exact episode admission across dialogue/case/automatic entries."""
import uuid
import pytest
from test_dialog_consultations import database,db,ready,A,CA,CB,PROFILE,flow,TARGET
from test_tenant_support_inbox import B,AGENT
from tenant_linear_policy import TenantLinearError
from tenant_consultation_episode import existing,queued


def case_for(local,data,*,scope='selected',event='handoff:episode-one'):
    ident=str(uuid.uuid4())
    local.execute("INSERT INTO saas_support_tickets(tenant_id,id,channel_id,conversation_id,source,reason,description,history_scope) VALUES(%s::uuid,%s::uuid,%s::uuid,'client','manual','Synthetic','Synthetic',%s)",(A,ident,CA,scope))
    for key in data['message_ids']:
        local.execute('INSERT INTO saas_support_ticket_messages(tenant_id,ticket_id,message_id,event_key) VALUES(%s::uuid,%s::uuid,%s::uuid,%s)',(A,ident,key,event))
    local.commit()
    return ident


def test_case_first_header_returns_same_consultation_without_sending(ready):
    local,data,sender=ready;case=case_for(local,data)
    first=flow.prepare(PROFILE,{**data,'case_id':case},'https://crm.test')
    second=flow.prepare(PROFILE,{**data,'request_id':str(uuid.uuid4()),'reuse_existing':True},'https://crm.test')
    assert second['reused'] and second['id']==first['id']
    assert second['case_id']==case and second['request_id']==data['request_id']
    assert local.execute('SELECT count(*) AS n FROM saas_internal_consultations')[0]['n']==1
    assert sender.call_count==0


def test_header_first_late_case_does_not_migrate_permissions_or_send_again(ready):
    local,data,sender=ready
    first=flow.prepare(PROFILE,data,'https://crm.test')
    case=case_for(local,data)
    with pytest.raises(TenantLinearError,match='другом контексте'):
        flow.prepare(PROFILE,{**data,'request_id':str(uuid.uuid4()),'case_id':case},'https://crm.test')
    assert local.execute('SELECT case_id FROM saas_internal_consultations')[0]['case_id'] is None
    assert flow.prepare(PROFILE,data,'https://crm.test')['id']==first['id'], 'exact original retry survives later case creation'
    assert sender.call_count==0


@pytest.mark.parametrize('scope,event',[('snapshot','case-snapshot:old'),('conversation','handoff:old'),('legacy','case-snapshot:old'),('selected','support-material-picker:old'),('selected','case-snapshot:manual')])
def test_broad_or_material_membership_is_not_implicit_case_proof(ready,scope,event):
    local,data,sender=ready;case_for(local,data,scope=scope,event=event)
    with pytest.raises(TenantLinearError,match='неоднозначна'):
        flow.prepare(PROFILE,data,'https://crm.test')
    assert not sender.called
    assert local.execute('SELECT count(*) AS n FROM saas_internal_consultations')[0]['n']==0


def test_implicit_case_retains_owner_and_support_permissions(ready):
    local,data,sender=ready;case_for(local,data)
    with pytest.raises(TenantLinearError):flow.prepare({'tenant_id':A,'telegram_id':33},data,'https://crm.test')
    assert not sender.called


@pytest.mark.parametrize('state',['sending','pending','unknown'])
def test_uncertain_transport_survives_case_closure_and_different_entry(ready,state):
    local,data,sender=ready;case=case_for(local,data)
    flow.prepare(PROFILE,{**data,'case_id':case},'https://crm.test')
    local.execute('UPDATE saas_consultation_group_deliveries SET state=%s',(state,))
    local.execute("UPDATE saas_support_tickets SET status='resolved' WHERE id=%s::uuid",(case,));local.commit()
    with pytest.raises(TenantLinearError,match='другом контексте'):
        flow.prepare(PROFILE,{**data,'request_id':str(uuid.uuid4())},'https://crm.test')
    assert local.execute('SELECT count(*) AS n FROM saas_internal_consultations')[0]['n']==1
    assert not sender.called


def test_same_source_edited_question_is_not_silently_reused(ready):
    local,data,sender=ready;case=case_for(local,data)
    flow.prepare(PROFILE,{**data,'case_id':case},'https://crm.test')
    with pytest.raises(TenantLinearError,match='другим вопросом'):
        flow.prepare(PROFILE,{**data,'request_id':str(uuid.uuid4()),'summary':'Different question'},'https://crm.test')
    assert not sender.called


def test_different_recipient_is_not_fallback_or_silent_reuse(ready):
    local,data,sender=ready;case=case_for(local,data)
    flow.prepare(PROFILE,{**data,'case_id':case},'https://crm.test')
    other='120363499999999999@g.us'
    local.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name) VALUES(%s::uuid,%s::uuid,%s,199,'Other developer')",(A,CA,other));local.commit()
    flow.developer_marker(PROFILE,{'channel_id':CA,'conversation_id':other,'enabled':True})
    with pytest.raises(TenantLinearError,match='другой разработчик'):
        flow.prepare(PROFILE,{**data,'request_id':str(uuid.uuid4()),'destination_conversation_id':other},'https://crm.test')
    assert not sender.called


def test_disjoint_question_in_same_dialogue_stays_independent_and_partial_overlap_conflicts(ready):
    local,data,sender=ready
    flow.prepare(PROFILE,data,'https://crm.test')
    key=str(uuid.uuid4())
    local.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,body,happened_at) VALUES(%s::uuid,%s::uuid,41,'connect:separate','incoming',%s,'Another question',now()+interval '1 minute')",(key,A,'connect:'+CA));local.commit()
    second=flow.prepare(PROFILE,{**data,'request_id':str(uuid.uuid4()),'message_ids':[key],'summary':'Unrelated new question'},'https://crm.test')
    assert second['created']
    # Remove the synthetic second reservation only in this isolated fixture so
    # the partial-overlap proof is unambiguous, without altering real state.
    local.execute("UPDATE saas_consultation_group_deliveries SET state='blocked' WHERE consultation_id=%s::uuid",(second['id'],));local.commit()
    with pytest.raises(TenantLinearError,match='частично пересекается'):
        flow.prepare(PROFILE,{**data,'request_id':str(uuid.uuid4()),'message_ids':data['message_ids']+[key]},'https://crm.test')
    with local.cursor() as cur:
        assert existing(cur,B,CA,'client',data['message_ids'],None,CA,TARGET,data['summary'],[],False,{}) is None
    assert not sender.called


def test_automatic_intent_only_blocks_its_original_causal_episode(ready):
    local,data,_=ready;case=case_for(local,data)
    request=str(uuid.uuid4())
    local.execute("INSERT INTO saas_ai_case_developer_dispatches(tenant_id,id,case_id,agent_id,event_key,destination_key,channel_id,conversation_id,owner_id,request_id) VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,'episode-one','target',%s::uuid,%s,11,%s::uuid)",(A,uuid.uuid4(),case,AGENT,CA,TARGET,request));local.commit()
    with local.cursor() as cur:
        assert queued(cur,A,case,str(uuid.uuid4()),data['message_ids'])
        assert not queued(cur,A,case,str(uuid.uuid4()),[str(uuid.uuid4())])
        assert not queued(cur,B,case,str(uuid.uuid4()),data['message_ids'])
    with pytest.raises(TenantLinearError,match='автоматический запрос'):
        flow.prepare(PROFILE,data,'https://crm.test')


def test_case_entry_also_opt_in_reuses_exact_existing_record(ready):
    local,data,sender=ready;case=case_for(local,data)
    first=flow.prepare(PROFILE,{**data,'case_id':case},'https://crm.test')
    again=flow.prepare(PROFILE,{**data,'case_id':case,'request_id':str(uuid.uuid4()),'reuse_existing':True},'https://crm.test')
    assert again['reused'] and again['id']==first['id'] and not sender.called
    with pytest.raises(TenantLinearError):
        flow.prepare(PROFILE,{**data,'reuse_existing':'true'},'https://crm.test')

def test_reused_employee_reference_cannot_authorize_legacy_fanout(ready):
    local,data,sender=ready;case=case_for(local,data)
    manual=flow.prepare(PROFILE,{**data,'case_id':case},'https://crm.test')
    other_request=str(uuid.uuid4())
    local.execute("INSERT INTO saas_ai_case_developer_dispatches(tenant_id,id,case_id,agent_id,event_key,destination_key,channel_id,conversation_id,owner_id,request_id,consultation_id,state) VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,'episode-one','target',%s::uuid,%s,11,%s::uuid,%s::uuid,'blocked')",(A,uuid.uuid4(),case,AGENT,CA,TARGET,other_request,manual['id']));local.commit()
    with local.cursor() as cur:
        with pytest.raises(TenantLinearError,match='другой разработчик'):
            existing(cur,A,CA,'client',data['message_ids'],case,CA,'other@g.us',data['summary'],[],False,{},
                auto={'legacy_multiple':True,'event_key':'episode-one','request_id':str(uuid.uuid4())})
    assert not sender.called


def test_answered_auto_intent_does_not_block_later_case_question_forever(ready):
    local,data,_=ready;case=case_for(local,data)
    manual=flow.prepare(PROFILE,{**data,'case_id':case},'https://crm.test')
    local.execute("INSERT INTO saas_ai_case_developer_dispatches(tenant_id,id,case_id,agent_id,event_key,destination_key,channel_id,conversation_id,owner_id,request_id,consultation_id,state) VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,'episode-one','target',%s::uuid,%s,11,%s::uuid,%s::uuid,'sent')",(A,uuid.uuid4(),case,AGENT,CA,TARGET,data['request_id'],manual['id']))
    local.execute("UPDATE saas_internal_consultations SET status='answered' WHERE id=%s::uuid",(manual['id'],));local.commit()
    with local.cursor() as cur:assert not queued(cur,A,case,str(uuid.uuid4()),data['message_ids'])


def test_reuse_preference_is_not_content_or_retry_identity(ready):
    local,data,_=ready
    first=flow.prepare(PROFILE,{**data,'reuse_existing':True},'https://crm.test')
    assert flow.prepare(PROFILE,{**data,'reuse_existing':False},'https://crm.test')['id']==first['id']
    assert flow.prepare(PROFILE,data,'https://crm.test')['id']==first['id']
    local.execute("UPDATE saas_internal_consultations SET status='answered'")
    local.execute("UPDATE saas_consultation_group_deliveries SET state='sent'");local.commit()
    with pytest.raises(TenantLinearError,match='уже подготовлен или отправлен'):
        flow.prepare(PROFILE,{**data,'request_id':str(uuid.uuid4()),'reuse_existing':False},'https://crm.test')
