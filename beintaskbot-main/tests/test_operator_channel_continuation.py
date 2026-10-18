"""Real SQL lifecycle on isolated PostgreSQL, all model/transport calls mocked."""
import pytest
from test_tenant_support_inbox import database, db, A, B, CA, CB, AGENT, PROFILE
from test_integrated_connect_release import flow
from test_connect_channel_isolation import channels, AGENT2
from test_tenant_operator_workspace import employee
from tenant_linear_policy import TenantLinearError
import tenant_operator_continuation as continuation
import tenant_ai_agents_store as agents
import tenant_ai_agent_connect as connect
import tenant_ai_agent_run_store as runs

DATA=dict(provider='connect',channel_id=CA,conversation_id='client',lead_id=41)

@pytest.fixture
def stopped(channels,monkeypatch):
    f=channels;d=f['db']
    monkeypatch.setattr(continuation,'_connect',lambda:d)
    d.execute('DELETE FROM saas_ai_legacy_permits')
    d.execute('DELETE FROM saas_ai_channel_legacy_state')
    d.execute("UPDATE saas_ai_agents SET mode='manual'")
    d.execute("UPDATE saas_ai_conversation_state SET state='human',reason='manager_replied',agent_id=NULL")
    d.execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,channel_id,conversation_id,state,reason,revision,agent_id) VALUES(%s::uuid,'connect','','client','human','manager_replied',22,%s::uuid),(%s::uuid,'connect',%s,'client','human','manager_replied',9,NULL)",(A,AGENT,A,CB))
    d.commit()
    return f

def request():
    return {**DATA,'agent_id':AGENT,'confirmed':True,'expected_token':continuation.options(PROFILE,DATA)['expected_token']}

def blocked(d,channel=CA,fingerprint=None):
    value=d.execute('SELECT ai_legacy_blocked(%s::uuid,%s,\'client\',%s) b',(A,channel,fingerprint))[0]['b'];d.commit();return value

def test_channel_only_preserves_legacy_human_manual_and_requires_new_message(stopped):
    f=stopped;d=f['db']
    old=d.execute("SELECT * FROM saas_ai_conversation_state WHERE channel_id=''")[0];d.commit()
    before,n=f['incoming'](CA);assert n==0
    assert blocked(d) and blocked(d,CB)
    data=request();assert continuation.grant(PROFILE,data)['resume_required'] is True
    assert not blocked(d) and blocked(d,CB)
    assert d.execute("SELECT * FROM saas_ai_conversation_state WHERE channel_id=''")[0]==old
    assert d.execute('SELECT original FROM saas_ai_channel_legacy_state')[0]['original']['revision']==22
    assert all(r['state']=='human' for r in d.execute('SELECT state FROM saas_ai_conversation_state'))
    assert all(r['mode']=='manual' for r in d.execute('SELECT mode FROM saas_ai_agents'));d.commit()
    with pytest.raises(TenantLinearError):continuation.grant(PROFILE,data)
    assert f['process']() is None
    assert f['incoming'](CA)[1]==0
    agents.set_conversation_state(PROFILE,'client','active','resume','connect',CA)
    with pytest.raises(TenantLinearError):agents.set_conversation_state(PROFILE,'client','active','resume','connect',CB)
    assert f['incoming'](CA,mid=before)[1]==0
    assert f['incoming'](CB)[1]==0
    assert f['process']() is None
    assert f['incoming'](CA)[1]==1
    f['process']()
    assert len(d.execute("SELECT id FROM saas_ai_agent_drafts WHERE state='pending'"))==1;d.commit()
    f['send'].assert_not_called();f['generate'].assert_called_once()

@pytest.mark.parametrize('change',['legacy','channel','agent'])
def test_revision_or_binding_change_revokes_permission(stopped,change):
    d=stopped['db'];continuation.grant(PROFILE,request())
    if change=='legacy':d.execute("UPDATE saas_ai_conversation_state SET revision=revision+1 WHERE channel_id=''")
    elif change=='channel':d.execute('UPDATE saas_ai_conversation_state SET revision=revision+1 WHERE channel_id=%s',(CA,))
    else:d.execute('UPDATE saas_ai_conversation_state SET agent_id=%s::uuid WHERE channel_id=%s',(AGENT2,CA))
    d.commit();assert blocked(d)
    with pytest.raises(TenantLinearError):agents.set_conversation_state(PROFILE,'client','active','resume','connect',CA)
    assert stopped['incoming'](CA)[1]==0;stopped['send'].assert_not_called()

