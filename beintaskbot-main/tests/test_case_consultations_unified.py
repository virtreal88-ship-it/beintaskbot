"""Unified case consultation regressions: isolated SQL and synthetic receipts only."""
import json
import uuid
from unittest.mock import Mock
import pytest
from test_tenant_support_inbox import database,db,A,B,CA,CB,AGENT,PROFILE,manual
from test_dialog_consultations import ready,TARGET
import tenant_dialog_consultations as flow
import tenant_dialog_consultations_worker as worker
import tenant_consultation_group as legacy
import tenant_internal_consultations as internal
import tenant_connect_transport as transport
import tenant_public_case as public
import tenant_support_inbox_store as inbox_store
from tenant_linear_policy import TenantLinearError


@pytest.fixture
def case_ready(ready,monkeypatch):
    local,data,sender=ready
    for module in (internal,legacy,worker):monkeypatch.setattr(module,'_connect',lambda:local)
    # PR1 installs this column; retain a direct compatible setup for this PR's
    # independent fixture before the material-scope migration is cherry-picked.
    local.execute("ALTER TABLE saas_support_tickets ADD COLUMN IF NOT EXISTS history_scope TEXT NOT NULL DEFAULT 'legacy'")
    local.commit()
    case=manual()['ticket']['id']
    local.execute("INSERT INTO saas_ai_group_coordination(tenant_id,group_jid,state,reason) VALUES(%s::uuid,%s,'human','fixture') ON CONFLICT DO NOTHING",(A,TARGET))
    local.commit()
    return local,{**data,'case_id':case},sender


def test_case_prepare_public_snapshot_and_owner_gate_no_agent_required(case_ready):
    local,data,sender=case_ready
    local.execute('UPDATE saas_ai_conversation_state SET agent_id=NULL WHERE tenant_id=%s::uuid',(A,));local.commit()
    result=flow.prepare(PROFILE,data,'https://crm.test')
    assert flow.prepare(PROFILE,data,'https://crm.test')['id']==result['id']
    assert flow.dispatch(PROFILE,result['id'],data['request_id'])['state']=='sent'
    assert flow.dispatch(PROFILE,result['id'],data['request_id'])['state']=='sent'
    assert sender.call_count==1
    share=local.execute('SELECT ticket_id,consultation_id,token_hash FROM saas_public_case_shares')[0]
    assert share['ticket_id'] is None and str(share['consultation_id'])==result['id']
    assert local.execute('SELECT count(*) AS n FROM saas_support_tickets')[0]['n']==1
    thread=flow.inbox(PROFILE,case_id=data['case_id'])
    assert thread['consultations'][0]['case_id']==data['case_id']
    assert thread['consultations'][0]['provider_message_id']=='PROVIDER_123'
    with pytest.raises(TenantLinearError):flow.prepare({'tenant_id':A,'telegram_id':33},{**data,'request_id':str(uuid.uuid4())},'https://crm.test')


def test_old_share_scope_hash_without_identity_flag_still_blocks_duplicate(case_ready):
    import hashlib
    local,data,_=case_ready
    result=flow.prepare(PROFILE,data,'https://crm.test')
    old_scope={**data,'channel_id':data['channel_id'],'destination_channel_id':data['destination_channel_id'],
      'message_ids':sorted(data['message_ids']),'media_ids':[],'summary':data['summary'].strip()}
    old_scope.pop('request_id');old_scope.pop('include_customer_identity',None)
    legacy_hash=hashlib.sha256(json.dumps(old_scope,sort_keys=True).encode()).hexdigest()
    local.execute("UPDATE saas_internal_consultations SET evidence=jsonb_set(evidence,'{scope_hash}',to_jsonb(%s::text)) WHERE tenant_id=%s::uuid AND id=%s::uuid",(legacy_hash,A,result['id']))
    local.commit()
    with pytest.raises(TenantLinearError,match='уже подготовлен или отправлен'):
      flow.prepare(PROFILE,{**data,'request_id':str(uuid.uuid4())},'https://crm.test')


def test_case_public_selection_cannot_escape_material_scope(case_ready):
    local,data,_=case_ready
    chosen=data['message_ids'][0]
    local.execute("UPDATE saas_support_tickets SET history_scope='snapshot' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,data['case_id']))
    local.execute("INSERT INTO saas_support_ticket_messages(tenant_id,ticket_id,message_id,event_key) VALUES(%s::uuid,%s::uuid,%s::uuid,'case-snapshot') ON CONFLICT DO NOTHING",(A,data['case_id'],chosen))
    outside=str(uuid.uuid4())
    local.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,body) VALUES(%s::uuid,%s::uuid,41,'outside','incoming',%s,'Unrelated new problem')",(outside,A,'connect:'+CA));local.commit()
    with pytest.raises(TenantLinearError):flow.prepare(PROFILE,{**data,'message_ids':[outside]},'https://crm.test')
    preview=flow.preview(PROFILE,{k:data[k] for k in ('case_id','channel_id','conversation_id')})
    assert [m['id'] for m in preview['messages']]==[chosen]
    assert all('raw' not in m and 'media_url' not in m for m in preview['messages'])
    result=flow.prepare(PROFILE,data,'https://crm.test')
    assert result['state']=='ready'
    assert local.execute('SELECT selected_message_ids FROM saas_public_case_shares')[0]['selected_message_ids']==[chosen]


def test_case_pending_receipt_reconciles_manual_reply_into_confirmable_draft_queue(case_ready):
    local,data,sender=case_ready
    sender.return_value={'state':'pending','message_id':'case-queue','external_message_id':None}
    result=flow.prepare(PROFILE,data,'https://crm.test')
    assert flow.dispatch(PROFILE,result['id'],data['request_id'])['state']=='pending'
    assert flow.dispatch(PROFILE,result['id'],data['request_id'])['state']=='pending'
    local.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,body,raw,author_name) VALUES(%s::uuid,%s::uuid,99,'connect:late-case-reply','incoming',%s,'Replace cable',%s::jsonb,'Developer')",(uuid.uuid4(),A,'connect:'+CA,'{"id":"late-case-reply","quotedExternalMessageId":"PROVIDER_CASE"}'));local.commit()
    assert worker.reconcile()==0
    with local.cursor() as cur:
        assert flow.observe_outbound_receipt(cur,A,CA,TARGET,{'_crm_signed_outbound_receipt':True,'fromMe':True,'id':'case-queue','externalMessageId':'PROVIDER_CASE','originClientMessageId':data['request_id']})
    local.commit()
    assert worker.reconcile()==1
    assert worker.reconcile()==0
    reply=flow.inbox(PROFILE,case_id=data['case_id'])['replies'][0]
    assert reply['author']=='Developer' and reply['body']=='Replace cable'
    assert reply['draft_state']=='pending'
    assert 'support_ticket_id='+data['case_id'] in reply['url']
    pending=worker.pending();assert len(pending)==1 and pending[0]['id']==reply['id']
    assert sender.call_count==1
    assert local.execute('SELECT count(*) AS n FROM saas_tenant_notifications')[0]['n']==1


