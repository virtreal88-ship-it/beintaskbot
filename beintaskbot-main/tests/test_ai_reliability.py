"""Reliability regressions use isolated PostgreSQL/WASM and mocked external APIs."""
import asyncio
import json
import uuid
from unittest.mock import Mock, patch
import pytest
from aiohttp.test_utils import make_mocked_request
from test_integrated_connect_release import flow
from test_tenant_support_inbox import database,db,A,B,CA,CB,AGENT,PROFILE,seed_job
import tenant_ai_reliability as reliability
import tenant_ai_agent_run_store as runs
import tenant_ai_agent_connect as connect
import tenant_connect_transport as transport
import tenant_ai_agents_api as api
from tenant_linear_policy import TenantLinearError

@pytest.fixture
def system(flow,monkeypatch):
    monkeypatch.setattr(reliability,'_connect',lambda:flow['db'])
    return flow


def test_webhook_queue_failure_rolls_back_receipt_and_history(system,monkeypatch):
    f=system;db=f['db'];mid=str(uuid.uuid4())
    event={'id':'atomic-inbound','type':'message.inbound.received','data':{'id':mid,'channelId':CA,'conversationId':'client','type':'text','text':'private incoming'}}
    monkeypatch.setattr(transport,'_verify_signature',lambda *a:True)
    enqueue=connect.enqueue
    def interrupted(*args,**kwargs):
        assert kwargs['connection'] is db
        # Fail after writes, before the shared transaction commits.
        enqueue(*args,**kwargs)
        raise RuntimeError('database interruption')
    with patch.object(connect,'enqueue',side_effect=interrupted):
        with pytest.raises(RuntimeError): transport.ingest(json.dumps(event).encode(),'sig',event)
    assert not db.execute("SELECT 1 FROM saas_connect_webhook_events WHERE event_id='atomic-inbound'")
    assert not db.execute("SELECT 1 FROM saas_crm_messages WHERE external_id=%s",('connect:'+mid,))
    db.commit()
    assert transport.ingest(json.dumps(event).encode(),'sig',event)
    assert transport.ingest(json.dumps(event).encode(),'sig',event)
    assert db.execute("SELECT count(*) AS n FROM saas_crm_messages WHERE external_id=%s",('connect:'+mid,))[0]['n']==1
    assert db.execute("SELECT count(*) AS n FROM saas_tenant_webhook_events WHERE external_id=%s",(mid,))[0]['n']==1
    f['process']();f['process']();assert f['send'].call_count==1


def expired_job(f,attempts=1):
    f['inbound']();job=runs.claim_next();db=f['db']
    db.execute("UPDATE saas_ai_agent_jobs SET attempts=%s,lease_until=now()-interval '1 second' WHERE tenant_id=%s::uuid AND provider='connect'",(attempts,A));db.commit()
    return job


def test_restart_before_paid_call_requeues_with_bounded_attempts(system):
    job=expired_job(system)
    recovered=runs.claim_next();assert recovered['attempts']==2
    assert recovered['lease_token']!=job['lease_token']
    db=system['db'];db.execute("UPDATE saas_ai_agent_jobs SET attempts=5,lease_until=now()-interval '1 second' WHERE tenant_id=%s::uuid",(A,));db.commit()
    assert runs.claim_next() is None
    assert db.execute("SELECT state FROM saas_tenant_webhook_events WHERE fingerprint=%s",(job['event_fingerprint'],))[0]['state']=='failed'


def test_restart_after_paid_reservation_does_not_repeat_model_or_send(system):
    f=system;f['inbound']();job=runs.claim_next();r=runs.reserve_run(job,pipeline_id='',stage_id='',model='test-model')
    db=f['db'];db.execute("UPDATE saas_ai_agent_jobs SET lease_until=now()-interval '1 second'");db.execute("UPDATE saas_ai_agent_runs SET created_at=now()-interval '10 minutes'");db.commit()
    assert runs.claim_next() is None
    assert db.execute("SELECT state FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid",(A,))[0]['state']=='unknown'
    assert runs.claim_next() is None
    f['send'].assert_not_called();f['generate'].assert_not_called()


