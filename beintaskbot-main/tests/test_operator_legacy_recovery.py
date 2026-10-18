"""Isolated PostgreSQL; no production data, model or transport calls."""
import pytest
from test_tenant_support_inbox import database, db, A, B, CA, CB, AGENT, PROFILE
from test_integrated_connect_release import flow
from test_tenant_operator_workspace import employee
from tenant_linear_policy import TenantLinearError
import tenant_operator_legacy as recovery
import tenant_ai_agents_store as agents
import tenant_ai_agent_connect as connect

DATA = dict(provider='connect', channel_id=CA, conversation_id='client', lead_id=41)


@pytest.fixture
def legacy(flow, monkeypatch):
    d = flow['db']
    d.execute('DELETE FROM saas_ai_channel_legacy_state')
    monkeypatch.setattr(recovery, '_connect', lambda: d)
    d.execute("UPDATE saas_ai_conversation_state SET channel_id='',state='human',reason='manager_replied' WHERE tenant_id=%s::uuid", (A,))
    d.execute("UPDATE saas_ai_agents SET mode='manual' WHERE tenant_id=%s::uuid", (A,))
    d.commit()
    return flow


def payload():
    return {**DATA, 'confirmed': True, 'expected_token': recovery.options(PROFILE, DATA)['expected_token']}


def test_restore_archives_original_and_resume_is_separate(legacy):
    f=legacy; d=f['db']
    original=d.execute("SELECT * FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid", (A,))[0]
    jobs=d.execute('SELECT * FROM saas_ai_agent_jobs');d.commit()
    data=payload()
    assert recovery.recover(PROFILE,data)['state']=='human'
    assert d.execute('SELECT * FROM saas_ai_agent_jobs')==jobs
    archive=d.execute('SELECT original FROM saas_ai_channel_legacy_state')[0]['original']
    assert archive['channel_id']=='' and archive['revision']==original['revision']
    d.commit()
    with pytest.raises(TenantLinearError):recovery.recover(PROFILE,data)
    assert f['process']() is None
    old, count = f['inbound']()
    assert count == 0
    agents.set_conversation_state(PROFILE,'client','active','resume','connect',CA)
    assert connect.enqueue(A,channel_id=CA,conversation_id='client',lead_id=41,message_id=old,event_id=old,text='replay') == 0
    assert f['process']() is None
    f['inbound']();f['process']()
    f['send'].assert_not_called()
    assert len(d.execute("SELECT * FROM saas_ai_agent_drafts WHERE state='pending'"))==1
    d.commit()


@pytest.mark.parametrize('case,reason',[
    ('two_channels','multiple_channels'),('scoped_state','conflicting_state'),
    ('job_channel','conflicting_evidence'),('job_agent','conflicting_evidence'),
    ('queued','outstanding_work'),('unknown','outstanding_work'),
    ('no_jobs','insufficient_evidence'),('agent_scope','legacy_agent_scope_mismatch'),
    ('event_channel','conflicting_evidence'),('archive','conflicting_evidence'),('group','group_requires_review')])
def test_ambiguous_or_unsafe_is_never_recovered(legacy,case,reason):
    d=legacy['db']
    if case=='two_channels':
        d.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'Second')",(A,CB))
        d.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id) VALUES(%s::uuid,%s::uuid,'client',43)",(A,CB))
    elif case=='scoped_state':d.execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,channel_id,conversation_id,state) VALUES(%s::uuid,'connect',%s,'client','human')",(A,CA))
    elif case=='job_channel':d.execute('UPDATE saas_ai_agent_jobs SET channel_id=%s',(CB,))
    elif case=='job_agent':
        d.execute("INSERT INTO saas_ai_agents(tenant_id,id,name,created_by) VALUES(%s::uuid,%s::uuid,'Other',11)",(A,CB))
        d.execute('UPDATE saas_ai_agent_jobs SET agent_id=%s::uuid',(CB,))
    elif case in {'queued','unknown'}:d.execute('UPDATE saas_ai_agent_jobs SET state=%s',(case,))
    elif case=='no_jobs':d.execute('DELETE FROM saas_ai_agent_jobs')
    elif case=='agent_scope':d.execute("UPDATE saas_ai_agents SET scopes='[]'::jsonb")
    elif case=='event_channel':d.execute("UPDATE saas_tenant_webhook_events SET payload=jsonb_build_object('conversation_id','client','channel_id',%s::text)",(CB,))
    elif case=='archive':d.execute("INSERT INTO saas_ai_channel_legacy_state(tenant_id,provider,conversation_id,original,resolved_channel,reason) VALUES(%s::uuid,'connect','client','{}'::jsonb,%s,'old')",(A,CB))
    elif case=='group':d.execute("UPDATE saas_connect_conversations SET conversation_kind='group'")
    d.commit()
    result=recovery.options(PROFILE,DATA)
    assert not result['allowed'] and result['reason']==reason
    with pytest.raises(TenantLinearError):recovery.recover(PROFILE,{**DATA,'confirmed':True,'expected_token':result['expected_token']})
    assert d.execute("SELECT count(*) n FROM saas_ai_conversation_state WHERE channel_id=''")[0]['n']==1
    d.commit();legacy['send'].assert_not_called();legacy['generate'].assert_not_called()


def test_permissions_tenant_route_confirmation_and_stale_preview(legacy):
    d=legacy['db']; data=payload()
    for person in ({'tenant_id':B,'telegram_id':22},employee(d,['dialogs','ai_agents_manage'])):
        with pytest.raises(TenantLinearError):recovery.options(person,DATA)
    with pytest.raises(TenantLinearError):recovery.options(PROFILE,{**DATA,'channel_id':CB})
    with pytest.raises(TenantLinearError):recovery.recover(PROFILE,{**data,'confirmed':False})
    d.execute("UPDATE saas_ai_conversation_state SET revision=revision+1");d.commit()
    with pytest.raises(TenantLinearError):recovery.recover(PROFILE,data)


def test_active_legacy_is_paused_not_resumed(legacy):
    d=legacy['db'];d.execute("UPDATE saas_ai_conversation_state SET state='active'");d.commit()
    assert recovery.recover(PROFILE,payload())['state']=='paused'
    assert legacy['process']() is None
    legacy['send'].assert_not_called()


def test_pending_draft_blocks_recovery_without_modifying_it(legacy):
    f=legacy;d=f['db']
    d.execute("UPDATE saas_ai_conversation_state SET channel_id=%s,state='active'",(CA,));d.commit()
    f['inbound']();f['process']()
    before=d.execute('SELECT * FROM saas_ai_agent_drafts')
    assert len(before)==1
    d.execute("UPDATE saas_ai_conversation_state SET channel_id='',state='human'")
    d.execute("UPDATE saas_ai_agent_jobs SET state='skipped'");d.commit()
    assert recovery.options(PROFILE,DATA)['reason']=='outstanding_work'
    assert d.execute('SELECT * FROM saas_ai_agent_drafts')==before
    d.commit();f['send'].assert_not_called()
