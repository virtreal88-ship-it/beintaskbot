import json
import uuid
from unittest.mock import Mock
import asyncio
import pytest
from test_connect_channel_isolation import channels
from test_tenant_support_inbox import A, B, CA, CB, AGENT, database, db
from test_integrated_connect_release import flow
from test_connect_channel_multiprocess import database
import tenant_connect_transport as transport
import tenant_ai_agent_connect as active
import tenant_ai_simulation_engine as simulation
import tenant_knowledge_live_queue as extraction
from tenant_connect_identity import conflicted


def test_conflicting_identity_keeps_history_and_blocks_all_ai_fences(channels, monkeypatch):
    db=channels['db']
    monkeypatch.setattr(transport, '_verify_signature', lambda *args: True)
    data={'id':str(uuid.uuid4()),'channelId':CA,'conversationId':'client','fromMe':False,
          'type':'text','text':'synthetic conflict','conversationIdentityConflict':True,
          'directIdentity':{'lid':'123456789@lid','jid':'994501234567@s.whatsapp.net'}}
    event={'id':str(uuid.uuid4()),'type':'message.inbound.received','occurred_at':'2026-10-23T16:00:00Z','data':data}
    before=db.execute('SELECT count(*) AS n FROM saas_crm_messages')[0]['n'];db.commit()
    assert transport.ingest(json.dumps(event).encode(),'mock-signature',event)
    assert transport.ingest(json.dumps(event).encode(),'mock-signature',event)
    assert db.execute('SELECT count(*) AS n FROM saas_crm_messages')[0]['n']==before+1
    with db.cursor() as cur:
        for conversation in ('client',data['directIdentity']['lid'],data['directIdentity']['jid']):
            assert conflicted(cur,A,CA,conversation)
        assert not conflicted(cur,A,CB,'client')
        assert not conflicted(cur,B,CA,'client')
        assert extraction._configured(cur,A,CA,conversation='client') is None
        monkeypatch.setenv('AI_SIMULATION_ENABLED','true')
        assert simulation.enqueue_inbound(cur,tenant_id=A,channel_id=CA,conversation_id='client',lead_id=41,
            source_message_id=str(uuid.uuid4()),source_event_id='synthetic',occurred_at='2026-10-23T16:00:00Z') is False
        assert simulation._mark_provider_started(cur,{'tenant_id':A,'channel_id':CA,'conversation_id':'client'},1) is False
    assert active.allowed({'provider':'connect','tenant_id':A,'channel_id':CA,'conversation_id':'client','agent_id':AGENT},connection=db) is False
    import tenant_operator_assignment as assignment
    from tenant_linear_policy import TenantLinearError
    data={'provider':'connect','channel_id':CA,'conversation_id':'client','lead_id':41}
    with db.cursor() as cur:
        tenant,_,channel,conversation,route,state,has_job,config=assignment.context(cur,{'tenant_id':A,'telegram_id':11},data)
        assert assignment.candidates(cur,tenant,channel,conversation,route,state,has_job,config)==([], 'conversation_identity_conflict')
        with pytest.raises(TenantLinearError,match='conversation_identity_conflict'):
            assignment.context(cur,{'tenant_id':A,'telegram_id':11},data,writing=True)
    db.commit()


def test_inbound_from_configured_employee_number_is_not_a_customer_request(channels, monkeypatch):
    db=channels['db']
    db.execute("INSERT INTO saas_tenant_member_whatsapp_numbers(tenant_id,telegram_id,phone_digits,recognition_enabled,created_by) VALUES(%s::uuid,11,'994501234567',TRUE,11)",(A,));db.commit()
    monkeypatch.setattr(transport,'_verify_signature',lambda *a:True)
    admit=Mock();monkeypatch.setattr(simulation,'enqueue_inbound',admit);monkeypatch.setenv('AI_SIMULATION_ENABLED','true')
    data={'id':str(uuid.uuid4()),'channelId':CA,'conversationId':'994501234567@s.whatsapp.net','fromMe':False,'type':'text','text':'Synthetic staff message'}
    event={'id':str(uuid.uuid4()),'type':'message.inbound.received','data':data}
    assert transport.ingest(json.dumps(event).encode(),'mock',event)
    row=db.execute('SELECT * FROM saas_crm_messages WHERE tenant_id=%s::uuid AND external_id=%s',(A,'connect:'+data['id']))[0]
    assert row['direction']=='incoming' and row['raw']['verified_employee_id']==11
    from tenant_operator_dialogs import _message
    assert _message(row,contact_name='Customer')['author_type']=='employee'
    from tenant_chat_ai_policy import history
    from tenant_operator_copilot import _bounded_conversation
    from tenant_knowledge_candidates_store import author_role
    assert history([row],{})[0][0]['speaker']=='SALES MANAGER'
    assert _bounded_conversation([row])[0]['speaker']=='EMPLOYEE'
    assert author_role(row)=='operator'
    assert not db.execute("SELECT 1 FROM saas_ai_agent_jobs WHERE conversation_id=%s",(data['conversationId'],))
    assert not db.execute("SELECT 1 FROM saas_knowledge_live_queue WHERE conversation_id=%s",(data['conversationId'],))
    admit.assert_not_called()
    db.commit()