@pytest.mark.parametrize('job_state',['processing','unknown'])
def test_recover_committed_send_without_resending_or_changing_human_mode(system,job_state):
    f=system;f['inbound']();job=runs.claim_next();r=runs.reserve_run(job,pipeline_id='',stage_id='',model='test-model')
    mid=str(uuid.uuid4());db=f['db']
    db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,author_name,body,raw) VALUES(%s::uuid,%s::uuid,41,%s,%s,'outgoing','AI Agent','secret',%s::jsonb)",(str(uuid.uuid4()),A,'connect:'+mid,'connect:'+CA,json.dumps({'run_id':r['run_id'],'status':'accepted'})))
    db.execute("UPDATE saas_ai_agent_jobs SET state=%s,lease_until=now()-interval '1 second'",(job_state,))
    db.execute("UPDATE saas_ai_conversation_state SET state='human'");db.commit()
    assert runs.claim_next() is None
    assert runs.claim_next() is None
    assert db.execute("SELECT state FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid",(A,))[0]['state']=='sent'
    assert db.execute("SELECT state FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid",(A,))[0]['state']=='human'
    f['send'].assert_not_called();f['generate'].assert_not_called()


def test_receipt_from_other_tenant_cannot_recover_send(system):
    f=system;f['inbound']();job=runs.claim_next();r=runs.reserve_run(job,pipeline_id='',stage_id='',model='test-model');db=f['db']
    db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,author_name,raw) VALUES(%s::uuid,%s::uuid,42,'connect:foreign',%s,'outgoing','AI Agent',%s::jsonb)",(str(uuid.uuid4()),B,'connect:'+CB,json.dumps({'run_id':r['run_id'],'status':'accepted'})))
    db.execute("UPDATE saas_ai_agent_jobs SET lease_until=now()-interval '1 second'");db.commit()
    assert runs.claim_next() is None
    assert db.execute("SELECT state FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid",(A,))[0]['state']=='unknown'


def test_stale_retry_and_skip_cannot_reopen_newer_event(system):
    f=system;f['inbound']();old=runs.claim_next();f['inbound']();new=runs.claim_next()
    assert runs.retry_or_fail(old,'read_or_context_failed') is False
    assert runs.skip_job(old,'old_failure') is False
    db=f['db']
    assert db.execute("SELECT state FROM saas_tenant_webhook_events WHERE fingerprint=%s",(old['event_fingerprint'],))[0]['state']=='superseded'
    assert db.execute("SELECT state FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid",(A,))[0]['state']=='processing'


def test_retry_refuses_existing_paid_receipt(system):
    f=system;f['inbound']();job=runs.claim_next();runs.reserve_run(job,pipeline_id='',stage_id='',model='test-model')
    assert runs.retry_or_fail(job,'read_or_context_failed') is False
    assert f['db'].execute("SELECT state FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid",(A,))[0]['state']=='processing'


def test_terminal_superseded_job_is_recovered(system):
    f=system;f['inbound']();job=runs.claim_next();runs.reserve_run(job,pipeline_id='',stage_id='',model='test-model');db=f['db']
    db.execute("UPDATE saas_ai_agent_jobs SET lease_until=now()-interval '1 second'");db.execute("UPDATE saas_ai_agent_runs SET state='superseded'");db.commit()
    assert runs.claim_next() is None
    assert db.execute("SELECT state FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid",(A,))[0]['state']=='skipped'


def test_diagnostics_scoped_redacted_and_history_survives_coalescing(system):
    f=system;first,_=f['inbound']();f['process']();f['inbound']();reliability.heartbeat()
    result=reliability.snapshot(PROFILE)
    assert result['health']['worker_alive']
    old=next(m for m in result['messages'] if m['message_id']==first)
    assert old['status']=='sent' and old['checkpoints']['generated']
    encoded=json.dumps(result)
    for secret in ('Cavab hazirdir','Salam','mock-connect-key','mock-platform-key','instructions','api_key'):
        assert secret not in encoded
    other=reliability.snapshot({'tenant_id':B,'telegram_id':22})
    assert other['messages']==[] and other['metrics']['queued']==0
    with pytest.raises(TenantLinearError): reliability.snapshot({'tenant_id':B,'telegram_id':11})


def test_unknown_errors_are_redacted_and_metrics_survive_job_replacement(system):
    f=system;f['inbound']();job=runs.claim_next();r=runs.reserve_run(job,pipeline_id='',stage_id='',model='test-model')
    runs.finish_unknown(job,r,'connect_send_unknown');f['inbound']()
    result=reliability.snapshot(PROFILE)
    assert result['metrics']['ai_errors_24h']==1 and result['metrics']['unsuccessful_sends_24h']==1
    assert reliability.reason('sk-secret https://evil.test private message')==('internal_error',reliability.REASONS['internal_error'])
    assert reliability.reason('voice_http_429_rate_limit')[0]=='voice_http_429_rate_limit'


