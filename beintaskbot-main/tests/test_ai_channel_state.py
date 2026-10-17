"""Catalogue/state contract against isolated PostgreSQL; external calls mocked."""
import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import Mock, patch
import pytest
from aiohttp.test_utils import make_mocked_request
from test_tenant_support_inbox import database, db, A, B, CA, CB, AGENT, PROFILE
from test_integrated_connect_release import flow
from test_connect_channel_isolation import channels, AGENT2
import tenant_ai_channel_catalog as catalog
import tenant_ai_state as state
import tenant_ai_state_api as api
import tenant_ai_reliability as reliability
import tenant_ai_agents_store as agents
import tenant_ai_agent_connect as connect
from tenant_ai_agent_queue import _scope_matches
from tenant_linear_policy import TenantLinearError


@pytest.fixture
def system(channels, monkeypatch):
    f = channels
    for module in (catalog, state, reliability): monkeypatch.setattr(module, '_connect', lambda: f['db'])
    monkeypatch.setattr(state, 'platform_key_ready', lambda *a: True)
    monkeypatch.setattr(state, 'platform_model_allowed', lambda *a: True)
    f['db'].execute("UPDATE saas_connect_channels SET status='connected'")
    f['db'].commit()
    reliability.heartbeat()
    return f


def test_catalog_preserves_identity_and_real_capabilities(system):
    result = catalog.catalog(PROFILE)
    assert result['schema_version'] == 1 and result['multiple_scopes_supported']
    assert {c['id'] for c in result['channels']} == {'connect:' + CA, 'connect:' + CB}
    for item in result['channels']:
        assert item['type'] == 'whatsapp' and item['provider'] == 'connect'
        assert item['scope'] == {'provider': 'connect', 'channel_id': item['channel_id']}
        assert item['ai']['available']
        assert item['connection']['source'] == 'cached'
        assert item['capabilities'] == dict(text_replies=True, incoming_voice=True, voice_replies=False,
                                            groups=True, drafts=True, crm_actions=False)
        assert 'group_opt_in' in item['requirements']
    assert all(c['type'] not in {'instagram', 'telegram'} for c in result['channels'])


def test_disconnected_unknown_and_onboarding_are_honest(system):
    d = system['db']
    d.execute("UPDATE saas_tenants SET status='onboarding' WHERE id=%s::uuid", (A,))
    d.execute("UPDATE saas_connect_channels SET status='not-a-real-state' WHERE connect_channel_id=%s::uuid", (CA,))
    d.execute("UPDATE saas_connect_channels SET active=FALSE WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid", (A, CB)); d.commit()
    by_id = {c['channel_id']: c for c in catalog.catalog(PROFILE)['channels']}
    assert by_id[CA]['connection']['state'] == 'unknown'
    assert not by_id[CA]['ai']['available'] and 'tenant_inactive' not in by_id[CA]['ai']['reason_codes']
    assert by_id[CB]['connection']['state'] == 'disconnected' and not by_id[CB]['ai']['available']


def test_public_summary_and_technical_permission_are_server_side(system):
    d = system['db']
    d.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,permissions) VALUES(%s::uuid,33,'manager','[\"dialogs\"]')", (A,)); d.commit()
    operator = {'tenant_id': A, 'telegram_id': 33, 'role': 'owner'}  # Forged role is ignored.
    result = state.snapshot(operator)
    assert not result['can_view_technical'] and 'technical' not in result
    assert 'tenant_id' not in json.dumps(result)
    with pytest.raises(TenantLinearError) as error: state.snapshot(operator, technical=True)
    assert error.value.status == 403
    with pytest.raises(TenantLinearError): catalog.catalog(operator)
    d.execute("UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s::uuid AND telegram_id=33", (A,)); d.commit()
    with pytest.raises(TenantLinearError): state.snapshot(operator)


def test_admin_requires_existing_manage_grant(system):
    d = system['db']
    d.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,permissions) VALUES(%s::uuid,33,'admin','[\"dialogs\"]')", (A,)); d.commit()
    profile = {'tenant_id': A, 'telegram_id': 33}
    with pytest.raises(TenantLinearError): state.snapshot(profile, technical=True)
    d.execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value) VALUES(%s::uuid,'members','{\"33\":{\"ai_agents_manage\":true}}') ON CONFLICT(tenant_id,policy_key) DO UPDATE SET value=EXCLUDED.value", (A,)); d.commit()
    assert state.snapshot(profile, technical=True)['technical']['tenant_id'] == A


