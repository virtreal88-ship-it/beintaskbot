"""Cross-feature release boundaries, isolated PostgreSQL and mocked transports."""
import json
import uuid
from datetime import datetime, timezone
import pytest
from test_tenant_support_inbox import database, db, A, B, CA, AGENT, PROFILE
from test_integrated_connect_release import flow
import tenant_ai_agents_store as agents
import tenant_ai_agent_run_store as runs
import tenant_night_report_store as report
from tenant_linear_policy import TenantLinearError


def test_night_agent_is_disabled_and_knowledge_is_scoped_without_changing_existing_agent(flow,monkeypatch):
    f=flow;db=f['db']
    monkeypatch.setattr(agents,'platform_service_ready',lambda:True)
    monkeypatch.setattr(agents,'platform_model_allowed',lambda model:True)
    before=db.execute('SELECT * FROM saas_ai_agents WHERE id=%s::uuid',(AGENT,))[0]
    settings=db.execute('SELECT * FROM saas_ai_agent_settings WHERE tenant_id=%s::uuid',(A,))[0];db.commit()
    schedule={'timezone':'Asia/Baku','days':[0,1,2,3,4],'start':'22:00','end':'06:00'}
    created=agents.save_agent(PROFILE,{'name':'Night test','instructions':'Use known facts.','enabled':False,'mode':'manual','model':'test-model','schedule':schedule,'scopes':[{'provider':'connect','channel_id':CA}]})
    assert not created['enabled'] and created['mode']=='manual' and created['schedule']==schedule
    shared=agents.save_knowledge(PROFILE,{'title':'Shared','content':'router common help'})
    bound=agents.save_knowledge(PROFILE,{'title':'Private','content':'router night instructions','agent_id':created['id']})
    assert str(bound['agent_id'])==created['id']
    old_ids={r['source_id'] for r in runs.knowledge_context(A,'router',agent_id=AGENT)}
    new_ids={r['source_id'] for r in runs.knowledge_context(A,'router',agent_id=created['id'])}
    assert shared['id'] in old_ids and bound['id'] not in old_ids
    assert {shared['id'],bound['id']}<=new_ids
    assert not runs.knowledge_context(B,'router',agent_id=created['id'])
    with pytest.raises(TenantLinearError):agents.save_knowledge({'tenant_id':B,'telegram_id':22},{'title':'Foreign','content':'router','agent_id':created['id']})
    with pytest.raises(TenantLinearError):agents.save_knowledge(PROFILE,{'agent_id':AGENT},source_id=bound['id'])
    assert db.execute('SELECT * FROM saas_ai_agents WHERE id=%s::uuid',(AGENT,))[0]==before
    assert db.execute('SELECT * FROM saas_ai_agent_settings WHERE tenant_id=%s::uuid',(A,))[0]==settings


def test_report_uses_durable_connect_error_after_queue_advances(flow,monkeypatch):
    f=flow;db=f['db'];monkeypatch.setattr(report,'_connect',lambda:db)
    f['inbound']();job=runs.claim_next();receipt=runs.reserve_run(job,pipeline_id='',stage_id='',model='test-model')
    runs.finish_unknown(job,receipt,'connect_send_unknown')
    db.execute("UPDATE saas_ai_agent_runs SET created_at='2026-10-15T22:00Z',updated_at='2026-10-15T22:01Z'")
    db.execute("UPDATE saas_tenant_webhook_events SET received_at='2026-10-15T22:00Z',completed_at='2026-10-15T22:01Z'");db.commit()
    f['inbound']()
    result=report.report(PROFILE,'2026-10-15','UTC','19:00','09:00',now=datetime(2026,10,16,12,tzinfo=timezone.utc))
    assert result['metrics']['connect_errors']==1 and result['metrics']['ai_errors']==0
    assert result['data_quality']['errors_complete'] is False
    assert result['failures'][0]['code']=='send_unknown'
    f['send'].assert_not_called()


def test_report_retains_pre_reservation_failure_after_job_replacement(flow,monkeypatch):
    f=flow;db=f['db'];monkeypatch.setattr(report,'_connect',lambda:db)
    f['inbound']();job=runs.claim_next()
    db.execute('UPDATE saas_ai_agent_jobs SET attempts=5');db.commit()
    runs.retry_or_fail(job,'read_or_context_failed')
    db.execute("UPDATE saas_tenant_webhook_events SET received_at='2026-10-15T22:00Z',completed_at='2026-10-15T22:01Z'");db.commit()
    f['inbound']()
    result=report.report(PROFILE,'2026-10-15','UTC','19:00','09:00',now=datetime(2026,10,16,12,tzinfo=timezone.utc))
    assert result['metrics']['ai_errors']==1 and result['metrics']['connect_errors']==0


def test_report_counts_failed_manual_draft_send(flow,monkeypatch):
    f=flow;db=f['db'];monkeypatch.setattr(report,'_connect',lambda:db)
    db.execute("UPDATE saas_ai_agents SET mode='manual'");db.commit()
    f['inbound']();f['process']()
    draft=db.execute('SELECT id FROM saas_ai_agent_drafts')[0]['id'];db.commit()
    agents.reserve_draft_send(PROFILE,draft)
    agents.finish_draft_send(A,draft,'unknown')
    db.execute("UPDATE saas_ai_agent_drafts SET send_started_at='2026-10-15T22:01Z'")
    db.execute("UPDATE saas_ai_agent_runs SET created_at='2026-10-15T22:00Z',updated_at='2026-10-15T22:01Z'");db.commit()
    result=report.report(PROFILE,'2026-10-15','UTC','19:00','09:00',now=datetime(2026,10,16,12,tzinfo=timezone.utc))
    assert result['metrics']['connect_errors']==1 and result['metrics']['ai_errors']==0
    f['send'].assert_not_called()