def test_heartbeat_missing_or_stale_is_not_healthy(system):
    db=system['db'];db.execute('DELETE FROM saas_ai_worker_health');db.commit()
    assert reliability.snapshot(PROFILE)['health']['state']=='unavailable'
    reliability.heartbeat();assert reliability.snapshot(PROFILE)['health']['state']=='ready'
    db.execute("UPDATE saas_ai_worker_health SET last_seen_at=now()-interval '2 minutes'");db.commit()
    assert reliability.snapshot(PROFILE)['health']['state']=='unavailable'


def test_reliability_api_get_uses_tenant_profile_and_no_store():
    profile={'tenant_id':A,'telegram_id':11};request=make_mocked_request('GET','/api/platform/ai/reliability?limit=12')
    with patch.object(reliability,'snapshot',return_value={'health':{'state':'ready'}}) as read:
        response=asyncio.run(api.handler(lambda r:profile,'https://crm.test',Mock(),'reliability')(request))
    assert response.status==200 and response.headers['Cache-Control']=='no-store'
    read.assert_called_once_with(profile,'12')


@pytest.mark.parametrize('receipt', ['accepted', 'missing', 'wrong_channel'])
def test_stuck_manual_send_is_reconciled_without_external_call(system, receipt):
    from tenant_ai_agents_store import reserve_draft_send
    f=system;db=f['db']
    db.execute("UPDATE saas_ai_agents SET mode='manual'");db.commit()
    mid,_=f['inbound']();f['process']()
    draft=db.execute('SELECT id FROM saas_ai_agent_drafts')[0]['id'];db.commit()
    assert reserve_draft_send(PROFILE,draft)
    assert db.execute('SELECT send_started_at FROM saas_ai_agent_drafts')[0]['send_started_at']
    if receipt!='missing':
        db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,author_name,raw) VALUES(%s::uuid,%s::uuid,41,'connect:manual-result',%s,'outgoing','AI Agent',%s::jsonb)",
                   (str(uuid.uuid4()),A,'connect:'+(CA if receipt=='accepted' else CB),json.dumps({'run_id':draft,'status':'accepted'})))
    db.execute("UPDATE saas_ai_agent_drafts SET send_started_at=now()-interval '11 minutes'");db.commit()
    assert runs.claim_next() is None
    assert runs.claim_next() is None
    expected='approved' if receipt=='accepted' else 'unknown'
    assert db.execute('SELECT state FROM saas_ai_agent_drafts')[0]['state']==expected
    db.commit()
    item=next(m for m in reliability.snapshot(PROFILE)['messages'] if m['message_id']==mid)
    assert item['status']==('sent' if receipt=='accepted' else 'error')
    assert not item['automatic_retry']
    f['send'].assert_not_called()
    assert f['generate'].call_count==1


@pytest.mark.parametrize('failure', ['model', 'media', 'connect'])
def test_failure_reason_and_next_message_are_independent(system,monkeypatch,failure):
    import tenant_connect_voice as voice
    from tenant_chat_ai_provider import ChatAIProviderError
    f=system
    if failure=='model':
        f['generate'].side_effect=ChatAIProviderError(429,'rate_limit')
        expected='chat_ai_http_429_rate_limit'
    elif failure=='media':
        monkeypatch.setattr(voice,'download_audio',Mock(side_effect=voice.VoiceError('voice_invalid_audio')))
        expected='voice_invalid_audio'
    else:
        f['send'].side_effect=RuntimeError('secret-api-key private payload')
        expected='connect_send_unknown'
    mid,_=f['inbound']('audio' if failure=='media' else 'text');f['process']()
    item=next(m for m in reliability.snapshot(PROFILE)['messages'] if m['message_id']==mid)
    assert item['status']=='error' and item['reason_code']==expected
    assert not item['automatic_retry']
    assert connect.enqueue(A,channel_id=CA,conversation_id='client',lead_id=41,message_id=mid,event_id='retry',text='')==0
    before=f['generate'].call_count;f['process']();assert f['generate'].call_count==before
    f['generate'].side_effect=None
    f['send'].side_effect=lambda *a:str(uuid.uuid4())
    last,_=f['inbound']();f['process']()
    result=reliability.snapshot(PROFILE)
    assert next(m for m in result['messages'] if m['message_id']==last)['status']=='sent'
    assert 'secret-api-key' not in json.dumps(result)