@pytest.mark.parametrize('state',['queued','processing','unknown','drafted'])
def test_outstanding_work_is_not_modified(stopped,state):
    d=stopped['db'];d.execute('UPDATE saas_ai_agent_jobs SET state=%s',(state,));d.commit()
    preview=continuation.options(PROFILE,DATA);assert preview['reason']=='outstanding_work'
    with pytest.raises(TenantLinearError):continuation.grant(PROFILE,{**request(),'expected_token':preview['expected_token']})
    assert d.execute('SELECT state FROM saas_ai_agent_jobs')[0]['state']==state;d.commit()

def test_permissions_scope_explicit_choice_and_stale_confirmation(stopped):
    d=stopped['db'];data=request()
    for profile in ({'tenant_id':B,'telegram_id':22},employee(d,['dialogs','ai_agents_manage'])):
        with pytest.raises(TenantLinearError):continuation.options(profile,DATA)
    for patch in ({'agent_id':AGENT2},{'confirmed':False},{'channel_id':CB},{'lead_id':43}):
        with pytest.raises(TenantLinearError):continuation.grant(PROFILE,{**data,**patch})
    d.execute('UPDATE saas_ai_conversation_state SET revision=revision+1 WHERE channel_id=%s',(CA,));d.commit()
    with pytest.raises(TenantLinearError):continuation.grant(PROFILE,data)
    assert blocked(d)

def test_manual_takeover_revokes_and_does_not_touch_other_channel(stopped):
    f=stopped;d=f['db'];continuation.grant(PROFILE,request())
    agents.set_conversation_state(PROFILE,'client','active','resume','connect',CA)
    f['incoming'](CA)
    connect.mark_operator_reply(A,41,CA,'client')
    assert blocked(d) and blocked(d,CB)
    assert f['process']() is None
    assert d.execute('SELECT revision FROM saas_ai_conversation_state WHERE channel_id=%s',(CB,))[0]['revision']==9;d.commit()
    f['send'].assert_not_called()

def test_old_source_with_new_webhook_fingerprint_still_cannot_run(stopped):
    f=stopped;d=f['db']
    mid,_=f['incoming'](CA)
    d.execute('DELETE FROM saas_ai_agent_jobs')
    d.execute('DELETE FROM saas_tenant_webhook_events');d.commit()
    continuation.grant(PROFILE,request())
    agents.set_conversation_state(PROFILE,'client','active','resume','connect',CA)
    assert f['incoming'](CA,mid=mid)[1]==0
    assert f['process']() is None;f['send'].assert_not_called();f['generate'].assert_not_called()

def test_automatic_fresh_replies_renew_checked_revision_without_double_send(stopped):
    f=stopped;d=f['db']
    d.execute("UPDATE saas_ai_agents SET mode='automatic' WHERE id=%s::uuid",(AGENT,));d.commit()
    continuation.grant(PROFILE,request())
    agents.set_conversation_state(PROFILE,'client','active','resume','connect',CA)
    for _ in range(2):
        mid,n=f['incoming'](CA);assert n==1
        f['process']();assert not blocked(d)
        assert f['incoming'](CA,mid=mid)[1]==0
        assert f['process']() is None
    assert f['send'].call_count==2 and blocked(d,CB)

@pytest.mark.parametrize('change',['legacy','takeover'])
def test_intervention_during_generation_cancels_stale_response(stopped,change):
    f=stopped;d=f['db'];d.execute("UPDATE saas_ai_agents SET mode='automatic'");d.commit()
    continuation.grant(PROFILE,request());agents.set_conversation_state(PROFILE,'client','active','resume','connect',CA)
    def intervene(*args,**kwargs):
        if change=='legacy':d.execute("UPDATE saas_ai_conversation_state SET revision=revision+1 WHERE channel_id=''");d.commit()
        else:connect.mark_operator_reply(A,41,CA,'client')
        return 'stale',{'total_tokens':1}
    f['generate'].side_effect=intervene
    f['incoming'](CA);f['process']();f['send'].assert_not_called();assert blocked(d)

def test_final_transport_rechecks_permission_after_draft_reservation(stopped):
    import tenant_connect_transport as transport
    f=stopped;d=f['db'];continuation.grant(PROFILE,request())
    agents.set_conversation_state(PROFILE,'client','active','resume','connect',CA)
    f['incoming'](CA);f['process']()
    draft=d.execute("SELECT id FROM saas_ai_agent_drafts WHERE state='pending'")[0]['id'];d.commit()
    reserved=agents.reserve_draft_send(PROFILE,draft)
    d.execute("UPDATE saas_ai_conversation_state SET revision=revision+1 WHERE channel_id=''");d.commit()
    assert transport.send_ai_reply_if_active(A,41,CA,'client','stale',draft,reserved['expected_revision']) is None
    f['send'].assert_not_called()