@pytest.mark.asyncio
async def test_manual_case_reply_generates_employee_reviewable_draft_without_customer_send(case_ready,monkeypatch):
    local,data,sender=case_ready
    monkeypatch.setattr(transport,'send_ai_reply',lambda *_a,**_k:pytest.fail('Manual mode sent to the customer'))
    monkeypatch.setattr(transport,'send_ai_reply_if_active',lambda *_a,**_k:pytest.fail('Manual mode sent to the customer'))
    result=flow.prepare(PROFILE,data,'https://crm.test');flow.dispatch(PROFILE,result['id'],data['request_id'])
    local.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,body,message_type,raw) VALUES(%s::uuid,%s::uuid,99,'connect:manual-mode-reply','incoming',%s,'Завтра посмотрю','text',%s::jsonb)",(uuid.uuid4(),A,'connect:'+CA,json.dumps({'id':'manual-mode-reply','quotedExternalMessageId':'PROVIDER_123'})));local.commit()
    with local.cursor() as cur:
        reply_id=flow.record_reply(cur,A,CA,TARGET,'manual-mode-reply','Завтра посмотрю','PROVIDER_123',
            author='Synthetic Developer',source_external_id='connect:manual-mode-reply')
    local.commit();assert reply_id
    assert len(worker.pending())==1
    calls=[]
    def reviewed_draft(profile,rid,request):
        calls.append((profile,rid,request))
        local.execute("UPDATE saas_dialog_consultation_replies SET draft_state='completed',draft='Accepted; developer plans to review tomorrow.' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,rid));local.commit()
        return {'customer_message_sent':False,'text':'Accepted; developer plans to review tomorrow.'}
    monkeypatch.setattr(flow,'draft',reviewed_draft)
    import logging
    await worker.deliver(logging.getLogger('case-draft-test'))
    row=local.execute('SELECT draft_state,draft FROM saas_dialog_consultation_replies WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,reply_id))[0]
    assert row=={'draft_state':'completed','draft':'Accepted; developer plans to review tomorrow.'}
    assert calls and sender.call_count==1,'only the existing developer dispatch occurred; no customer send was attempted'
    assert local.execute("SELECT count(*) AS n FROM saas_tenant_notifications WHERE event_key='dialog_consultation:'||%s",(reply_id,))[0]['n']==1


def test_case_manual_draft_revalidates_open_case_and_assignment(case_ready,monkeypatch):
    local,data,sender=case_ready;destination='synthetic-manual-developer'
    local.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name) VALUES(%s::uuid,%s::uuid,%s,9101,'Synthetic Developer')",(A,CA,destination));local.commit()
    flow.developer_marker(PROFILE,{'channel_id':CA,'conversation_id':destination,'enabled':True})
    local.execute("UPDATE saas_support_tickets SET assigned_developer_channel_id=%s::uuid,assigned_developer_conversation_id=%s WHERE tenant_id=%s::uuid AND id=%s::uuid",(CA,destination,A,data['case_id']));local.commit()
    monkeypatch.setattr(transport,'connect_route',lambda _cur,_tenant,lead,**_kw:{'channel_id':CA,'conversation_id':destination if lead==9101 else 'client','api_key':'synthetic'})
    request={'request_id':str(uuid.uuid4()),'case_id':data['case_id'],'channel_id':CA,'conversation_id':'client',
      'destination_channel_id':CA,'destination_conversation_id':destination,'message_ids':data['message_ids'],
      'media_ids':[],'summary':'Synthetic case summary','confirm':True,'include_customer_identity':False}
    result=flow.prepare(PROFILE,request,'https://crm.test');assert flow.dispatch(PROFILE,result['id'],request['request_id'])['state']=='sent'
    local.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,body,message_type,raw) VALUES(%s::uuid,%s::uuid,9101,'connect:manual-case-reply','incoming',%s,'Developer update','text',%s::jsonb)",(uuid.uuid4(),A,'connect:'+CA,json.dumps({'id':'manual-case-reply','quotedExternalMessageId':'PROVIDER_123'})));local.commit()
    with local.cursor() as cur:reply_id=flow.record_reply(cur,A,CA,destination,'manual-case-reply','Developer update','PROVIDER_123',author='Synthetic Developer',source_external_id='connect:manual-case-reply')
    local.commit();assert reply_id
    import tenant_operator_copilot as copilot
    mock=Mock(return_value={'text':'Employee approved draft text'});monkeypatch.setattr(copilot,'run',mock)
    assert flow.draft(PROFILE,reply_id,{'request_id':str(uuid.uuid4())})=={'text':'Employee approved draft text','customer_message_sent':False}
    assert sender.call_count==1 and mock.call_count==1
    # The case and recipient can change while Copilot is generating. A stale
    # draft is discarded at persistence time and never becomes a send action.
    local.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,body,message_type,raw) VALUES(%s::uuid,%s::uuid,9101,'connect:manual-case-reply-2','incoming',%s,'Second developer update','text',%s::jsonb)",(uuid.uuid4(),A,'connect:'+CA,json.dumps({'id':'manual-case-reply-2','quotedExternalMessageId':'PROVIDER_123'})));local.commit()
    with local.cursor() as cur:reply_two=flow.record_reply(cur,A,CA,destination,'manual-case-reply-2','Second developer update','PROVIDER_123',author='Synthetic Developer',source_external_id='connect:manual-case-reply-2')
    local.commit();assert reply_two
    def close_during_generation(*_args,**_kwargs):
        local.execute("UPDATE saas_support_tickets SET status='resolved' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,data['case_id']));local.commit()
        return {'text':'Stale close draft'}
    monkeypatch.setattr(copilot,'run',close_during_generation)
    with pytest.raises(TenantLinearError):flow.draft(PROFILE,reply_two,{'request_id':str(uuid.uuid4())})
    assert local.execute("SELECT draft_state FROM saas_dialog_consultation_replies WHERE id=%s::uuid",(reply_two,))[0]['draft_state']!='completed'
    local.execute("UPDATE saas_support_tickets SET status='new' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,data['case_id']));local.commit()
    local.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,body,message_type,raw) VALUES(%s::uuid,%s::uuid,9101,'connect:manual-case-reply-3','incoming',%s,'Third developer update','text',%s::jsonb)",(uuid.uuid4(),A,'connect:'+CA,json.dumps({'id':'manual-case-reply-3','quotedExternalMessageId':'PROVIDER_123'})));local.commit()
    with local.cursor() as cur:reply_three=flow.record_reply(cur,A,CA,destination,'manual-case-reply-3','Third developer update','PROVIDER_123',author='Synthetic Developer',source_external_id='connect:manual-case-reply-3')
    local.commit();assert reply_three
    def reassign_during_generation(*_args,**_kwargs):
        local.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name) VALUES(%s::uuid,%s::uuid,'changed-developer',9102,'Changed developer')",(A,CA))
        local.execute("UPDATE saas_support_tickets SET assigned_developer_conversation_id='changed-developer' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,data['case_id']));local.commit()
        return {'text':'Stale reassignment draft'}
    monkeypatch.setattr(copilot,'run',reassign_during_generation)
    with pytest.raises(TenantLinearError):flow.draft(PROFILE,reply_three,{'request_id':str(uuid.uuid4())})
    assert sender.call_count==1


def test_resolution_requires_explicit_command_quote_and_assigned_developer(case_ready):
    local,data,sender=case_ready
    local.execute('''UPDATE saas_support_tickets SET assigned_developer_channel_id=%s::uuid,
      assigned_developer_conversation_id=%s WHERE tenant_id=%s::uuid AND id=%s::uuid''',
      (CA,TARGET,A,data['case_id']));local.commit()
    result=flow.prepare(PROFILE,data,'https://crm.test')
    flow.dispatch(PROFILE,result['id'],data['request_id'])
    import tenant_ai_group_coordination as group_coordination
    group_coordination.create_schema(local)
    local.execute("INSERT INTO saas_ai_group_staff(tenant_id,channel_id,jid,member_id,verified_by) VALUES(%s::uuid,%s::uuid,'15550001111@s.whatsapp.net',11,11) ON CONFLICT DO NOTHING",(A,CA))
    local.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,body,message_type,raw) VALUES(%s::uuid,%s::uuid,99,'connect:ordinary-reply','incoming',%s,'Завтра посмотрю','text',%s::jsonb)",(uuid.uuid4(),A,'connect:'+CA,json.dumps({'id':'ordinary-reply','participantJid':'15550001111@s.whatsapp.net'})));local.commit()
    with local.cursor() as cur:
        ordinary=flow.record_reply(cur,A,CA,TARGET,'ordinary-reply','Завтра посмотрю','PROVIDER_123',author='Developer',source_external_id='connect:ordinary-reply',source_actor=11)
    local.commit()
    assert ordinary and local.execute('SELECT status FROM saas_support_tickets WHERE id=%s::uuid',(data['case_id'],))[0]['status']=='new'
    with local.cursor() as cur:
        assert flow.record_reply(cur,A,CA,TARGET,'unquoted-done','/done',author='Developer') is None
    local.commit()
    local.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,body,message_type,raw) VALUES(%s::uuid,%s::uuid,99,'connect:explicit-done','incoming',%s,'/done','text',%s::jsonb)",(uuid.uuid4(),A,'connect:'+CA,json.dumps({'id':'explicit-done','participantJid':'15550001111@s.whatsapp.net'})));local.commit()
    with local.cursor() as cur:
        resolved=flow.record_reply(cur,A,CA,TARGET,'explicit-done','/done','PROVIDER_123',author='Developer',source_external_id='connect:explicit-done',source_actor=11)
    local.commit()
    assert resolved
    row=local.execute('SELECT status,completion_outcome FROM saas_support_tickets WHERE id=%s::uuid',(data['case_id'],))[0]
    assert row=={'status':'resolved','completion_outcome':'resolved_confirmed'}
    assert flow.record_reply(local.cursor(),A,CA,TARGET,'explicit-done','/done','PROVIDER_123',author='Developer') is None


