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
    assert len(db.execute("SELECT * FROM saas_tenant_audit_events WHERE action='ai_conversation_agent_assigned'")) == 1
    assert all(a['mode'] == 'manual' for a in db.execute('SELECT mode FROM saas_ai_agents WHERE tenant_id=%s::uuid', (A,)))
    state = db.execute("SELECT * FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND channel_id=%s", (A, CA))[0]
    assert state['reason'] == 'manager_replied' and state['agent_id'] == AGENT
    db.commit(); f['send'].assert_not_called(); f['generate'].assert_not_called()
    page = dialogs.history(PROFILE, 'connect', 41, CA, 'client')
    assert page['dialog']['agent_id'] == AGENT and page['dialog']['available_actions'] == ['resume']


def test_resume_is_separate_and_only_future_inbound_creates_manual_draft(old_dialog):
    f = old_dialog; data = payload(f); old, queued = f['incoming'](CA)
    assert queued == 0
    assignment.assign(PROFILE, data)
    assert f['process']() is None
    agents.set_conversation_state(PROFILE, 'client', 'active', provider='connect', channel_id=CA)
    assert f['incoming'](CA, mid=old)[1] == 0 and f['process']() is None
    # Retrying assignment after Resume must not stop or resume the dialogue again.
    assert assignment.assign(PROFILE, data)['state'] == 'active'
    assert f['incoming'](CA)[1] == 1
    f['process'](); f['send'].assert_not_called()
    drafts = f['db'].execute('SELECT * FROM saas_ai_agent_drafts WHERE tenant_id=%s::uuid', (A,)); f['db'].commit()
    assert len(drafts) == 1 and drafts[0]['agent_id'] == AGENT


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


def test_group_opt_in_test_binding_disabled_agents_and_company_stop(old_dialog):
    f = old_dialog; db = f['db']; data = payload(f)
    db.execute("UPDATE saas_connect_conversations SET conversation_kind='group' WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid", (A, CA)); db.commit()
    assert not assignment.options(PROFILE, data)['allowed']
    db.execute("UPDATE saas_ai_agents SET scopes=%s::jsonb WHERE id=%s::uuid", (json.dumps([{'provider':'connect','channel_id':CA,'allow_group_messages':True}]), AGENT)); db.commit()
    assert assignment.options(PROFILE, data)['allowed']
    db.execute("UPDATE saas_ai_agent_settings SET operating_mode='test' WHERE tenant_id=%s::uuid", (A,)); db.commit()
    assert not assignment.options(PROFILE, data)['allowed']
    db.execute("INSERT INTO saas_ai_agent_connect_test_mode(tenant_id,enabled,verified,channel_id,conversation_id,agent_id) VALUES(%s::uuid,TRUE,TRUE,%s,'client',%s::uuid)", (A, CA, AGENT)); db.commit()
    assert assignment.options(PROFILE, data)['allowed']
    db.execute('UPDATE saas_ai_agents SET enabled=FALSE WHERE id=%s::uuid', (AGENT,)); db.commit()
    assert not assignment.options(PROFILE, data)['allowed']
    db.execute('UPDATE saas_ai_agent_settings SET emergency_stop=TRUE WHERE tenant_id=%s::uuid', (A,)); db.commit()
    assert assignment.options(PROFILE, data)['reason'] == 'emergency_stop'


def test_explicit_choice_survives_resume_even_with_multiple_matching_agents(old_dialog):
    f = old_dialog; db = f['db']
    db.execute('UPDATE saas_ai_agents SET scopes=%s::jsonb WHERE id=%s::uuid', (json.dumps([{'provider':'connect','channel_id':CA}]), AGENT2)); db.commit()
    data = payload(f, agent_id=AGENT2)
    assert {a['id'] for a in assignment.options(PROFILE, data)['agents']} == {AGENT, AGENT2}
    assignment.assign(PROFILE, data)
    agents.set_conversation_state(PROFILE, 'client', 'active', provider='connect', channel_id=CA)
    assert f['incoming'](CA)[1] == 1
    jobs = db.execute("SELECT * FROM saas_ai_agent_jobs WHERE channel_id=%s", (CA,)); db.commit()
    assert len(jobs) == 1 and jobs[0]['agent_id'] == AGENT2
    assert not connect.allowed(dict(jobs[0], agent_id=AGENT))
    f['process'](); f['send'].assert_not_called()
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
    assert result['state'] == ('paused' if initial == 'paused' else 'human')
    assert db.execute("SELECT state FROM saas_ai_conversation_state WHERE provider='kommo'")[0]['state'] == 'paused'; db.commit()
    with pytest.raises(TenantLinearError): agents.list_agents(person)


