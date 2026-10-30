"""Real isolated PostgreSQL regression: assignment is not Resume or sending."""
import json
import uuid
import pytest
from test_tenant_support_inbox import database, db, A, B, CA, CB, AGENT, PROFILE
from test_integrated_connect_release import flow
from test_connect_channel_isolation import channels, AGENT2
from test_tenant_operator_workspace import employee
import tenant_operator_assignment as assignment
import tenant_operator_dialogs as dialogs
import tenant_ai_agents_store as agents
import tenant_ai_agent_connect as connect
import tenant_member_store as members
import tenant_chat_send_store as sender
from tenant_linear_policy import TenantLinearError


@pytest.fixture
def old_dialog(channels, monkeypatch):
    f = channels
    for module in (assignment, members, sender):
        monkeypatch.setattr(module, '_connect', lambda: f['db'])
    f['db'].execute("DELETE FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND channel_id=%s", (A, CA))
    f['db'].execute("UPDATE saas_ai_conversation_state SET agent_id=NULL,state='human',reason='manager_replied' WHERE tenant_id=%s::uuid AND channel_id=%s", (A, CA))
    f['db'].execute("UPDATE saas_ai_agents SET mode='manual' WHERE tenant_id=%s::uuid", (A,))
    f['db'].commit()
    return f


def payload(f, **patch):
    data = dict(provider='connect', channel_id=CA, conversation_id='client', lead_id=41)
    options = assignment.options(PROFILE, data)
    return dict(**data, agent_id=AGENT, request_id=str(uuid.uuid4()), expected_revision=options['revision']) | patch


def test_assign_preserves_human_history_modes_and_other_channel(old_dialog):
    f = old_dialog; db = f['db']
    f['incoming'](CB)
    other = db.execute("SELECT * FROM saas_ai_agent_jobs WHERE channel_id=%s", (CB,)); db.commit()
    before = db.execute('SELECT * FROM saas_crm_messages ORDER BY id'); db.commit()
    data = payload(f)
    first = assignment.assign(PROFILE, data)
    retry = assignment.assign(PROFILE, data)
    assert first['state'] == retry['state'] == 'human' and retry['replayed']
    assert first['revision'] == retry['revision']
    assert db.execute("SELECT * FROM saas_ai_agent_jobs WHERE channel_id=%s", (CB,)) == other
    assert db.execute('SELECT * FROM saas_crm_messages ORDER BY id') == before
    assert len(db.execute("SELECT * FROM saas_tenant_audit_events WHERE action='ai_conversation_agent_configured'")) == 1
    assert all(a['mode'] == 'manual' for a in db.execute('SELECT mode FROM saas_ai_agents WHERE tenant_id=%s::uuid', (A,)))
    state = db.execute("SELECT * FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND channel_id=%s", (A, CA))[0]
    assert state['reason'] == 'manager_replied' and state['agent_id'] == AGENT
    db.commit(); f['send'].assert_not_called(); f['generate'].assert_not_called()
    page = dialogs.history(PROFILE, 'connect', 41, CA, 'client')
    assert page['dialog']['agent_id'] == AGENT and page['dialog']['available_actions'] == ['resume']


def test_saving_the_same_primary_agent_does_not_stop_active_dialog(old_dialog):
    f=old_dialog;data=payload(f);assignment.assign(PROFILE,data)
    agents.set_conversation_state(PROFILE,'client','active',provider='connect',channel_id=CA)
    current=assignment.options(PROFILE,{**data,'manage':'1'})
    result=assignment.assign(PROFILE,{**data,'request_id':str(uuid.uuid4()),'expected_revision':current['revision']})
    assert result['unchanged'] and result['state']=='active' and result['revision']==current['revision']


def test_manual_agent_stays_assigned_and_never_queues_background_reply(old_dialog):
    f = old_dialog; data = payload(f); old, queued = f['incoming'](CA)
    assert queued == 0
    assignment.assign(PROFILE, data)
    assert f['process']() is None
    agents.set_conversation_state(PROFILE, 'client', 'active', provider='connect', channel_id=CA)
    assert f['incoming'](CA, mid=old)[1] == 0 and f['process']() is None
    # Retrying assignment after Resume must not stop or resume the dialogue again.
    assert assignment.assign(PROFILE, data)['state'] == 'active'
    outcome=f['incoming'](CA)
    assert outcome[1] == 0
    f['process'](); f['send'].assert_not_called();f['generate'].assert_not_called()
    drafts = f['db'].execute('SELECT * FROM saas_ai_agent_drafts WHERE tenant_id=%s::uuid', (A,)); f['db'].commit()
    assert not drafts