def test_pending_old_draft_and_unknown_run_prevent_grant(stopped):
    f=stopped;d=f['db'];continuation.grant(PROFILE,request())
    agents.set_conversation_state(PROFILE,'client','active','resume','connect',CA)
    f['incoming'](CA);f['process']();connect.mark_operator_reply(A,41,CA,'client')
    # Even a historical draft made pending again cannot acquire send permission.
    d.execute("UPDATE saas_ai_agent_drafts SET state='pending'");d.commit()
    draft=d.execute('SELECT id FROM saas_ai_agent_drafts')[0]['id'];d.commit()
    assert continuation.options(PROFILE,DATA)['reason']=='outstanding_work'
    with pytest.raises(TenantLinearError):agents.reserve_draft_send(PROFILE,draft)
    d.execute("UPDATE saas_ai_agent_drafts SET state='superseded'")
    d.execute("UPDATE saas_ai_agent_jobs SET state='skipped'")
    d.execute("UPDATE saas_ai_agent_runs SET state='unknown'");d.commit()
    assert continuation.options(PROFILE,DATA)['reason']=='outstanding_work'
    f['send'].assert_not_called()

def test_recreated_pending_old_draft_is_rejected_after_new_grant_and_resume(stopped):
    f=stopped;d=f['db'];continuation.grant(PROFILE,request())
    agents.set_conversation_state(PROFILE,'client','active','resume','connect',CA)
    f['incoming'](CA);f['process']();connect.mark_operator_reply(A,41,CA,'client')
    d.execute("UPDATE saas_ai_agent_drafts SET state='superseded'")
    d.execute("UPDATE saas_ai_agent_jobs SET state='skipped'");d.commit()
    continuation.grant(PROFILE,request());agents.set_conversation_state(PROFILE,'client','active','resume','connect',CA)
    d.execute("UPDATE saas_ai_agent_drafts SET state='pending'")
    draft=d.execute('SELECT id FROM saas_ai_agent_drafts')[0]['id'];d.commit()
    with pytest.raises(TenantLinearError):agents.reserve_draft_send(PROFILE,draft)
    f['send'].assert_not_called()

def test_schema_rerun_preserves_original_and_permit(stopped):
    from tenant_ai_legacy_permit import schema
    d=stopped['db'];continuation.grant(PROFILE,request())
    before=d.execute('SELECT * FROM saas_ai_legacy_permits');old=d.execute("SELECT * FROM saas_ai_conversation_state WHERE channel_id=''");d.commit()
    with d.cursor() as cur:schema(cur)
    d.commit()
    assert d.execute('SELECT * FROM saas_ai_legacy_permits')==before
    assert d.execute("SELECT * FROM saas_ai_conversation_state WHERE channel_id=''")==old;d.commit()


@pytest.mark.parametrize('case',['disabled','expert','wrong_scope','group'])
def test_ineligible_agents_and_groups_cannot_receive_permission(stopped,case):
    d=stopped['db']
    if case=='group':
        d.execute("UPDATE saas_connect_conversations SET conversation_kind='group' WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid",(A,CA));d.commit()
        with pytest.raises(TenantLinearError):continuation.options(PROFILE,DATA)
        return
    if case=='disabled':d.execute('UPDATE saas_ai_agents SET enabled=FALSE WHERE id=%s::uuid',(AGENT,))
    elif case=='expert':d.execute("UPDATE saas_ai_agents SET role='expert' WHERE id=%s::uuid",(AGENT,))
    else:d.execute("UPDATE saas_ai_agents SET scopes='[]'::jsonb WHERE id=%s::uuid",(AGENT,))
    d.commit();assert continuation.options(PROFILE,DATA)['reason']=='no_eligible_agent'
    with pytest.raises(TenantLinearError):continuation.grant(PROFILE,request())
    assert blocked(d)

def test_existing_archive_is_never_overwritten(stopped):
    d=stopped['db']
    d.execute("INSERT INTO saas_ai_channel_legacy_state(tenant_id,provider,conversation_id,original,reason) VALUES(%s::uuid,'connect','client','{\"revision\":7}'::jsonb,'prior_archive')",(A,));d.commit()
    before=d.execute('SELECT * FROM saas_ai_channel_legacy_state');d.commit()
    continuation.grant(PROFILE,request())
    assert d.execute('SELECT * FROM saas_ai_channel_legacy_state')==before;d.commit()