@pytest.mark.parametrize('blocker',['agent_disabled','company_ai_disabled','emergency_stop','conversation_human_or_paused'])
def test_admission_explains_why_message_was_skipped(system,blocker):
    from tenant_ai_agents_store import set_conversation_state
    f=system;db=f['db']
    if blocker=='agent_disabled':db.execute('UPDATE saas_ai_agents SET enabled=FALSE')
    elif blocker=='company_ai_disabled':db.execute('UPDATE saas_ai_agent_settings SET enabled=FALSE')
    elif blocker=='emergency_stop':db.execute('UPDATE saas_ai_agent_settings SET emergency_stop=TRUE')
    else:set_conversation_state(PROFILE,'client','human','takeover','connect',CA)
    db.commit();mid,count=f['inbound']();assert count==0
    item=next(m for m in reliability.snapshot(PROFILE)['messages'] if m['message_id']==mid)
    assert item['status']=='skipped' and item['reason_code']==blocker
    assert not item['automatic_retry']
    f['generate'].assert_not_called();f['send'].assert_not_called()


def test_schedule_and_limit_have_specific_reasons(system,monkeypatch):
    from datetime import datetime,timedelta,timezone
    import tenant_ai_agent_worker as worker
    f=system;mid,_=f['inbound']()
    monkeypatch.setattr(worker,'_next_open',lambda _:datetime.now(timezone.utc)+timedelta(hours=1))
    f['process']()
    item=next(m for m in reliability.snapshot(PROFILE)['messages'] if m['message_id']==mid)
    assert item['reason_code']=='outside_schedule' and item['status']=='queued'
    assert item['next_attempt_at']
    monkeypatch.setattr(worker,'_next_open',lambda _:None)
    monkeypatch.setattr(worker,'reserve_run',Mock(side_effect=TenantLinearError('limit',429)))
    mid,_=f['inbound']();f['process']()
    item=next(m for m in reliability.snapshot(PROFILE)['messages'] if m['message_id']==mid)
    assert item['reason_code']=='ai_limit_reached' and item['status']=='skipped'
    f['generate'].assert_not_called();f['send'].assert_not_called()


def test_paid_generation_cancelled_by_takeover_still_consumes_daily_limit(system):
    f=system;db=f['db'];f['inbound']();job=runs.claim_next()
    reservation=runs.reserve_run(job,pipeline_id='',stage_id='',model='test-model')
    runs.record_token_usage(job,reservation,{'total_tokens':5},generated=True)
    runs.finish_superseded(job,reservation,'connect_human_takeover')
    # The platform-backed tenant defaults to 50 calls/day; 49 earlier calls plus
    # this successfully generated but cancelled reply must exhaust the budget.
    db.execute("INSERT INTO saas_chat_ai_runs(tenant_id,request_id,requested_by,lead_id,input_hash,config_version,status,reserved_calls) VALUES(%s::uuid,%s::uuid,11,41,'test-hash','test-version','completed',49)",(A,str(uuid.uuid4())))
    db.commit();f['inbound']();job=runs.claim_next()
    with pytest.raises(TenantLinearError) as error:
        runs.reserve_run(job,pipeline_id='',stage_id='',model='test-model')
    assert error.value.status==429


def test_backlog_metrics_and_access_are_tenant_scoped(system):
    f=system;db=f['db'];f['inbound']();reliability.heartbeat()
    db.execute("UPDATE saas_ai_agent_jobs SET due_at=now()-interval '6 minutes' WHERE tenant_id=%s::uuid AND state='queued'",(A,));db.commit()
    result=reliability.snapshot(PROFILE)
    assert result['metrics']['overdue']==1 and result['health']['state']=='degraded'
    assert result['metrics']['oldest_due_seconds']>=360
    other=reliability.snapshot({'tenant_id':B,'telegram_id':22})
    assert other['metrics']['overdue']==0 and not other['messages']
    db.execute("UPDATE saas_tenant_members SET role='admin' WHERE tenant_id=%s::uuid",(A,));db.commit()
    with pytest.raises(TenantLinearError):reliability.snapshot(PROFILE)