@pytest.mark.parametrize('case', ['other_channel_agent','other_tenant_agent','wrong_channel','wrong_tenant','lead_mismatch','missing_channel','kommo','stale_revision','no_permission','inactive_member','suspended','quarantine'])
def test_assignment_rejects_wrong_scope_and_stale_authority(old_dialog, case):
    f = old_dialog; db = f['db']; data = payload(f); person = PROFILE
    if case == 'other_channel_agent': data['agent_id'] = AGENT2
    elif case == 'other_tenant_agent':
        foreign = str(uuid.uuid4())
        db.execute("INSERT INTO saas_ai_agents(id,tenant_id,name,created_by,enabled) VALUES(%s::uuid,%s::uuid,'Foreign',22,TRUE)", (foreign, B)); data['agent_id'] = foreign
    elif case == 'wrong_channel': data['channel_id'] = CB
    elif case == 'wrong_tenant': person = {'tenant_id': B, 'telegram_id': 22}
    elif case == 'lead_mismatch': data['lead_id'] = 43
    elif case == 'missing_channel': data.pop('channel_id')
    elif case == 'kommo': data['provider'] = 'kommo'
    elif case == 'stale_revision':
        connect.mark_operator_reply(A, 41, CA, 'client')
    elif case in {'no_permission','inactive_member'}:
        person = employee(db, [] if case == 'no_permission' else ['dialogs'])
        if case == 'inactive_member': db.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s::uuid AND telegram_id=33', (A,))
    elif case == 'suspended': db.execute("UPDATE saas_tenants SET status='suspended' WHERE id=%s::uuid", (A,))
    elif case == 'quarantine': db.execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,conversation_id,state) VALUES(%s::uuid,'connect','client','human')", (A,))
    db.commit()
    with pytest.raises(TenantLinearError): assignment.assign(person, data)
    assert not db.execute('SELECT * FROM saas_ai_conversation_state WHERE agent_assignment_id IS NOT NULL'); db.commit()
    f['send'].assert_not_called()


def test_group_assignment_does_not_require_company_auto_send(old_dialog):
    f = old_dialog; db = f['db']; data = payload(f)
    db.execute("UPDATE saas_connect_conversations SET conversation_kind='group' WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid", (A, CA)); db.commit()
    assert not assignment.options(PROFILE, data)['allowed']
    db.execute("UPDATE saas_ai_agents SET scopes=%s::jsonb WHERE id=%s::uuid", (json.dumps([{'provider':'connect','channel_id':CA,'allow_group_messages':True}]), AGENT)); db.commit()
    assert assignment.options(PROFILE, data)['allowed']
    db.execute("UPDATE saas_ai_agent_settings SET auto_send_enabled=FALSE WHERE tenant_id=%s::uuid", (A,)); db.commit()
    assert assignment.options(PROFILE, data)['allowed']
    assert db.execute('SELECT auto_send_enabled FROM saas_ai_agent_settings WHERE tenant_id=%s::uuid',(A,))[0]['auto_send_enabled'] is False
    assert db.execute('SELECT mode FROM saas_ai_agents WHERE id=%s::uuid',(AGENT,))[0]['mode']=='manual'
    assert not db.execute('SELECT * FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND channel_id=%s',(A,CA))
    f['send'].assert_not_called();db.commit()
    db.execute('UPDATE saas_ai_agents SET enabled=FALSE WHERE id=%s::uuid', (AGENT,)); db.commit()
    assert not assignment.options(PROFILE, data)['allowed']
    db.execute('UPDATE saas_ai_agent_settings SET emergency_stop=TRUE WHERE tenant_id=%s::uuid', (A,)); db.commit()
    assert assignment.options(PROFILE, data)['reason'] == 'emergency_stop'