def test_resolution_command_from_unassigned_destination_only_records_answer(case_ready):
    local,data,_=case_ready
    result=flow.prepare(PROFILE,data,'https://crm.test');flow.dispatch(PROFILE,result['id'],data['request_id'])
    with local.cursor() as cur:
        ident=flow.record_reply(cur,A,CA,TARGET,'unassigned-done','/решено','PROVIDER_123',author='Developer')
    local.commit()
    assert ident
    assert local.execute('SELECT status FROM saas_support_tickets WHERE id=%s::uuid',(data['case_id'],))[0]['status']=='new'


def test_developer_context_is_limited_to_unique_case_containing_current_message(case_ready,monkeypatch):
    local,data,_=case_ready
    monkeypatch.setattr(flow,'_connect',lambda:local)
    result=flow.prepare(PROFILE,data,'https://crm.test');flow.dispatch(PROFILE,result['id'],data['request_id'])
    import tenant_ai_group_coordination as coordination
    coordination.create_schema(local)
    local.execute("INSERT INTO saas_ai_group_staff(tenant_id,channel_id,jid,member_id,verified_by) VALUES(%s::uuid,%s::uuid,'15550001111@s.whatsapp.net',11,11) ON CONFLICT DO NOTHING",(A,CA))
    local.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,body,raw) VALUES(%s::uuid,%s::uuid,99,'connect:context-reply','incoming',%s,'Завтра посмотрю',%s::jsonb)",(uuid.uuid4(),A,'connect:'+CA,json.dumps({'id':'context-reply','quotedExternalMessageId':'PROVIDER_123','participantJid':'15550001111@s.whatsapp.net'})))
    with local.cursor() as cur:
        reply_id=flow.record_reply(cur,A,CA,TARGET,'context-reply','Завтра посмотрю','PROVIDER_123',author='Developer')
    assert reply_id
    local.commit()
    source=local.execute('SELECT external_id FROM saas_crm_messages WHERE id=%s::uuid',(data['message_ids'][0],))[0]['external_id']
    job={'provider':'connect','tenant_id':A,'channel_id':CA,'conversation_id':'client','payload':{
      'developer_case_followup':True,'case_id':data['case_id'],'developer_reply_message_id':reply_id}}
    context=flow.active_case_developer_context(job,source)
    assert len(context)==1 and context[0]['message']=='Завтра посмотрю'
    local.execute("UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s::uuid AND telegram_id=11",(A,));local.commit()
    assert flow.active_case_developer_context(job,source)==[]
    local.execute("UPDATE saas_tenant_members SET active=TRUE WHERE tenant_id=%s::uuid AND telegram_id=11",(A,));local.commit()
    job['payload']['developer_reply_message_id']=str(uuid.uuid4())
    assert flow.active_case_developer_context(job,source)==[]
    job['payload']['developer_reply_message_id']=reply_id
    job['payload']['case_id']=str(uuid.uuid4())
    assert flow.active_case_developer_context(job,source)==[]
    job['payload']['case_id']=data['case_id']
    assert flow.active_case_developer_context(job,'unrelated-message')==[]


