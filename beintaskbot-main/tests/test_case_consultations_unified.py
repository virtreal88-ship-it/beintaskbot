"""Unified case consultation regressions: isolated SQL and synthetic receipts only."""
import uuid
from unittest.mock import Mock
import pytest
from test_tenant_support_inbox import database,db,A,B,CA,CB,PROFILE,manual
from test_dialog_consultations import ready,TARGET
import tenant_dialog_consultations as flow
import tenant_dialog_consultations_worker as worker
import tenant_consultation_group as legacy
import tenant_internal_consultations as internal
import tenant_connect_transport as transport
import tenant_public_case as public
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


def test_case_pending_receipt_reconciles_late_reply_and_never_auto_drafts(case_ready):
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
    assert reply['draft_state']=='not_requested'
    assert 'support_ticket_id='+data['case_id'] in reply['url']
    assert worker.pending()==[]
    assert sender.call_count==1
    assert local.execute('SELECT count(*) AS n FROM saas_tenant_notifications')[0]['n']==1


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
    assert run.call_count==1


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