def test_competing_assignment_cannot_overwrite_binding(old_dialog):
    f = old_dialog; data = payload(f)
    assignment.assign(PROFILE, data)
    with pytest.raises(TenantLinearError): assignment.assign(PROFILE, {**data,'request_id':str(uuid.uuid4())})
    with pytest.raises(TenantLinearError): assignment.assign(PROFILE, {**data,'agent_id':AGENT2})
    with pytest.raises(TenantLinearError): agents.delete_agent(PROFILE, AGENT)
    assert assignment.assign(PROFILE, data)['state']=='human'


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
    assert len(db.execute("SELECT * FROM saas_ai_agent_drafts WHERE tenant_id=%s::uuid AND state='pending'",(A,)))==1;db.commit()
    f['generate'].reset_mock()
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


def test_bulk_preview_is_owner_only_and_read_only(old_dialog,monkeypatch):
    from unittest.mock import Mock
    f=old_dialog;db=f['db'];monkeypatch.setattr(assignment,'_connect',lambda:db)
    row={'provider':'connect','lead_id':41,'channel_id':CA,'conversation_id':'client','agent_id':None,
         'agent_assignment':{'allowed':True,'revision':0,'agents':[{'id':AGENT,'name':'Night support'}]},
         'display_state':'unmanaged','state':'unmanaged','customer_kind':'contact','customer_name':'Test customer'}
    listing=Mock(return_value={'dialogs':[row],'next_offset':None})
    monkeypatch.setattr(dialogs,'listing',listing)
    before=db.execute('SELECT * FROM saas_ai_conversation_state');db.commit()
    preview=assignment.bulk_preview(PROFILE)
    after=db.execute('SELECT * FROM saas_ai_conversation_state');db.commit()
    assert preview['summary']['one_candidate']==1 and preview['summary']['assigned']==0
    assert preview['requires_owner_confirmation'] is True and len(preview['eligible'])==1
    assert before==after and f['generate'].call_count==0 and f['send'].call_count==0
    person=employee(db,['dialogs'],role='manager')
    with pytest.raises(TenantLinearError):assignment.bulk_preview(person)


def test_bulk_apply_requires_explicit_confirmation_and_is_idempotent(monkeypatch):
    from unittest.mock import Mock
    preview={'preview_token':'a'*64,'eligible':[{'provider':'connect','lead_id':41,'channel_id':CA,
        'conversation_id':'client','agent_id':AGENT,'agent_name':'Night support','customer_name':'Customer','revision':3}]}
    monkeypatch.setattr(assignment,'bulk_preview',lambda _profile:preview)
    applied=Mock(return_value={'replayed':False,'state':'human'})
    monkeypatch.setattr(assignment,'assign',applied)
    with pytest.raises(TenantLinearError):assignment.bulk_apply(PROFILE,{'confirmed':False,'preview_token':'a'*64,'batch_id':str(uuid.uuid4())})
    batch=str(uuid.uuid4());payload={'confirmed':True,'preview_token':'a'*64,'batch_id':batch}
    assignment.bulk_apply(PROFILE,payload);first=applied.call_args.args[1]
    assignment.bulk_apply(PROFILE,payload);second=applied.call_args.args[1]
    assert first['request_id']==second['request_id'] and first['expected_revision']==3
    assert first['agent_id']==AGENT and applied.call_count==2