def test_verified_developer_reply_enqueues_one_automatic_client_followup(case_ready,monkeypatch):
    local,data,sender=case_ready
    monkeypatch.setenv('AI_AGENTS_ENABLED','1');monkeypatch.setenv('AI_AGENTS_AUTO_SEND_ENABLED','1')
    monkeypatch.setattr(flow,'_connect',lambda:local)
    direct='synthetic-developer-direct'
    local.execute("UPDATE saas_ai_conversation_state SET state='active',agent_id=%s::uuid WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id='client'",(AGENT,A,CA))
    local.execute("UPDATE saas_ai_agents SET mode='automatic',enabled=TRUE,scopes=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",(json.dumps([{'provider':'connect','channel_id':CA}]),A,AGENT))
    local.execute("UPDATE saas_ai_agent_settings SET enabled=TRUE,auto_send_enabled=TRUE WHERE tenant_id=%s::uuid",(A,))
    local.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name,last_message_at) VALUES(%s::uuid,%s::uuid,%s,199,'Synthetic Developer',now())",(A,CA,direct))
    flow.developer_marker(PROFILE,{'channel_id':CA,'conversation_id':direct,'enabled':True})
    local.execute('''UPDATE saas_support_tickets SET assigned_developer_channel_id=%s::uuid,
      assigned_developer_conversation_id=%s WHERE tenant_id=%s::uuid AND id=%s::uuid''',(CA,direct,A,data['case_id']))
    local.execute("UPDATE saas_crm_messages SET external_id='connect:m1' WHERE tenant_id=%s::uuid AND external_id='m1'",(A,));local.commit()
    selected={**data,'case_id':data['case_id'],'destination_conversation_id':direct}
    import tenant_connect_transport as connect_transport
    monkeypatch.setattr(connect_transport,'connect_route',lambda _cur,_tenant,lead,**_kw:{'channel_id':CA,'conversation_id':direct if lead==199 else 'client','api_key':'synthetic'})
    consultation=flow.prepare(PROFILE,selected,'https://crm.test')
    assert flow.dispatch(PROFILE,consultation['id'],selected['request_id'])['state']=='sent'
    local.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,body,message_type,raw) VALUES(%s::uuid,%s::uuid,199,'connect:developer-reply-1','incoming',%s,'Завтра посмотрю','text',%s::jsonb)",(uuid.uuid4(),A,'connect:'+CA,json.dumps({'id':'developer-reply-1','quotedExternalMessageId':'PROVIDER_123'})));local.commit()
    with local.cursor() as cur:
      reply_id=flow.record_reply(cur,A,CA,direct,'developer-reply-1','Завтра посмотрю','PROVIDER_123',author='Synthetic Developer',source_external_id='connect:developer-reply-1',fresh_followup=True)
    assert reply_id
    local.execute("UPDATE saas_ai_agent_jobs SET state='skipped'");local.commit()
    # Webhook/reply receipt is already stored, but its enqueue was lost. The
    # scheduled reconciler must recover it idempotently without provider POST.
    local.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,body,message_type,happened_at) VALUES(%s::uuid,%s::uuid,41,'connect:newer-client-message','incoming',%s,'A newer unrelated question','text',now()+interval '1 minute')",(uuid.uuid4(),A,'connect:'+CA));local.commit()
    worker.reconcile()
    assert local.execute("SELECT count(*) AS n FROM saas_tenant_webhook_events WHERE tenant_id=%s::uuid AND event_type='developer_case_reply'",(A,))[0]['n']==0
    local.execute("DELETE FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id='connect:newer-client-message'",(A,));local.commit()
    worker.reconcile();worker.reconcile()
    event=local.execute("SELECT event_type,fingerprint,payload::jsonb AS payload FROM saas_tenant_webhook_events WHERE tenant_id=%s::uuid AND event_type='developer_case_reply'",(A,))[0]
    job=local.execute("SELECT state,revision,event_fingerprint FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND provider='connect' AND conversation_id='client'",(A,))[0]
    assert event['payload']['developer_case_followup'] is True
    assert event['payload']['case_id']==data['case_id'] and event['payload']['developer_reply_message_id']==reply_id
    assert event['payload']['expected_conversation_revision']==1
    assert job['state']=='queued' and job['event_fingerprint']==event['fingerprint']
    assert local.execute("SELECT count(*) AS n FROM saas_tenant_webhook_events WHERE tenant_id=%s::uuid AND event_type='developer_case_reply'",(A,))[0]['n']==1
    assert sender.call_count==1
    assert local.execute("SELECT count(*) AS n FROM saas_tenant_webhook_events WHERE tenant_id=%s::uuid AND event_type='developer_case_reply'",(A,))[0]['n']==1
    assert sender.call_count==1


