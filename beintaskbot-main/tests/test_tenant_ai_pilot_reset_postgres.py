"""Reset transaction integration tests against the isolated PGlite database."""
import uuid

import pytest

from test_tenant_support_inbox import database, db, A, CA, AGENT, PROFILE

import tenant_ai_pilot as pilot
import tenant_ai_agents_store as agents
import tenant_platform
import tenant_consultation_group as group_flow
import tenant_support_inbox_store as tickets
from tenant_linear_policy import TenantLinearError


CUSTOMER='994102135103@s.whatsapp.net'
GROUP=pilot.SCOPED_LIVE_GROUP


def seed_reset_state(db, monkeypatch):
    group_flow.schema(db)
    monkeypatch.setattr(pilot,'SCOPED_LIVE_TENANT',A)
    monkeypatch.setattr(pilot,'SCOPED_LIVE_CHANNEL',CA)
    monkeypatch.setenv('AI_AGENTS_PILOT_TENANT_ID',A)
    monkeypatch.setenv('AI_AGENTS_PILOT_CHANNEL_ID',CA)
    monkeypatch.setenv('AI_AGENTS_PILOT_CONVERSATION_ID',CUSTOMER)
    monkeypatch.setenv('AI_AGENTS_PILOT_GROUP_CONVERSATION_ID',GROUP)
    monkeypatch.setenv('AI_AGENTS_SCOPED_LIVE_ENABLED','true')
    monkeypatch.setenv('AI_AGENTS_ENABLED','true')
    monkeypatch.setattr(tenant_platform,'_connect',lambda:db)
    db.execute('''INSERT INTO saas_connect_conversations
      (tenant_id,connect_channel_id,conversation_id,lead_id,contact_name,last_message_at)
      VALUES(%s::uuid,%s::uuid,%s,99,'Pilot customer',now())''',(A,CA,CUSTOMER))
    db.execute('''INSERT INTO saas_ai_agents(id,tenant_id,name,enabled,mode,scopes,created_by)
      VALUES(%s::uuid,%s::uuid,'Pilot',TRUE,'manual',%s::jsonb,11)''',
      (AGENT,A,'[{"provider":"connect","channel_id":"'+CA+'"}]'))
    db.execute("UPDATE saas_connect_channels SET active=TRUE WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid",(A,CA))
    db.execute("UPDATE saas_connect_conversations SET ai_enabled=TRUE,manual_hold=FALSE WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s",(A,CA,CUSTOMER))
    db.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status) VALUES(%s::uuid,'connect','connected') ON CONFLICT(tenant_id,provider) DO UPDATE SET status='connected'",(A,))
    db.execute("INSERT INTO saas_ai_agent_settings(tenant_id,enabled,emergency_stop,auto_send_enabled,operating_mode) VALUES(%s::uuid,TRUE,FALSE,FALSE,'simulation') ON CONFLICT(tenant_id) DO UPDATE SET enabled=TRUE,emergency_stop=FALSE,auto_send_enabled=FALSE,operating_mode='simulation'",(A,))
    db.execute('''INSERT INTO saas_ai_conversation_state
      (tenant_id,provider,channel_id,conversation_id,agent_id,state,reason,revision)
      VALUES(%s::uuid,'connect',%s,%s,%s::uuid,'human','ai_developer_consultation:missing_knowledge',7)''',
      (A,CA,CUSTOMER,AGENT))
    ticket=str(uuid.uuid4());consult=str(uuid.uuid4());event='old-pilot-event'
    db.execute('''INSERT INTO saas_support_tickets
      (tenant_id,id,channel_id,conversation_id,source,reason,status)
      VALUES(%s::uuid,%s::uuid,%s::uuid,%s,'ai_handoff','synthetic test','waiting')''',
      (A,ticket,CA,CUSTOMER))
    db.execute('''INSERT INTO saas_internal_consultations
      (tenant_id,id,case_id,channel_id,conversation_id,request_key,reason,evidence,status)
      VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s,'test','{}'::jsonb,'waiting_specialist')''',
      (A,consult,ticket,CA,CUSTOMER,'test-reset:'+consult))
    db.execute('''INSERT INTO saas_consultation_group_deliveries
      (tenant_id,consultation_id,request_id,case_id,channel_id,conversation_id,body,body_hash,
       state,provider_message_id,created_by)
      VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,'CASE test','hash','sent','PILOT_SENT',11)''',
      (A,consult,str(uuid.uuid4()),ticket,CA,GROUP))
    db.execute('''INSERT INTO saas_ai_pilot_followups
      (tenant_id,id,consultation_id,case_id,channel_id,conversation_id,group_channel_id,
       group_conversation_id,group_message_id,group_reply,agent_id,state,awaiting_customer)
      VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s::uuid,%s,'old-group-reply',
       'old reply',%s::uuid,'queued',TRUE)''',
      (A,str(uuid.uuid4()),consult,ticket,CA,CUSTOMER,CA,GROUP,AGENT))
    db.execute('''INSERT INTO saas_tenant_webhook_events
      (tenant_id,fingerprint,provider,external_id,event_type,payload,state)
      VALUES(%s::uuid,%s,'connect','old-event','incoming_message','{}'::jsonb,'queued')''',(A,event))
    db.execute('''INSERT INTO saas_ai_agent_jobs
      (tenant_id,agent_id,provider,conversation_id,lead_id,talk_id,chat_id,channel_id,
       event_fingerprint,due_at,state)
      VALUES(%s::uuid,%s::uuid,'connect',%s,99,'talk','chat',%s,%s,now(),'queued')''',
      (A,AGENT,CUSTOMER,CA,event))
    other_event='other-dialog-event'
    db.execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,channel_id,conversation_id,agent_id,state,reason,revision) VALUES(%s::uuid,'connect',%s,'other-dialog',%s::uuid,'human','unrelated',4)",(A,CA,AGENT))
    db.execute("INSERT INTO saas_tenant_webhook_events(tenant_id,fingerprint,provider,external_id,event_type,payload,state) VALUES(%s::uuid,%s,'connect','other-event','incoming_message','{}'::jsonb,'queued')",(A,other_event))
    db.execute("INSERT INTO saas_ai_agent_jobs(tenant_id,agent_id,provider,conversation_id,lead_id,talk_id,chat_id,channel_id,event_fingerprint,due_at,state) VALUES(%s::uuid,%s::uuid,'connect','other-dialog',100,'other-talk','other-chat',%s,%s,now(),'queued')",(A,AGENT,CA,other_event))
    db.execute('''INSERT INTO saas_ai_pilot_confirmations
      (tenant_id,channel_id,conversation_id,scope_fingerprint,kwid_confirmed_by,kwid_confirmed_at,
       kwid_expires_at,owner_approved_by,owner_approved_at,owner_expires_at)
      VALUES(%s::uuid,%s::uuid,%s,%s,11,now(),now()+interval '10 minutes',11,now(),now()+interval '10 minutes')''',
      (A,CA,CUSTOMER,'a'*64))
    db.commit()
    return ticket,consult,event