def test_candidate_reason_identifies_scope_and_disabled_agent(old_dialog):
    f=old_dialog;db=f['db'];data=payload(f)
    db.execute('UPDATE saas_ai_agents SET scopes=%s::jsonb WHERE id=%s::uuid',
               (json.dumps([{'provider':'kommo','channel_id':'other'}]),AGENT));db.commit()
    assert assignment.options(PROFILE,data)['reason']=='no_connect_scope'
    db.execute('UPDATE saas_ai_agents SET scopes=%s::jsonb WHERE id=%s::uuid',
               (json.dumps([{'provider':'connect','channel_id':CA}]),AGENT));db.commit()
    assert assignment.options(PROFILE,data)['allowed']
    db.execute('UPDATE saas_ai_agents SET enabled=FALSE WHERE id=%s::uuid',(AGENT,));db.commit()
    assert assignment.options(PROFILE,data)['reason']=='agent_disabled'


def test_explicit_choice_survives_resume_even_with_multiple_matching_agents(old_dialog):
    f = old_dialog; db = f['db']
    db.execute('UPDATE saas_ai_agents SET scopes=%s::jsonb WHERE id=%s::uuid', (json.dumps([{'provider':'connect','channel_id':CA}]), AGENT2)); db.commit()
    db.execute("UPDATE saas_ai_agents SET mode='automatic' WHERE id=%s::uuid", (AGENT2,)); db.commit()
    data = payload(f, agent_id=AGENT2)
    assert {a['id'] for a in assignment.options(PROFILE, data)['agents']} == {AGENT, AGENT2}
    assignment.assign(PROFILE, data)
    agents.set_conversation_state(PROFILE, 'client', 'active', provider='connect', channel_id=CA)
    assert f['incoming'](CA)[1] == 1
    jobs = db.execute("SELECT * FROM saas_ai_agent_jobs WHERE channel_id=%s", (CA,)); db.commit()
    assert len(jobs) == 1 and jobs[0]['agent_id'] == AGENT2
    assert not connect.allowed(dict(jobs[0], agent_id=AGENT))
    f['process'](); f['send'].assert_called_once()
    db.execute('UPDATE saas_ai_agents SET enabled=FALSE WHERE id=%s::uuid', (AGENT2,)); db.commit()
    assert f['incoming'](CA)[1] == 0  # Never falls back to another matching agent.


@pytest.mark.parametrize('initial', ['paused','untracked'])
def test_pause_or_untracked_dialogue_stays_stopped_and_kommo_untouched(old_dialog, initial):
    f = old_dialog; db = f['db']
    db.execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,conversation_id,state) VALUES(%s::uuid,'kommo','client','paused')", (A,))
    if initial == 'untracked': db.execute('DELETE FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND channel_id=%s', (A, CA))
    else: db.execute("UPDATE saas_ai_conversation_state SET state='paused' WHERE tenant_id=%s::uuid AND channel_id=%s", (A, CA))
    db.commit()
    person = employee(db, ['dialogs'])
    result = assignment.assign(person, payload(f))
    assert result['state'] == 'paused'
    assert db.execute("SELECT state FROM saas_ai_conversation_state WHERE provider='kommo'")[0]['state'] == 'paused'; db.commit()
    with pytest.raises(TenantLinearError): agents.list_agents(person)


def test_primary_assignment_can_be_changed_or_removed_without_resuming(old_dialog,monkeypatch):
    f=old_dialog;db=f['db'];data=payload(f)
    import tenant_operator_dialogs as dialogs
    monkeypatch.setattr(dialogs,'_connect',lambda:db)
    assignment.assign(PROFILE,data)
    db.execute('UPDATE saas_ai_agents SET scopes=%s::jsonb WHERE id=%s::uuid',(json.dumps([{'provider':'connect','channel_id':CA}]),AGENT2));db.commit()
    current=assignment.options(PROFILE,{**data,'manage':'1'})
    assert current['current_agent_id']==AGENT and current['can_manage']
    changed=assignment.assign(PROFILE,{**data,'agent_id':AGENT2,'request_id':str(uuid.uuid4()),'expected_revision':current['revision']})
    assert changed['state']=='human' and changed['automatic_replies_enabled'] is False
    state=db.execute('SELECT agent_id,state,reason FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND channel_id=%s',(A,CA))[0]
    assert state['agent_id']==AGENT2 and state['state']=='human' and state['reason']=='manager_replied'
    removed=assignment.assign(PROFILE,{**data,'agent_id':None,'request_id':str(uuid.uuid4()),'expected_revision':changed['revision']})
    assert removed['state']=='human' and removed['agent_id'] is None
    assert db.execute('SELECT state,reason FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND channel_id=%s',(A,CA))[0]=={'state':'human','reason':'manager_replied'}
    detail=dialogs.history(PROFILE,'connect',41,CA,'client')['dialog']
    assert detail.get('agent_id') is None and detail['agent_status']=='needs_assignment'
    agents.delete_agent(PROFILE,AGENT,replacement_agent_id=AGENT2)
    f['send'].assert_not_called();f['generate'].assert_not_called()