def test_group_case_followup_inherits_only_current_admitted_source_fence(case_ready,monkeypatch):
    import hashlib
    import tenant_ai_agent_connect as connect_ai
    import tenant_ai_group_coordination as groups
    local,data,_sender=case_ready;monkeypatch.setenv('AI_AGENTS_ENABLED','1');monkeypatch.setenv('AI_AGENTS_AUTO_SEND_ENABLED','1')
    monkeypatch.setattr(flow,'_connect',lambda:local);monkeypatch.setattr(groups,'_connect',lambda:local);groups.create_schema(local)
    group='120363431037910651@g.us';direct='synthetic-developer-direct'
    local.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name,last_message_at,conversation_kind) VALUES(%s::uuid,%s::uuid,%s,299,'Synthetic customer group',now(),'group') ON CONFLICT DO NOTHING",(A,CA,group))
    local.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name,last_message_at) VALUES(%s::uuid,%s::uuid,%s,199,'Synthetic Developer',now()) ON CONFLICT DO NOTHING",(A,CA,direct))
    monkeypatch.setattr(transport,'connect_route',lambda _cur,_tenant,lead,**_kw:{'channel_id':CA,'conversation_id':direct if lead==199 else 'client','api_key':'synthetic'})
    local.execute("UPDATE saas_ai_conversation_state SET conversation_id=%s,state='active',agent_id=%s::uuid WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s",(group,AGENT,A,CA))
    local.execute("UPDATE saas_support_tickets SET assigned_developer_channel_id=%s::uuid,assigned_developer_conversation_id=%s WHERE tenant_id=%s::uuid AND id=%s::uuid",(CA,direct,A,data['case_id']))
    local.execute("UPDATE saas_ai_agents SET mode='automatic',enabled=TRUE,scopes=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",(json.dumps([{'provider':'connect','channel_id':CA,'allow_group_messages':True}]),A,AGENT))
    local.execute("UPDATE saas_ai_agent_settings SET enabled=TRUE,auto_send_enabled=TRUE WHERE tenant_id=%s::uuid",(A,))
    flow.developer_marker(PROFILE,{'channel_id':CA,'conversation_id':direct,'enabled':True})
    local.execute("UPDATE saas_crm_messages SET external_id='connect:m1' WHERE tenant_id=%s::uuid AND external_id='m1'",(A,))
    groups.configure(PROFILE,{'conversation_id':group,'expected_revision':0,'assignments':[{'agent_id':AGENT,'channel_id':CA,'priority':10}]})
    local.execute("UPDATE saas_ai_group_coordination SET state='active',reason='',resumed_at=now()-interval '5 minutes' WHERE tenant_id=%s::uuid AND group_jid=%s",(A,group))
    local.execute("UPDATE saas_ai_conversation_state SET state='active',agent_id=%s::uuid WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id=%s",(AGENT,A,CA,group))
    source=str(uuid.uuid4());source_id='new-group-source'
    local.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,body,message_type,created_at,happened_at) VALUES(%s::uuid,%s::uuid,41,%s,'incoming',%s,'New group issue','text',clock_timestamp(),clock_timestamp()+interval '1 second')",(source,A,'connect:'+source_id,'connect:'+CA))
    local.execute("INSERT INTO saas_support_ticket_messages(tenant_id,ticket_id,message_id,event_key) VALUES(%s::uuid,%s::uuid,%s::uuid,'new-group-source')",(A,data['case_id'],source))
    admission=groups.admit(local.cursor(),A,CA,group,source_id,source_id)
    assert admission
    source_fingerprint=hashlib.sha256(('connect:'+source_id).encode()).hexdigest()
    local.execute("INSERT INTO saas_tenant_webhook_events(tenant_id,fingerprint,provider,external_id,event_type,payload,state) VALUES(%s::uuid,%s,'connect',%s,'incoming_message',%s::jsonb,'completed') ON CONFLICT DO NOTHING",(A,source_fingerprint,source_id,json.dumps({'message_id':source_id,'channel_id':CA,'conversation_id':group,**admission})))
    local.commit()
    consultation=flow.prepare(PROFILE,{**data,'destination_conversation_id':direct},'https://crm.test')
    flow.dispatch(PROFILE,consultation['id'],data['request_id'])
    local.execute("UPDATE saas_support_tickets SET conversation_id=%s WHERE tenant_id=%s::uuid AND id=%s::uuid",(group,A,data['case_id']))
    local.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,body,message_type,raw) VALUES(%s::uuid,%s::uuid,199,'connect:developer-reply-group','incoming',%s,'We will check the cable','text',%s::jsonb)",(uuid.uuid4(),A,'connect:'+CA,json.dumps({'id':'developer-reply-group','quotedExternalMessageId':'PROVIDER_123'})))
    local.commit()
    with local.cursor() as cur:reply_id=flow.record_reply(cur,A,CA,direct,'developer-reply-group','We will check the cable','PROVIDER_123',author='Synthetic Developer',source_external_id='connect:developer-reply-group',fresh_followup=True)
    with local.cursor() as cur:assert connect_ai.enqueue_case_developer_followup(cur,A,reply_id,source_external_id='connect:developer-reply-group',quoted_message_id='PROVIDER_123')
    event=local.execute("SELECT payload::jsonb AS payload FROM saas_tenant_webhook_events WHERE tenant_id=%s::uuid AND event_type='developer_case_reply'",(A,))[0]['payload']
    assert event['group_event_key']==admission['group_event_key'] and event['group_revision']==admission['group_revision']
    local.execute("UPDATE saas_ai_group_coordination SET state='human',revision=revision+1 WHERE tenant_id=%s::uuid AND group_jid=%s",(A,group));local.commit()
    from tenant_ai_group_coordination import job_allowed
    assert not job_allowed(local.cursor(),{'provider':'connect','tenant_id':A,'channel_id':CA,'conversation_id':group,'agent_id':AGENT,'payload':event})