def test_tenant_isolation_and_no_secrets_or_message_content(system, monkeypatch):
    f = system; f['incoming'](CA, 'PRIVATE CUSTOMER MESSAGE'); f['process']()
    monkeypatch.setenv('RAILWAY_DEPLOYMENT_ID', 'sk-secret-not-a-deployment-id')
    result = state.snapshot(PROFILE, technical=True)
    encoded = json.dumps(result)
    for secret in ('PRIVATE CUSTOMER MESSAGE', 'Cavab hazirdir', 'mock-platform-key', 'mock-connect-key', 'sk-secret', 'instructions', 'client'):
        assert secret not in encoded
    assert result['technical']['deployment_id'] is None
    other = state.snapshot({'tenant_id': B, 'telegram_id': 22})
    assert other['events'] == [] and other['agents'] == []
    with pytest.raises(TenantLinearError): state.snapshot({'tenant_id': B, 'telegram_id': 11})
    assert result['events'][0]['dialog'] == {'provider': 'connect', 'channel_id': CA, 'lead_id': 41}


def test_human_is_not_system_failure_and_channel_events_do_not_mix(system):
    f = system
    f['incoming'](CA); f['incoming'](CB)
    connect.mark_operator_reply(A, 41, CA, 'client')
    f['process']()
    result = state.snapshot(PROFILE)
    by_id = {a['id']: a for a in result['agents']}
    assert result['summary']['ai_errors_24h'] == 0
    assert result['summary']['state'] != 'attention'
    assert all(e['channel']['id'] == 'connect:' + CA for e in by_id[AGENT]['recent_events'])
    assert all(e['channel']['id'] == 'connect:' + CB for e in by_id[AGENT2]['recent_events'])
    assert any(e['category'] == 'human' for e in by_id[AGENT]['recent_events'])
    assert any(e['status'] == 'sent' for e in by_id[AGENT2]['recent_events'])
    assert by_id[AGENT]['channels'][0]['dialogue_states']['human'] == 1


def test_manual_drafts_and_pause_resume_remain_independent(system):
    f = system; d = f['db']
    d.execute("UPDATE saas_ai_agents SET mode='manual'"); d.commit()
    f['incoming'](CA); f['incoming'](CB); f['process'](); f['process']()
    before = d.execute('SELECT * FROM saas_ai_agent_drafts ORDER BY id'); d.commit()
    agents.set_conversation_state(PROFILE, 'client', 'paused', provider='connect', channel_id=CA)
    result = state.snapshot(PROFILE)
    assert all(a['state'] == 'manual' for a in result['agents'])
    agents.set_conversation_state(PROFILE, 'client', 'active', provider='connect', channel_id=CA)
    assert f['process']() is None
    state.snapshot(PROFILE, technical=True); catalog.catalog(PROFILE)
    after = d.execute('SELECT * FROM saas_ai_agent_drafts ORDER BY id'); d.commit()
    assert before == after and len(before) == 2
    f['send'].assert_not_called()


def test_unknown_error_cannot_echo_provider_data(system):
    f = system; f['incoming'](CA)
    f['send'].side_effect = RuntimeError('password=secret customer phone')
    f['process']()
    result = state.snapshot(PROFILE)
    assert result['summary']['state'] == 'attention'
    assert result['summary']['ai_errors_24h'] > 0
    assert 'password' not in json.dumps(result)


def test_worker_presence_does_not_prove_agent_readiness(system):
    d = system['db']
    d.execute("UPDATE saas_ai_agent_settings SET enabled=FALSE"); d.commit()
    result = state.snapshot(PROFILE)
    assert result['summary']['service'] == 'available'
    assert result['summary']['state'] == 'not_ready'
    assert all(a['state'] == 'blocked' for a in result['agents'])