def test_archive_transfers_dialog_atomically_and_preserves_history(old_dialog):
    f=old_dialog;db=f['db'];data=payload(f)
    assignment.assign(PROFILE,data)
    db.execute("UPDATE saas_ai_agents SET scopes=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",(json.dumps([{'provider':'connect','channel_id':CA}]),A,AGENT2))
    before=db.execute("SELECT id,direction,body FROM saas_crm_messages WHERE tenant_id=%s::uuid ORDER BY id",(A,));db.commit()
    result=agents.delete_agent(PROFILE,AGENT,replacement_agent_id=AGENT2)
    assert result['archived'] and result['transferred_dialogs'] >= 1
    source=db.execute("SELECT enabled,archived_at,archived_replaced_by FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,AGENT))[0]
    assert source['enabled'] is False and source['archived_at'] and str(source['archived_replaced_by'])==AGENT2
    state=db.execute("SELECT agent_id,state,reason FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id='client'",(A,CA))[0]
    assert str(state['agent_id'])==AGENT2 and state['state']=='human' and state['reason']=='agent_archived_transfer'
    assert db.execute("SELECT id,direction,body FROM saas_crm_messages WHERE tenant_id=%s::uuid ORDER BY id",(A,))==before
    assert AGENT not in [row['id'] for row in agents.list_agents(PROFILE)]
    assert f['process']() is None;f['send'].assert_not_called();f['generate'].assert_not_called()


def test_archive_rejects_recipient_without_channel_scope(old_dialog):
    f=old_dialog;db=f['db'];assignment.assign(PROFILE,payload(f))
    db.execute("UPDATE saas_ai_agents SET scopes=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",(json.dumps([{'provider':'connect','channel_id':CB}]),A,AGENT2));db.commit()
    with pytest.raises(TenantLinearError) as error:
        agents.delete_agent(PROFILE,AGENT,replacement_agent_id=AGENT2)
    assert error.value.status==409
    assert db.execute("SELECT archived_at FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,AGENT))[0]['archived_at'] is None


def test_permanent_delete_removes_agent_config_but_preserves_history_and_pauses_dialog(old_dialog,monkeypatch):
    f=old_dialog;db=f['db']
    db.execute("UPDATE saas_ai_conversation_state SET agent_id=%s::uuid,state='human',reason='manager_replied' WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s",(AGENT,A,CA));db.commit()
    db.execute("INSERT INTO saas_ai_agent_jobs(tenant_id,agent_id,provider,conversation_id,chat_id,channel_id,lead_id,event_fingerprint,talk_id,due_at) VALUES(%s::uuid,%s::uuid,'connect','client','client',%s,41,'evt','',now())",(A,AGENT,CA));db.commit()
    # Use a real completed run and a published company source as preserved dependents.
    run_id=str(uuid.uuid4())
    db.execute("INSERT INTO saas_ai_agent_runs(tenant_id,id,agent_id,event_fingerprint,revision,model,state,result) VALUES(%s::uuid,%s::uuid,%s::uuid,'evt',1,'test-model','completed','{\"answer\":\"kept\"}'::jsonb)",(A,run_id,AGENT));db.commit()
    usage_id=str(uuid.uuid4())
    db.execute("INSERT INTO saas_ai_agent_token_usage(tenant_id,id,run_id,agent_id,model,input_tokens,output_tokens,total_tokens) VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,'test-model',2,3,5)",(A,usage_id,run_id,AGENT));db.commit()
    draft_id=str(uuid.uuid4())
    db.execute("INSERT INTO saas_ai_agent_drafts(tenant_id,id,agent_id,run_id,lead_id,talk_id,chat_id,source_message_id,source_fingerprint,draft) VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,41,'','client','m1','evt','Preserved draft')",(A,draft_id,AGENT,run_id));db.commit()
    session_id=str(uuid.uuid4());turn_id=str(uuid.uuid4())
    db.execute("INSERT INTO saas_ai_test_sessions(tenant_id,id,agent_id,created_by,state) VALUES(%s::uuid,%s::uuid,%s::uuid,11,'paused')",(A,session_id,AGENT));db.commit()
    db.execute("INSERT INTO saas_ai_test_turns(tenant_id,id,session_id,input_hash,client_text,response,state) VALUES(%s::uuid,%s::uuid,%s::uuid,'fixture','question','answer','completed')",(A,turn_id,session_id));db.commit()
    source=agents.save_knowledge(PROFILE,{'title':'Shared guide','kind':'text','content':'Reusable verified guidance.','active':True,'agent_id':AGENT})
    db.commit()
    campaign_id=str(uuid.uuid4())
    db.execute('''INSERT INTO saas_knowledge_campaigns(tenant_id,id,created_by,sources,analysis_agent_id,status,
      approved_by,approved_at,max_calls,max_tokens,progress,media_policy,max_cost_microusd)
      VALUES(%s::uuid,%s::uuid,11,'[]'::jsonb,%s::uuid,'stopped',11,now(),1,24000,'{}'::jsonb,'{}'::jsonb,10000000)''',(A,campaign_id,AGENT));db.commit()
    current=db.execute('SELECT version FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,AGENT))[0]['version'];db.commit()
    preview=agents.preview_agent_deletion(PROFILE,AGENT)
    assert preview['confirmation_required'] and preview['irreversible']
    assert preview['impact']['assigned_dialogs']==1 and preview['impact']['runs']==1
    assert preview['impact']['knowledge_sources']==1
    with pytest.raises(TenantLinearError):
        agents.permanently_delete_agent(PROFILE,AGENT,expected_version=current+1,confirm_agent_id=AGENT)
    with pytest.raises(TenantLinearError):
        agents.permanently_delete_agent(PROFILE,AGENT,expected_version=current,confirm_agent_id=AGENT2)
    db.rollback()
    result=agents.permanently_delete_agent(PROFILE,AGENT,expected_version=current,confirm_agent_id=AGENT)
    assert result['deleted'] and result['paused_dialogs']==1
    db.commit()
    assert not db.execute('SELECT 1 FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,AGENT))
    assert db.execute('SELECT state FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND agent_id=%s::uuid',(A,AGENT))[0]['state']=='skipped'
    assert db.execute('SELECT result FROM saas_ai_agent_runs WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,run_id))[0]['result']=={'answer':'kept'}
    assert db.execute('SELECT total_tokens FROM saas_ai_agent_token_usage WHERE tenant_id=%s::uuid AND run_id=%s::uuid',(A,run_id))[0]['total_tokens']==5
    assert db.execute('SELECT draft,state FROM saas_ai_agent_drafts WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,draft_id))[0]=={'draft':'Preserved draft','state':'rejected'}
    assert db.execute('SELECT status,analysis_agent_id FROM saas_knowledge_campaigns WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,campaign_id))[0]=={'status':'stopped','analysis_agent_id':AGENT}
    assert db.execute('SELECT id FROM saas_ai_test_sessions WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,session_id))
    import tenant_ai_testing_store as testing
    monkeypatch.setattr(testing,'_connect',lambda:db)
    historical=testing.read(PROFILE,session_id)
    assert historical['session']['agent_name']=='Night support' and historical['session']['block_reason']=='agent_deleted'
    assert historical['turns'][0]['response']=='answer'
    assert db.execute('SELECT content,active FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,source['id']))[0]=={'content':'Reusable verified guidance.','active':True}
    identity=db.execute('SELECT display_name,deleted_at,deleted_by FROM saas_ai_agent_history_identities WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,AGENT))[0]
    assert identity['display_name']=='Night support' and identity['deleted_at'] and identity['deleted_by']==11
    knowledge=agents.list_knowledge(PROFILE,source['id'])
    assert knowledge['agent_name']=='Night support' and knowledge['content']=='Reusable verified guidance.'
    state=db.execute("SELECT agent_id,state,reason FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND channel_id=%s",(A,CA))[0]
    assert state['agent_id'] is None and state['state']=='human' and state['reason']=='agent_permanently_deleted'
    assert agents.permanently_delete_agent(PROFILE,AGENT,expected_version=current,confirm_agent_id=AGENT)['replayed'] is True


def test_permanent_delete_blocks_while_provider_work_is_processing(old_dialog):
    f=old_dialog;db=f['db']
    db.execute("INSERT INTO saas_ai_agent_jobs(tenant_id,agent_id,provider,conversation_id,chat_id,channel_id,lead_id,event_fingerprint,talk_id,due_at,state,lease_until) VALUES(%s::uuid,%s::uuid,'connect','client','client',%s,41,'evt','',now(),'processing',now()+interval '1 minute')",(A,AGENT,CA));db.commit()
    version=db.execute('SELECT version FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,AGENT))[0]['version'];db.commit()
    preview=agents.preview_agent_deletion(PROFILE,AGENT)
    assert preview['impact']['processing_jobs']==1
    with pytest.raises(TenantLinearError,match='выполняется задача'):
        agents.permanently_delete_agent(PROFILE,AGENT,expected_version=version,confirm_agent_id=AGENT)
    db.rollback()
    assert db.execute('SELECT 1 FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,AGENT))
    assert db.execute("SELECT state FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND agent_id=%s::uuid",(A,AGENT))[0]['state']=='processing'


def test_permanent_delete_blocks_while_case_media_campaign_is_active(old_dialog):
    f=old_dialog;db=f['db'];campaign_id=str(uuid.uuid4())
    db.execute('''INSERT INTO saas_knowledge_campaigns(tenant_id,id,created_by,sources,analysis_agent_id,status,
      approved_by,approved_at,max_calls,max_tokens,progress,media_policy,max_cost_microusd)
      VALUES(%s::uuid,%s::uuid,11,'[]'::jsonb,%s::uuid,'running',11,now(),1,24000,'{"mode":"case_media_batch"}'::jsonb,'{}'::jsonb,10000000)''',(A,campaign_id,AGENT));db.commit()
    version=db.execute('SELECT version FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,AGENT))[0]['version'];db.commit()
    preview=agents.preview_agent_deletion(PROFILE,AGENT)
    assert preview['impact']['active_campaigns']==1
    with pytest.raises(TenantLinearError,match='анализ'):
        agents.permanently_delete_agent(PROFILE,AGENT,expected_version=version,confirm_agent_id=AGENT)
    db.rollback()
    assert db.execute('SELECT status FROM saas_knowledge_campaigns WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,campaign_id))[0]['status']=='running'
    assert db.execute('SELECT 1 FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,AGENT))


def test_permanent_delete_blocks_while_direct_ai_run_is_generating(old_dialog):
    f=old_dialog;db=f['db'];run_id=str(uuid.uuid4())
    db.execute("INSERT INTO saas_ai_agent_runs(tenant_id,id,agent_id,event_fingerprint,revision,model,state) VALUES(%s::uuid,%s::uuid,%s::uuid,'evt',1,'fixture','generating')",(A,run_id,AGENT));db.commit()
    version=db.execute('SELECT version FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,AGENT))[0]['version'];db.commit()
    preview=agents.preview_agent_deletion(PROFILE,AGENT)
    assert preview['impact']['active_runs']==1 and preview['impact']['active_executions']==1
    with pytest.raises(TenantLinearError,match='AI-анализ'):
        agents.permanently_delete_agent(PROFILE,AGENT,expected_version=version,confirm_agent_id=AGENT)
    db.rollback()
    assert db.execute('SELECT state FROM saas_ai_agent_runs WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,run_id))[0]['state']=='generating'


def test_additive_migration_preserves_legacy_and_other_provider_states(old_dialog):
    import tenant_ai_agents_schema as schema
    db=old_dialog['db']
    db.execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,conversation_id,state) VALUES(%s::uuid,'connect','ambiguous','human'),(%s::uuid,'kommo','client','paused')",(A,A))
    db.execute('ALTER TABLE saas_ai_conversation_state DROP COLUMN agent_assignment_id')
    before=db.execute('SELECT * FROM saas_ai_conversation_state ORDER BY provider,channel_id,conversation_id');db.commit()
    schema._ready=False;schema.ensure_schema(db)
    after=db.execute('SELECT * FROM saas_ai_conversation_state ORDER BY provider,channel_id,conversation_id');db.commit()
    assert all(r['agent_assignment_id'] is None for r in after)
    assert [{k:v for k,v in r.items() if k!='agent_assignment_id'} for r in after]==before


def test_activation_changes_only_company_gate_and_does_not_replay_drafts(old_dialog):
    f = old_dialog; db = f['db']
    db.execute("UPDATE saas_tenants SET status='onboarding' WHERE id=%s::uuid", (A,)); db.commit()
    assignment.assign(PROFILE,payload(f))
    agents.set_conversation_state(PROFILE,'client','active',provider='connect',channel_id=CA)
    f['incoming'](CA);f['process']()
    assert db.execute("SELECT * FROM saas_ai_agent_drafts WHERE tenant_id=%s::uuid AND state='pending'",(A,))==[];db.commit()
    f['generate'].assert_not_called()
    assert dialogs.history(PROFILE,'connect',41,CA,'client')['dialog']['send_block_reason']=='tenant_not_active'
    tables = ['saas_ai_agents','saas_ai_agent_settings','saas_ai_conversation_state','saas_ai_agent_jobs','saas_ai_agent_drafts','saas_tenant_integrations','saas_connect_channels','saas_tenant_members']
    before = {t:db.execute('SELECT * FROM '+t+' ORDER BY to_jsonb('+t+')::text') for t in tables}; db.commit()
    members.finalize_onboarding(tenant_id=A, owner_id=11)
    assert db.execute('SELECT status FROM saas_tenants WHERE id=%s::uuid',(A,))[0]['status']=='active'
    assert {t:db.execute('SELECT * FROM '+t+' ORDER BY to_jsonb('+t+')::text') for t in tables}==before; db.commit()
    assert dialogs.history(PROFILE,'connect',41,CA,'client')['dialog']['can_send']
    with db.cursor() as cur: profile, connection, credentials = sender.authority(cur,PROFILE,41)
    db.commit()
    assert connection == ['connect',CA,'client'] and credentials['provider']=='connect'
    assert f['process']() is None
    f['send'].assert_not_called(); f['generate'].assert_not_called()


def test_assignment_http_requires_identity_origin_and_dialog_permission(monkeypatch):
    import asyncio
    from unittest.mock import Mock
    import tenant_operator_api as api
    from types import SimpleNamespace
    assign_mock=Mock(return_value={'state':'human'})
    monkeypatch.setattr(api,'assign_agent',assign_mock)
    handle=api.handler(lambda _:PROFILE,'https://crm.test',Mock(),'agent_assignment')
    body={'provider':'connect','expected_tenant_id':A,'expected_user_id':11}
    def request(data,origin='https://crm.test',size=None):
        raw=json.dumps(data).encode()
        async def read():return raw
        return SimpleNamespace(method='POST',headers={'Origin':origin},content_type='application/json',
                               content_length=len(raw) if size is None else size,query={},read=read)
    assert asyncio.run(handle(request(body,'https://foreign.test'))).status==403
    assert asyncio.run(handle(request({**body,'expected_tenant_id':B}))).status==409
    assert asyncio.run(handle(request(body,size=20001))).status==413
    assert asyncio.run(handle(request([]))).status==400
    assign_mock.assert_not_called()
    assert asyncio.run(handle(request(body))).status==200
    assert assign_mock.call_args.args==(PROFILE,{'provider':'connect'})


def test_assignment_conflict_distinguishes_verified_identity(monkeypatch):
    import asyncio
    from unittest.mock import Mock
    from types import SimpleNamespace
    import tenant_operator_api as api
    def conflict(*_): raise TenantLinearError('Legacy dialogue channel is ambiguous',409)
    monkeypatch.setattr(api,'assignment_options',conflict)
    handle=api.handler(lambda _:PROFILE,'https://crm.test',Mock(),'agent_assignment')
    def request(tenant):
        return SimpleNamespace(method='GET',headers={'X-Tenant-Id':tenant,'X-User-Id':'11'},query={})
    verified=asyncio.run(handle(request(A)))
    assert verified.status==409 and json.loads(verified.text)['scope_verified'] is True
    changed=asyncio.run(handle(request(B)))
    assert changed.status==409 and 'scope_verified' not in json.loads(changed.text)


def test_bulk_preview_is_owner_only_channel_and_agent_scoped_read_only(old_dialog,monkeypatch):
    from unittest.mock import Mock
    f=old_dialog;db=f['db'];monkeypatch.setattr(assignment,'_connect',lambda:db)
    row={'provider':'connect','lead_id':41,'channel_id':CA,'conversation_id':'client','agent_id':None,
         'agent_assignment':{'allowed':True,'revision':0,'agents':[{'id':AGENT,'name':'Night support'}]},
         'display_state':'unmanaged','state':'unmanaged','customer_kind':'contact','customer_name':'Test customer'}
    listing=Mock(return_value={'dialogs':[row],'next_offset':None})
    monkeypatch.setattr(dialogs,'listing',listing)
    before=db.execute('SELECT * FROM saas_ai_conversation_state');db.commit()
    preview=assignment.bulk_preview(PROFILE,CA,AGENT)
    after=db.execute('SELECT * FROM saas_ai_conversation_state');db.commit()
    assert preview['summary']['one_candidate']==1 and preview['summary']['assigned']==0
    assert preview['requires_owner_confirmation'] is True and len(preview['eligible'])==1
    assert preview['channel_id']==CA and preview['agent_id']==AGENT
    assert preview['automatic_replies_enabled'] is False
    assert listing.call_args.kwargs=={'channel':'connect:'+CA,'offset':0}
    assert before==after and f['generate'].call_count==0 and f['send'].call_count==0
    person=employee(db,['dialogs'],role='manager')
    with pytest.raises(TenantLinearError):assignment.bulk_preview(person,CA,AGENT)
    with pytest.raises(TenantLinearError):assignment.bulk_preview(PROFILE,'not-a-channel',AGENT)


def test_bulk_apply_requires_explicit_confirmation_and_is_idempotent(monkeypatch):
    from unittest.mock import Mock
    preview={'preview_token':'a'*64,'eligible':[{'provider':'connect','lead_id':41,'channel_id':CA,
        'conversation_id':'client','agent_id':AGENT,'agent_name':'Night support','customer_name':'Customer','revision':3}]}
    monkeypatch.setattr(assignment,'bulk_preview',lambda _profile,_channel,_agent:preview)
    applied=Mock(return_value={'replayed':False,'state':'human'})
    monkeypatch.setattr(assignment,'assign',applied)
    with pytest.raises(TenantLinearError):assignment.bulk_apply(PROFILE,{'confirmed':False,'preview_token':'a'*64,'batch_id':str(uuid.uuid4())})
    batch=str(uuid.uuid4());payload={'confirmed':True,'preview_token':'a'*64,'batch_id':batch}
    payload.update(channel_id=CA,agent_id=AGENT)
    assignment.bulk_apply(PROFILE,payload);first=applied.call_args.args[1]
    assignment.bulk_apply(PROFILE,payload);second=applied.call_args.args[1]
    assert first['request_id']==second['request_id'] and first['expected_revision']==3
    assert first['agent_id']==AGENT and applied.call_count==2


def test_bulk_preview_never_selects_one_of_multiple_agents(old_dialog,monkeypatch):
    from unittest.mock import Mock
    f=old_dialog;monkeypatch.setattr(assignment,'_connect',lambda:f['db'])
    row={'provider':'connect','lead_id':41,'channel_id':CA,'conversation_id':'client','agent_id':None,
         'unambiguous':True,'customer_kind':'contact','agent_assignment':{'allowed':True,'revision':0,'agents':[
             {'id':AGENT,'name':'First'},{'id':AGENT2,'name':'Second'}]}}
    monkeypatch.setattr(dialogs,'listing',Mock(return_value={'dialogs':[row],'next_offset':None}))
    preview=assignment.bulk_preview(PROFILE,CA,AGENT)
    assert preview['eligible']==[] and preview['summary']['multiple_candidates']==1
    assert preview['skipped_reasons']=={'multiple_candidates':1}