def reset_request():
    return {'confirmed':True,'reset_id':str(uuid.uuid4())}


def test_reset_commits_cutoff_fences_old_case_and_preserves_history(db,monkeypatch):
    ticket,consult,event=seed_reset_state(db,monkeypatch)
    request=reset_request()
    result=pilot.reset_pilot_test_dialog(PROFILE,request)
    assert result['reset'] and result['new_attestations_required']
    reset_state=db.execute('''SELECT revision,state,reason FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid
      AND provider='connect' AND channel_id=%s AND conversation_id=%s''',(A,CA,CUSTOMER))[0]
    assert reset_state=={'revision':8,'state':'active','reason':'pilot_test_reset'}
    assert db.execute('SELECT state,error_code FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND event_fingerprint=%s',(A,event))[0]=={'state':'skipped','error_code':'pilot_test_reset'}
    assert db.execute('SELECT state,error_code FROM saas_tenant_webhook_events WHERE tenant_id=%s::uuid AND fingerprint=%s',(A,event))[0]=={'state':'skipped','error_code':'pilot_test_reset'}
    assert db.execute('SELECT state,awaiting_customer FROM saas_ai_pilot_followups WHERE tenant_id=%s::uuid AND consultation_id=%s::uuid',(A,consult))[0]=={'state':'blocked','awaiting_customer':False}
    assert db.execute('SELECT status,delivery_state FROM saas_internal_consultations WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,consult))[0]=={'status':'closed','delivery_state':'test_reset'}
    assert db.execute('SELECT status,completion_outcome,knowledge_candidate_requested FROM saas_support_tickets WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,ticket))[0]=={'status':'waiting','completion_outcome':'test_stopped','knowledge_candidate_requested':False}
    assert db.execute('SELECT count(*) AS n FROM saas_crm_messages WHERE tenant_id=%s::uuid',(A,))[0]['n']>=1
    assert db.execute('SELECT count(*) AS n FROM saas_ai_pilot_episode_cutoffs WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id=%s',(A,CA,CUSTOMER))[0]['n']==1
    cutoff=pilot.episode_cutoff({'provider':'connect','tenant_id':A,'channel_id':CA,'conversation_id':CUSTOMER})
    from tenant_ai_agent_worker import _after_pilot_cutoff
    old={'external_id':'old-history','happened_at':'2000-01-01T00:00:00+00:00','body':'preserved old text'}
    fresh={'external_id':'fresh-history','happened_at':'2999-01-01T00:00:00+00:00','body':'fresh test text'}
    assert _after_pilot_cutoff([old,fresh],cutoff)==[fresh]
    assert old['body']=='preserved old text'
    assert db.execute('SELECT kwid_confirmed_by,owner_approved_by FROM saas_ai_pilot_confirmations WHERE tenant_id=%s::uuid',(A,))[0]=={'kwid_confirmed_by':None,'owner_approved_by':None}
    assert db.execute("SELECT revision,state,reason FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id='other-dialog'",(A,CA))[0]=={'revision':4,'state':'human','reason':'unrelated'}
    assert db.execute('SELECT state FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND event_fingerprint=%s',(A,'other-dialog-event'))[0]['state']=='queued'
    assert pilot.reset_pilot_test_dialog(PROFILE,request)['idempotent_replay'] is True
    assert db.execute('''SELECT revision FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid
      AND provider='connect' AND channel_id=%s AND conversation_id=%s''',(A,CA,CUSTOMER))[0]['revision']==8
    assert pilot.confirm_kwid_disabled(PROFILE,{'confirmed':True})['kwid_confirmation']['state']=='confirmed'
    assert pilot.approve_pilot(PROFILE,{'approved':True})['separate_owner_approval']['state']=='approved'
    old_allowed=pilot.automatic_send_allowed({'provider':'connect','tenant_id':A,'channel_id':CA,
      'conversation_id':CUSTOMER,'event_fingerprint':event,'agent_id':AGENT})
    assert old_allowed is False
    fresh_event='fresh-after-reset'
    db.execute('''INSERT INTO saas_tenant_webhook_events(tenant_id,fingerprint,provider,external_id,
      event_type,payload,state,received_at) VALUES(%s::uuid,%s,'connect','fresh-event',
      'incoming_message','{}'::jsonb,'queued',clock_timestamp()+interval '1 second')''',(A,fresh_event))
    db.commit()
    fresh_allowed=pilot.automatic_send_allowed({'provider':'connect','tenant_id':A,'channel_id':CA,
      'conversation_id':CUSTOMER,'event_fingerprint':fresh_event,'agent_id':AGENT})
    assert fresh_allowed is True
    with db.cursor() as cur:
        created=tickets.create_in_transaction(cur,A,CA,CUSTOMER,'fresh episode','fresh-episode-event',actor=11,description='A new test issue')
    db.commit()
    assert created['created'] and created['id']!=ticket
    # A quoted reply to the old CASE can be recorded as history but cannot
    # materialize a new follow-up after the ticket was test-stopped.
    with db.cursor() as cur:
        assert group_flow.record_group_reply(cur,A,CA,GROUP,'late-old-reply','I found it. CASE:'+consult,'PILOT_SENT')==consult
        assert group_flow.enqueue_pilot_customer_followup(cur,A,CA,GROUP,'late-old-reply') is False
    db.commit()


def test_reset_rolls_back_cutoff_and_all_mutations_on_transaction_failure(db,monkeypatch):
    ticket,consult,event=seed_reset_state(db,monkeypatch)
    monkeypatch.setattr(agents,'_audit',lambda *_args,**_kwargs:(_ for _ in ()).throw(RuntimeError('synthetic audit failure')))
    with pytest.raises(RuntimeError,match='synthetic audit failure'):
        pilot.reset_pilot_test_dialog(PROFILE,reset_request())
    assert db.execute('''SELECT revision FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid
      AND provider='connect' AND channel_id=%s AND conversation_id=%s''',(A,CA,CUSTOMER))[0]['revision']==7
    assert db.execute('SELECT state FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND event_fingerprint=%s',(A,event))[0]['state']=='queued'
    assert db.execute('SELECT status,completion_outcome FROM saas_support_tickets WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,ticket))[0]=={'status':'waiting','completion_outcome':''}
    assert db.execute('SELECT count(*) AS n FROM saas_ai_pilot_episode_cutoffs WHERE tenant_id=%s::uuid',(A,))[0]['n']==0


def test_non_owner_cannot_reset_the_exact_dialog(db,monkeypatch):
    ticket,_consult,event=seed_reset_state(db,monkeypatch)
    db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name) VALUES(%s::uuid,33,'manager','Manager')",(A,))
    db.commit()
    with pytest.raises(TenantLinearError) as caught:
        pilot.reset_pilot_test_dialog({'tenant_id':A,'telegram_id':33},reset_request())
    assert caught.value.status==403
    assert db.execute('SELECT state FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND event_fingerprint=%s',(A,event))[0]['state']=='queued'
    assert db.execute('SELECT status FROM saas_support_tickets WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,ticket))[0]['status']=='waiting'