def test_night_shift_uses_runtime_schedule_and_timezone(system):
    d = system['db']
    d.execute("UPDATE saas_ai_agents SET schedule=%s::jsonb", (json.dumps({'timezone':'Asia/Baku','days':[0],'start':'22:00','end':'06:00'}),)); d.commit()
    at = lambda s: datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    assert all(a['state'] == 'ready' for a in state.snapshot(PROFILE, now=at('2026-10-19T23:00:00'))['agents'])
    result = state.snapshot(PROFILE, now=at('2026-10-20T03:00:00'))
    assert all(a['state'] == 'outside_schedule' for a in result['agents'])
    assert all(a['next_shift_at'] for a in result['agents'])


def test_quarantine_is_retained_read_only(system):
    d = system['db']; system['incoming'](CA)
    d.execute("UPDATE saas_ai_conversation_state SET channel_id='' WHERE tenant_id=%s::uuid AND channel_id=%s", (A, CA)); d.commit()
    before = d.execute("SELECT * FROM saas_ai_conversation_state"); d.commit()
    result = state.snapshot(PROFILE)
    assert result['summary']['quarantined_legacy_dialogues'] == 1
    assert any(e['reason_code'] == 'legacy_channel_ambiguous' for e in result['events'])
    assert before == d.execute("SELECT * FROM saas_ai_conversation_state")


def test_kommo_scope_cannot_match_connect_even_with_same_ids():
    assert not _scope_matches([{'provider':'connect','pipeline_id':'1','stage_id':'2','channel_id':'same'}], '1', '2', 'same')
    assert _scope_matches([{'provider':'kommo','pipeline_id':'1','stage_id':'2','channel_id':'same'}], '1', '2', 'same')
    assert _scope_matches([{'pipeline_id':'1','stage_id':'2','channel_id':''}], '1', '2', 'any')


def seed_kommo(d):
    d.execute("INSERT INTO saas_tenant_integrations(tenant_id,provider,status) VALUES(%s::uuid,'kommo','connected')", (A,))
    d.execute("INSERT INTO saas_crm_deals(tenant_id,kommo_lead_id,pipeline_id,status_id) VALUES(%s::uuid,999,10,20)", (A,))
    d.execute("INSERT INTO saas_tenant_webhook_events(tenant_id,fingerprint,provider,external_id,event_type,payload,state) VALUES(%s::uuid,'kommo-event','kommo','kommo-message','incoming_message',%s::jsonb,'queued')", (A,json.dumps({'channel':CA,'chat_id':'crm-chat','lead_id':999})))
    d.execute("INSERT INTO saas_ai_agent_jobs(tenant_id,agent_id,provider,conversation_id,chat_id,channel_id,lead_id,event_fingerprint,talk_id,due_at) VALUES(%s::uuid,%s::uuid,'kommo','crm-chat','crm-chat',%s,999,'kommo-event','42',now())", (A,AGENT,CA)); d.commit()


def test_kommo_catalog_is_provider_namespaced_and_options_do_not_include_connect(system):
    d = system['db']
    assert agents.configuration_options(PROFILE)['channels'] == []
    seed_kommo(d)
    result = catalog.catalog(PROFILE)['channels']
    assert len(result) == 3 and len({c['id'] for c in result}) == 3
    crm = next(c for c in result if c['provider'] == 'kommo')
    assert crm['id'] == 'kommo:' + CA and crm['type'] == 'unknown'
    assert crm['ai']['available'] and crm['connection']['state'] == 'unknown'
    assert not crm['capabilities']['incoming_voice'] and not crm['capabilities']['groups']
    assert crm['capabilities']['crm_actions']
    assert 'pipeline_id' in crm['requirements'] and 'stage_id' in crm['requirements']
    assert agents.configuration_options(PROFILE)['channels'] == [{'id': CA, 'name': CA}]


def test_kommo_event_link_requires_existing_deal_permissions(system):
    d = system['db']; seed_kommo(d)
    owner = state.snapshot(PROFILE)
    event = next(e for e in owner['events'] if e['channel']['provider'] == 'kommo')
    assert event['dialog'] == {'provider':'kommo','channel_id':CA,'lead_id':999}
    d.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,permissions) VALUES(%s::uuid,33,'manager','[\"dialogs\"]')", (A,)); d.commit()
    assert all(e['channel']['provider'] != 'kommo' for e in state.snapshot({'tenant_id':A,'telegram_id':33})['events'])