def test_auto_dispatch_reply_resolution_notification_full_offline_flow(case_ready,monkeypatch):
    local,data,sender=case_ready
    # Explicitly enable only the runtime gates inside this isolated SQL fixture.
    # No provider transport is reachable; the sender remains a spy.
    monkeypatch.setenv('AI_AGENTS_ENABLED','1')
    monkeypatch.setenv('AI_AGENTS_AUTO_SEND_ENABLED','1')
    local.execute("UPDATE saas_ai_conversation_state SET state='active',agent_id=%s::uuid WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id='client'",(AGENT,A,CA))
    local.execute("UPDATE saas_ai_agents SET scopes=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",(json.dumps([{'provider':'connect','channel_id':CA}]),A,AGENT))
    local.execute("UPDATE saas_support_tickets SET status='resolved' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,data['case_id']))
    local.execute("UPDATE saas_crm_messages SET external_id='connect:m1' WHERE tenant_id=%s::uuid AND external_id='m1'",(A,))
    second_target='120363411822115410@g.us'
    local.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name,last_message_at) VALUES(%s::uuid,%s::uuid,%s,98,'Synthetic Dev 2',now())",(A,CA,second_target))
    local.execute("INSERT INTO saas_ai_group_coordination(tenant_id,group_jid,state,reason) VALUES(%s::uuid,%s,'human','fixture') ON CONFLICT DO NOTHING",(A,second_target));local.commit()
    flow.developer_marker(PROFILE,{'channel_id':CA,'conversation_id':second_target,'enabled':True})
    config={'auto_case_dispatch_enabled':True,'auto_case_dispatch_owner_id':11,
      'developer_destinations':[{'channel_id':CA,'conversation_id':TARGET},{'channel_id':CA,'conversation_id':second_target}]}
    local.execute("UPDATE saas_ai_agents SET mode='automatic',response_policy=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",(json.dumps(config),A,AGENT))
    local.execute('UPDATE saas_ai_agent_settings SET auto_send_enabled=TRUE WHERE tenant_id=%s::uuid',(A,));local.commit()
    job={'tenant_id':A,'provider':'connect','agent_id':AGENT,'conversation_id':'client','chat_id':'client',
      'channel_id':CA,'lead_id':41,'event_fingerprint':'automatic-case-event','source_message_id':'m1',
      'case_message_ids':['m1']}
    with local.cursor() as cur:receipt=inbox_store.handoff_ticket(cur,job,'synthetic escalation')
    local.commit();assert receipt['created'] is True
    outboxes=local.execute("SELECT id::text FROM saas_ai_case_developer_dispatches WHERE case_id=%s::uuid ORDER BY destination_key",(receipt['id'],))
    assert len(outboxes)==2
    import tenant_connect_transport as connect_transport
    monkeypatch.setattr(connect_transport,'connect_route',lambda _cur,_tenant,lead,**_kw:{'channel_id':CA,'conversation_id':TARGET if lead==99 else second_target,'api_key':'synthetic'})
    for outbox in outboxes:worker.dispatch_auto_case(outbox['id'])
    assert sender.call_count==2, local.execute('SELECT state,reason FROM saas_ai_case_developer_dispatches WHERE case_id=%s::uuid',(receipt['id'],))
    rows=local.execute('SELECT state,consultation_id::text FROM saas_ai_case_developer_dispatches WHERE case_id=%s::uuid',(receipt['id'],))
    assert len(rows)==2 and all(row['state']=='sent' and row['consultation_id'] for row in rows)
    # A positive provider receipt can arrive after an earlier pending/unknown
    # projection. Reconciliation must mark sent without queueing another POST.
    late=local.execute('''SELECT x.id::text,x.request_id::text FROM saas_ai_case_developer_dispatches x
      WHERE x.case_id=%s::uuid ORDER BY x.destination_key LIMIT 1''',(receipt['id'],))[0]
    local.execute("UPDATE saas_ai_case_developer_dispatches SET state='unknown' WHERE id=%s::uuid",(late['id'],))
    local.execute("UPDATE saas_consultation_group_deliveries SET state='unknown' WHERE tenant_id=%s::uuid AND request_id=%s::uuid",(A,late['request_id']));local.commit()
    worker.reconcile_auto_dispatches()
    assert local.execute("SELECT state FROM saas_ai_case_developer_dispatches WHERE id=%s::uuid",(late['id'],))[0]['state']=='unknown'
    local.execute("UPDATE saas_consultation_group_deliveries SET state='sent' WHERE tenant_id=%s::uuid AND request_id=%s::uuid",(A,late['request_id']));local.commit()
    worker.reconcile_auto_dispatches()
    assert local.execute("SELECT state FROM saas_ai_case_developer_dispatches WHERE id=%s::uuid",(late['id'],))[0]['state']=='sent'
    assert all(row['id']!=late['id'] for row in worker.auto_dispatch_queue())
    assert sender.call_count==2
    # Auto fan-out recipients are correlated through their own sent receipts;
    # no single CRM assignee is required. Group senders must be tenant verified.
    import tenant_ai_group_coordination as group_coordination
    group_coordination.create_schema(local)
    local.execute("INSERT INTO saas_ai_group_staff(tenant_id,channel_id,jid,member_id,verified_by) VALUES(%s::uuid,%s::uuid,'15550001111@s.whatsapp.net',11,11) ON CONFLICT DO NOTHING",(A,CA))
    local.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,body,message_type,raw) VALUES(%s::uuid,%s::uuid,99,'connect:auto-answer','incoming',%s,'Завтра посмотрю','text',%s::jsonb)",(uuid.uuid4(),A,'connect:'+CA,json.dumps({'id':'auto-answer','participantJid':'15550001111@s.whatsapp.net','quotedExternalMessageId':'PROVIDER_123'})));local.commit()
    with local.cursor() as cur:
        answer=flow.record_reply(cur,A,CA,TARGET,'auto-answer','Завтра посмотрю','PROVIDER_123',author='Developer',source_external_id='connect:auto-answer',source_actor=11,fresh_followup=True)
    local.commit()
    assert answer and local.execute('SELECT status FROM saas_support_tickets WHERE id=%s::uuid',(receipt['id'],))[0]['status']!='resolved'
    assert local.execute('SELECT draft_state FROM saas_dialog_consultation_replies WHERE id=%s::uuid',(answer,))[0]['draft_state']=='not_requested'
    from tenant_ai_agent_connect import enqueue_case_developer_followup
    local.execute("UPDATE saas_ai_agent_jobs SET state='skipped'");local.commit()
    with local.cursor() as cur:
        assert enqueue_case_developer_followup(cur,A,answer,source_external_id='connect:auto-answer',quoted_message_id='PROVIDER_123')
    local.commit()
    followup=local.execute("SELECT event_type,fingerprint,payload::jsonb AS payload FROM saas_tenant_webhook_events WHERE tenant_id=%s::uuid AND event_type='developer_case_reply'",(A,))[0]
    assert followup['payload']['case_id']==receipt['id'] and followup['payload']['developer_reply_message_id']==answer
    assert followup['payload']['developer_case_followup'] is True
    assert local.execute("SELECT count(*) AS n FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND provider='connect' AND conversation_id='client' AND event_fingerprint=%s",(A,followup['fingerprint']))[0]['n']==1
    with local.cursor() as cur:assert flow.case_developer_followup_current(cur,A,followup['payload'])
    monkeypatch.setattr(transport,'_connect',lambda:local);monkeypatch.setattr(transport,'_schema',lambda _conn:None);monkeypatch.setattr(transport,'lock_dialog',lambda *_a,**_k:None)
    final_provider_send=Mock(return_value='must-not-send');monkeypatch.setattr(transport,'send_ai_reply',final_provider_send)
    local.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name) VALUES(%s::uuid,%s::uuid,'other-developer',9001,'Synthetic alternate developer')",(A,CB))
    local.execute("UPDATE saas_support_tickets SET assigned_developer_channel_id=%s::uuid,assigned_developer_conversation_id=%s WHERE tenant_id=%s::uuid AND id=%s::uuid",(CB,'other-developer',A,receipt['id']));local.commit()
    with local.cursor() as cur:assert not flow.case_developer_followup_current(cur,A,followup['payload'])
    assert transport.send_ai_reply_if_active(A,41,CA,'client','Synthetic follow-up',str(uuid.uuid4()),1,case_followup_binding=followup['payload']) is None
    local.execute("UPDATE saas_support_tickets SET assigned_developer_channel_id=NULL,assigned_developer_conversation_id=NULL WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,receipt['id']));local.commit()
    local.execute("UPDATE saas_support_tickets SET status='resolved' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,receipt['id']));local.commit()
    with local.cursor() as cur:assert not flow.case_developer_followup_current(cur,A,followup['payload'])
    assert transport.send_ai_reply_if_active(A,41,CA,'client','Synthetic follow-up',str(uuid.uuid4()),1,
        case_followup_binding=followup['payload']) is None
    assert not final_provider_send.called
    local.execute("UPDATE saas_support_tickets SET status='new' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,receipt['id']));local.commit()
    job['source_message_id']='connect:m1'
    assert flow.active_case_developer_context(job,'connect:m1')[0]['message']=='Завтра посмотрю'
    local.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,body,message_type,raw) VALUES(%s::uuid,%s::uuid,99,'connect:auto-done','incoming',%s,'/решено','text',%s::jsonb)",(uuid.uuid4(),A,'connect:'+CA,json.dumps({'id':'auto-done','participantJid':'15550001111@s.whatsapp.net'})));local.commit()
    with local.cursor() as cur:
        done=flow.record_reply(cur,A,CA,TARGET,'auto-done','/решено','PROVIDER_123',author='Developer',source_external_id='connect:auto-done',source_actor=11)
    local.commit()
    assert done and local.execute('SELECT status,completion_outcome FROM saas_support_tickets WHERE id=%s::uuid',(receipt['id'],))[0]=={'status':'resolved','completion_outcome':'resolved_confirmed'}
    assert local.execute("SELECT count(*) AS n FROM saas_tenant_notifications WHERE event_key='dialog_consultation:'||%s",(done,))[0]['n']==1


def test_legacy_case_marker_and_saved_reply_project_to_common_thread(case_ready):
    local,data,_=case_ready
    cid=str(uuid.uuid4());rid=str(uuid.uuid4())
    local.execute("INSERT INTO saas_internal_consultations(tenant_id,id,case_id,channel_id,conversation_id,request_key,reason,status,created_by) VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,'client',%s,'Old case','waiting_specialist',11)",(A,cid,data['case_id'],CA,'legacy:'+cid))
    local.execute("INSERT INTO saas_consultation_group_deliveries(tenant_id,consultation_id,request_id,case_id,channel_id,conversation_id,body,body_hash,state,created_by,provider_message_id) VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,'old','hash','sent',11,'OLD_PROVIDER')",(A,cid,rid,data['case_id'],CA,TARGET))
    local.commit()
    with local.cursor() as cur:
        reply=flow.record_reply(cur,A,CA,TARGET,'old-case-reply','CASE:'+cid+' Check cable',author='Old developer')
    local.commit()
    assert reply
    assert flow.inbox(PROFILE,case_id=data['case_id'])['replies'][0]['id']==reply
    assert not flow.known_reply(local.cursor(),A,CA,TARGET,'old-case-reply'),'legacy pilot admission is preserved'
    local.execute("INSERT INTO saas_consultation_group_replies(tenant_id,consultation_id,channel_id,conversation_id,message_id,body) VALUES(%s::uuid,%s::uuid,%s::uuid,%s,'historical-linked','An earlier answer')",(A,cid,CA,TARGET));local.commit()
    assert worker.reconcile()==1
    assert worker.reconcile()==0
    thread=flow.inbox(PROFILE,case_id=data['case_id'])
    assert {r['source_kind'] for r in thread['replies']}=={'provider','legacy'}
    assert all(r['draft_state']=='not_requested' for r in thread['replies'])


def test_manual_answer_is_attributed_idempotent_and_never_provider_delivery(case_ready):
    local,data,sender=case_ready
    result=flow.prepare(PROFILE,data,'https://crm.test')
    for _ in range(2):flow.manual_reply(PROFILE,result['id'],{'response':'Developer called: replace cable'})
    thread=flow.inbox(PROFILE,case_id=data['case_id'])
    assert len(thread['replies'])==1
    reply=thread['replies'][0]
    assert reply['source_kind']=='manual' and reply['source_actor']==11
    assert reply['author']=='Owner' and reply['draft_state']=='not_requested'
    assert thread['consultations'][0]['state']=='ready'
    assert sender.call_count==0
    assert local.execute('SELECT count(*) AS n FROM saas_tenant_notifications')[0]['n']==1


def test_unified_case_read_requires_support_and_source_dialog_rights(case_ready):
    local,data,_=case_ready
    flow.prepare(PROFILE,data,'https://crm.test')
    with pytest.raises(TenantLinearError):flow.inbox({'tenant_id':A,'telegram_id':33},case_id=data['case_id'])
    assert flow.inbox({'tenant_id':A,'telegram_id':33})['consultations']==[]
    with pytest.raises(TenantLinearError):flow.inbox({'tenant_id':B,'telegram_id':22},case_id=data['case_id'])


def test_case_group_preflight_stays_blocked_without_sending_or_mode_changes(case_ready):
    local,data,sender=case_ready
    local.execute("UPDATE saas_ai_group_coordination SET state='active' WHERE tenant_id=%s::uuid",(A,));local.commit()
    result=flow.prepare(PROFILE,data,'https://crm.test')
    outcome=flow.dispatch(PROFILE,result['id'],data['request_id'])
    assert outcome['state']=='blocked' and 'Группа' in outcome['error']
    assert not sender.called
    assert local.execute('SELECT state FROM saas_ai_group_coordination')[0]['state']=='active'


def test_legacy_dispatch_accepts_queue_receipt_without_false_sent_or_retry(case_ready):
    local,data,sender=case_ready
    consultation=internal.create(PROFILE,{'case_id':data['case_id'],'recipient_id':11,'request_key':'legacy:'+str(uuid.uuid4()),'reason':'Investigate'})
    request=str(uuid.uuid4());cid=consultation['id']
    local.execute("INSERT INTO saas_consultation_group_deliveries(tenant_id,consultation_id,request_id,case_id,channel_id,conversation_id,body,body_hash,state,created_by) VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,'synthetic','hash','ready',11)",(A,cid,request,data['case_id'],CA,TARGET));local.commit()
    sender.return_value={'state':'pending','message_id':'legacy-queue','external_message_id':None}
    assert legacy.dispatch(PROFILE,cid,request)['state']=='pending'
    assert legacy.dispatch(PROFILE,cid,request)['state']=='pending'
    assert sender.call_count==1
    with local.cursor() as cur:
        assert flow.observe_outbound_receipt(cur,A,CA,TARGET,{'_crm_signed_outbound_receipt':True,'fromMe':True,'id':'legacy-queue','externalMessageId':'LEGACY_PROVIDER','originClientMessageId':request})
    local.commit()
    assert legacy.read(PROFILE,cid)['state']=='sent'


def test_stale_inflight_is_unknown_until_actual_positive_receipt(case_ready):
    local,data,sender=case_ready
    result=flow.prepare(PROFILE,data,'https://crm.test')
    local.execute("UPDATE saas_consultation_group_deliveries SET state='sending',connect_message_id='known-queue',updated_at=now()-interval '6 minutes'");local.commit()
    worker.reconcile()
    assert flow.inbox(PROFILE,case_id=data['case_id'])['consultations'][0]['state']=='unknown'
    with local.cursor() as cur:
        receipt={'_crm_signed_outbound_receipt':True,'id':'known-queue','externalMessageId':'POSITIVE_PROVIDER','status':'failed'}
        assert not flow.observe_outbound_receipt(cur,A,CA,TARGET,receipt)
        assert flow.observe_outbound_receipt(cur,A,CA,TARGET,{**receipt,'status':'sent'})
    local.commit()
    assert flow.inbox(PROFILE,case_id=data['case_id'])['consultations'][0]['state']=='sent'
    assert not sender.called


def test_case_http_preview_thread_and_explicit_manual_record(case_ready):
    import asyncio
    import logging
    from aiohttp import web
    from aiohttp.test_utils import TestClient,TestServer
    from tenant_dialog_consultations_api import handler
    _,data,sender=case_ready
    expected={'expected_tenant_id':A,'expected_user_id':11}
    async def scenario():
        app=web.Application()
        for method,path,resource in [('POST','/preview','preview'),('POST','/prepare','prepare'),
            ('GET','/replies','replies'),('POST','/{id}/manual-reply','manual_reply')]:
            app.router.add_route(method,path,handler(lambda _:PROFILE,'https://crm.test',logging.getLogger(__name__),resource))
        async with TestClient(TestServer(app)) as client:
            preview=await client.post('/preview',json={**expected,**{key:data[key] for key in ('case_id','channel_id','conversation_id')}},headers={'Origin':'https://crm.test'})
            assert preview.status==200
            assert (await preview.json())['result']['case_id']==data['case_id']
            prepared=await client.post('/prepare',json={**expected,**data},headers={'Origin':'https://crm.test'})
            assert prepared.status==200
            cid=(await prepared.json())['result']['id']
            saved=await client.post('/'+cid+'/manual-reply',json={**expected,'response':'Phone advice from developer'},headers={'Origin':'https://crm.test'})
            assert saved.status==200
            thread=await client.get('/replies?case_id='+data['case_id'])
            assert thread.status==200
            result=(await thread.json())['result']
            assert result['replies'][0]['source_kind']=='manual'
            assert result['consultations'][0]['state']=='ready'
            assert not sender.called
    asyncio.run(scenario())


def test_queued_legacy_manual_followup_blocks_without_resume_or_new_job(case_ready,monkeypatch):
    import tenant_ai_pilot as pilot
    from test_tenant_support_inbox import AGENT
    local,data,_=case_ready
    result=flow.prepare(PROFILE,data,'https://crm.test')
    local.execute("UPDATE saas_ai_conversation_state SET state='human',reason='ai_developer_consultation:legacy' WHERE tenant_id=%s::uuid AND channel_id=%s AND conversation_id='client'",(A,CA))
    local.execute("INSERT INTO saas_ai_pilot_followups(tenant_id,id,consultation_id,case_id,channel_id,conversation_id,group_channel_id,group_conversation_id,group_message_id,group_reply,agent_id,source_message_id,state) VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s::uuid,'client',%s::uuid,%s,'old-queued-reply','Check cable',%s::uuid,'source','queued')",(A,uuid.uuid4(),result['id'],data['case_id'],CA,CA,TARGET,AGENT));local.commit()
    monkeypatch.setattr(pilot,'manual_live_job',lambda _:True)
    monkeypatch.setattr(pilot,'configured_scope',lambda:{'tenant_id':A,'channel_id':CA,'conversation_id':'client'})
    before=local.execute("SELECT state,reason,revision FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND channel_id=%s AND conversation_id='client'",(A,CA))[0]
    jobs=local.execute('SELECT count(*) AS n FROM saas_ai_agent_jobs')[0]['n']
    with local.cursor() as cur:assert not legacy.materialize_pilot_followup(cur)
    local.commit()
    assert local.execute("SELECT state,error_code FROM saas_ai_pilot_followups WHERE group_message_id='old-queued-reply'")[0]=={'state':'blocked','error_code':'manual_mode_requires_staff'}
    assert local.execute("SELECT state,reason,revision FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND channel_id=%s AND conversation_id='client'",(A,CA))[0]==before
    assert local.execute('SELECT count(*) AS n FROM saas_ai_agent_jobs')[0]['n']==jobs


def test_already_linked_manual_case_event_blocks_processing_and_final_send(db,monkeypatch):
    import json
    import tenant_ai_pilot as pilot
    import tenant_ai_group_coordination as coordination
    from test_tenant_consultation_group import setup_case,setup_real_pilot_gate
    from test_tenant_support_inbox import AGENT
    _,case=setup_case(db,monkeypatch)
    _,fingerprint=setup_real_pilot_gate(db,monkeypatch,customer_state='active',reason='pilot_developer_followup')
    payload={'pilot_developer_followup':True,'case_id':case}
    db.execute("UPDATE saas_tenant_webhook_events SET payload=%s::jsonb WHERE tenant_id=%s::uuid AND fingerprint=%s",(json.dumps(payload),A,fingerprint));db.commit()
    before=db.execute('SELECT mode,version,updated_at FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,AGENT))[0]
    job={'tenant_id':A,'provider':'connect','channel_id':CA,'conversation_id':'client','agent_id':AGENT,'event_fingerprint':fingerprint}
    assert not pilot.processing_allowed(job,connection=db)
    assert not pilot.automatic_send_allowed(job,connection=db)
    source={**job,'payload':payload,'source_kind':'run'}
    monkeypatch.setattr(coordination,'delivery_source',lambda *_:source)
    class Fernet:
        def decrypt(self,_):return b'{"api_key":"synthetic"}'
    monkeypatch.setattr(transport,'_fernet',lambda:Fernet())
    send=Mock(return_value='must-not-send');monkeypatch.setattr(transport,'send_ai_reply',send)
    revision=db.execute("SELECT revision FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND channel_id=%s AND conversation_id='client'",(A,CA))[0]['revision']
    assert transport.send_ai_reply_if_active(A,41,CA,'client','Do not auto send',str(uuid.uuid4()),revision,pilot_scope_fenced=True) is None
    assert not send.called
    assert db.execute('SELECT mode,version,updated_at FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,AGENT))[0]==before


def test_failed_explicit_case_draft_never_becomes_background_paid_retry(case_ready,monkeypatch):
    local,data,_=case_ready
    result=flow.prepare(PROFILE,data,'https://crm.test')
    flow.dispatch(PROFILE,result['id'],data['request_id'])
    with local.cursor() as cur:
        rid=flow.record_reply(cur,A,CA,TARGET,'draft-failure-case','Replace cable','PROVIDER_123')
    local.commit()
    run=Mock(side_effect=TenantLinearError('AI permission temporarily unavailable',403))
    monkeypatch.setattr('tenant_operator_copilot.run',run)
    with pytest.raises(TenantLinearError):flow.draft(PROFILE,rid,{'request_id':str(uuid.uuid4())})
    assert local.execute('SELECT draft_state FROM saas_dialog_consultation_replies WHERE id=%s::uuid',(rid,))[0]['draft_state']=='not_requested'
    assert worker.pending()==[]
    # A historical incorrect pending state also cannot restore paid case work.
    local.execute("UPDATE saas_dialog_consultation_replies SET draft_state='pending' WHERE id=%s::uuid",(rid,));local.commit()
    assert worker.pending()==[]
    assert run.call_count==0,'invalid quote correlation is rejected before the paid copilot call'


@pytest.mark.parametrize('state',['ready','pending','unknown','sent'])
@pytest.mark.parametrize('empty_case',[None,''])
def test_empty_case_id_cannot_bypass_independent_uncertain_or_sent_dedup(ready,state,empty_case):
    local,data,_=ready
    result=flow.prepare(PROFILE,data,'https://crm.test')
    local.execute('UPDATE saas_consultation_group_deliveries SET state=%s',(state,));local.commit()
    same=flow.prepare(PROFILE,{**data,'case_id':empty_case},'https://crm.test')
    assert same['id']==result['id'] and same['state']==state
    with pytest.raises(TenantLinearError):
        flow.prepare(PROFILE,{**data,'request_id':str(uuid.uuid4()),'case_id':empty_case},'https://crm.test')
    assert local.execute('SELECT count(*) AS n FROM saas_internal_consultations')[0]['n']==1