@pytest.mark.parametrize('state,expected',[('pending','pending'),('failed','failed'),('disabled','disabled'),(None,'not_requested')])
def test_media_analysis_state_is_distinct_from_download_failure(state,expected):
    from tenant_operator_dialogs import _message
    row={'channel':'connect:'+CA,'direction':'incoming','message_type':'audio','body':'','raw':{'voice_transcription':{'status':state}}}
    assert _message(row)['analysis_state']==expected


def test_only_confirmed_database_rollbacks_are_retried(monkeypatch):
    from psycopg.errors import DeadlockDetected
    action=Mock(side_effect=[DeadlockDetected('synthetic rollback'),True])
    monkeypatch.setattr(transport,'_ingest_once',action)
    assert transport.ingest(b'{}','synthetic',{}) is True
    assert action.call_count==2
    action=Mock(side_effect=ConnectionError('synthetic unknown connection outcome'))
    monkeypatch.setattr(transport,'_ingest_once',action)
    with pytest.raises(ConnectionError):transport.ingest(b'{}','synthetic',{})
    assert action.call_count==1


@pytest.mark.parametrize('code,expected',[
    ('voice_media_unavailable','file_unavailable'),('voice_download_timeout','file_unavailable'),
    ('voice_unsupported','unsupported'),('voice_transcription_failed','transcription_unavailable'),
    ('other_error','failed')])
def test_known_media_failures_have_distinct_display_reasons(code,expected):
    from tenant_operator_dialogs import _message
    assert _message({'channel':'connect:'+CA,'message_type':'audio',
        'raw':{'voice_transcription':{'status':'failed','error_code':code}}})['analysis_state']==expected


def test_inactive_channel_history_is_readable_but_never_a_send_route(channels):
    db=channels['db']
    db.execute("UPDATE saas_connect_channels SET active=FALSE,status='disconnected' WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid",(A,CA));db.commit()
    with db.cursor() as cur:
        assert transport.connect_route(cur,A,41) is None
        assert transport.connect_route(cur,A,41,historical_read=True)['channel_id']==CA
        assert transport.connect_route(cur,B,41,historical_read=True) is None


def test_avatar_authority_is_rechecked_after_download(channels,monkeypatch):
    import tenant_connect_avatars as avatars
    from tenant_linear_policy import TenantLinearError
    from test_tenant_support_inbox import PROFILE
    db=channels['db'];monkeypatch.setattr(avatars,'_connect',lambda:db)
    async def revoked(route):
        db.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s::uuid AND telegram_id=11',(A,));db.commit()
        return b'synthetic-image','image/jpeg'
    monkeypatch.setattr(avatars,'_fetch',revoked)
    with pytest.raises(TenantLinearError):
        asyncio.run(avatars.get_avatar(PROFILE,{'provider':'connect','lead_id':'41','channel_id':CA,'conversation_id':'client'}))


@pytest.mark.parametrize('state,age,allowed',[('unavailable','1 hour',False),('unavailable','8 days',True),
    ('loading','1 minute',False),('loading','4 minutes',True)])
def test_avatar_negative_cache_and_interrupted_download_have_bounded_retry(channels,monkeypatch,state,age,allowed):
    import tenant_connect_avatars as avatars
    from tenant_linear_policy import TenantLinearError
    from test_tenant_support_inbox import PROFILE
    db=channels['db'];monkeypatch.setattr(avatars,'_connect',lambda:db)
    db.execute("UPDATE saas_connect_conversations SET avatar_status=%s,avatar_checked_at=now()-%s::interval WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id='client'",(state,age,A,CA));db.commit()
    calls=[]
    async def fetch(route):
        calls.append(route['conversation_id']);return b'png','image/png'
    monkeypatch.setattr(avatars,'_fetch',fetch)
    params={'provider':'connect','lead_id':'41','channel_id':CA,'conversation_id':'client'}
    if allowed:assert asyncio.run(avatars.get_avatar(PROFILE,params))==(b'png','image/png')
    else:
        with pytest.raises(TenantLinearError):asyncio.run(avatars.get_avatar(PROFILE,params))
    assert len(calls)==int(allowed)