def test_multiple_channels_one_agent_and_catalog_not_disabled_by_manual_company_flag(system):
    d = system['db']
    d.execute("UPDATE saas_ai_agents SET scopes=%s::jsonb,mode='manual' WHERE id=%s::uuid", (json.dumps([{'provider':'connect','channel_id':CA},{'provider':'connect','channel_id':CB}]), AGENT))
    d.execute("UPDATE saas_ai_agents SET enabled=FALSE WHERE id=%s::uuid", (AGENT2,)); d.commit()
    result = state.snapshot(PROFILE)
    agent = next(a for a in result['agents'] if a['id'] == AGENT)
    assert {c['channel_id'] for c in agent['channels']} == {CA, CB}
    assert agent['state'] == 'manual'
    d.execute('UPDATE saas_ai_agent_settings SET enabled=FALSE'); d.commit()
    assert all(c['ai']['available'] for c in catalog.catalog(PROFILE)['channels'])


def test_test_mode_without_binding_is_not_reported_as_ready(system):
    d = system['db']; d.execute("UPDATE saas_ai_agent_settings SET operating_mode='test'"); d.commit()
    result = state.snapshot(PROFILE)
    assert result['summary']['eligible_agents'] == 0
    assert all('test_binding_mismatch' in c['reason_codes'] for a in result['agents'] for c in a['channels'])


def test_diagnostic_reads_do_not_enqueue_or_send_or_change_settings(system):
    d = system['db']; system['incoming'](CA)
    tables = ['saas_ai_agent_settings','saas_ai_agents','saas_ai_agent_jobs','saas_ai_conversation_state','saas_ai_agent_runs','saas_support_tickets']
    def contents():
        value = {table:d.execute('SELECT * FROM '+table) for table in tables}; d.commit(); return value
    before = contents()
    state.snapshot(PROFILE); state.snapshot(PROFILE, technical=True); catalog.catalog(PROFILE)
    assert contents() == before
    system['send'].assert_not_called(); system['generate'].assert_not_called()


def test_other_agents_run_and_draft_cannot_be_presented_as_current_agents_reply(system):
    import uuid
    f = system; mid, _ = f['incoming'](CA); d = f['db']
    fingerprint = d.execute("SELECT fingerprint FROM saas_tenant_webhook_events WHERE external_id=%s", (mid,))[0]['fingerprint']
    run = str(uuid.uuid4())
    d.execute("INSERT INTO saas_ai_agent_runs(tenant_id,id,agent_id,event_fingerprint,revision,model,state,result) VALUES(%s::uuid,%s::uuid,%s::uuid,%s,1,'test-model','completed','{\"send_state\":\"accepted\"}')", (A,run,AGENT2,fingerprint))
    d.execute("INSERT INTO saas_ai_agent_drafts(tenant_id,id,agent_id,run_id,lead_id,talk_id,chat_id,source_message_id,source_fingerprint,draft) VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,41,'','client',%s,%s,'PRIVATE OTHER AGENT DRAFT')", (A,str(uuid.uuid4()),AGENT2,run,mid,fingerprint)); d.commit()
    item = next(m for m in reliability.snapshot(PROFILE)['messages'] if m['message_id'] == mid)
    assert item['agent_id'] == AGENT and item['status'] == 'queued'
    assert not item['checkpoints']['sent'] and not item['checkpoints']['generated']


@pytest.mark.parametrize('resource', ['channels','state','technical'])
def test_api_identity_guard_precedes_store(resource):
    request = make_mocked_request('GET', '/api/platform/ai/state?expected_tenant_id=wrong&expected_user_id=11')
    with patch.object(api, 'snapshot') as read, patch.object(api, 'catalog') as channels_read:
        response = asyncio.run(api.handler(lambda _: PROFILE, Mock(), resource)(request))
    assert response.status == 409 and response.headers['Cache-Control'] == 'no-store'
    read.assert_not_called(); channels_read.assert_not_called()


def test_api_clamps_limit_and_does_not_promote_query_technical():
    request = make_mocked_request('GET', f'/api/platform/ai/state?expected_tenant_id={A}&expected_user_id=11&limit=999&technical=true')
    with patch.object(api, 'snapshot', return_value={'schema_version':1}) as read:
        response = asyncio.run(api.handler(lambda _: PROFILE, Mock())(request))
    assert response.status == 200
    read.assert_called_once_with(PROFILE, 100, technical=False)
