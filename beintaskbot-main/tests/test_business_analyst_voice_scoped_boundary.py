"""Synthetic scoped-member and live-revocation checks for upstream analyst voice.

Only isolated PGlite is used. No provider is opened and no key is decrypted.
"""
import asyncio
import json
from unittest.mock import Mock

import pytest

from test_tenant_support_inbox import database, db, A, PROFILE
from test_tenant_operator_workspace import operator_db
from test_dialog_source_scopes import scoped, grants
from tenant_linear_policy import TenantLinearError
import tenant_ai_business_analytics_voice as voice
import tenant_operator_voice_analytics as tools


@pytest.fixture
def voice_db(scoped, monkeypatch):
    connection, employee = scoped
    monkeypatch.setattr(voice, '_connect', lambda: connection)
    monkeypatch.setattr('tenant_crm_store._connect', lambda: connection)
    monkeypatch.setenv('COPILOT_VOICE_PILOT_TENANT_ID', A)
    monkeypatch.setenv('COPILOT_VOICE_PILOT_ACTOR_ID', '11')
    monkeypatch.setenv('COPILOT_VOICE_TTS_PROVIDER', 'openai')
    compose = Mock(side_effect=AssertionError('No external provider may be composed'))
    monkeypatch.setattr(voice, '_compose_session', compose)
    metadata = {'enabled': True, 'consent': True, 'audio_enabled': True,
                'copilot_enabled': True}
    connection.execute("""INSERT INTO saas_tenant_integrations
      (tenant_id,provider,status,secrets,metadata)
      VALUES(%s,'chat_ai','connected',%s,%s::jsonb)""",
      (A, b'synthetic-not-a-key', json.dumps(metadata)))
    connection.commit()
    yield connection, employee
    compose.assert_not_called()


@pytest.mark.parametrize('view', ['none', 'self', 'department', 'all'])
@pytest.mark.parametrize('role', ['manager', 'admin'])
def test_scoped_employee_cannot_enter_owner_voice_with_forged_profile(voice_db, monkeypatch, role, view):
    connection, employee = voice_db
    grants(connection, 33, view, 'all')
    connection.execute("UPDATE saas_tenant_members SET role=%s WHERE tenant_id=%s AND telegram_id=33", (role, A))
    connection.execute("""UPDATE saas_tenant_workflow_policies
      SET value=jsonb_set(value,'{33,ai_agents_manage}','true')
      WHERE tenant_id=%s AND policy_key='members'""", (A,))
    connection.commit()
    # Even the configured pilot and a forged owner profile cannot bypass the
    # current member role or promote employee AI/source grants into ownership.
    monkeypatch.setenv('COPILOT_VOICE_PILOT_ACTOR_ID', '33')
    profile = {**employee, 'role': 'owner', 'permissions': ['dialogs', 'ai_agents_manage']}
    with pytest.raises(TenantLinearError) as denied:
        asyncio.run(voice.AnalyticsVoiceAuthority.create(profile, lambda: profile))
    assert denied.value.status == 403
    assert not connection.execute("SELECT 1 FROM saas_tenant_audit_events WHERE tenant_id=%s AND action='ai_business_analytics_tool_read'", (A,))


def test_owner_voice_uses_stateless_context_and_current_read_only_registry(voice_db):
    connection, _ = voice_db

    async def run():
        authority = await voice.AnalyticsVoiceAuthority.create(PROFILE, lambda: PROFILE)
        context = voice.AnalyticsVoiceContext(authority)
        result = await context.read_authorized(authority.session_scope)
        assert result['lines'] == []
        assert result['extra_context'] == {}
        assert authority.session_scope[2:] == ('00000000-0000-0000-0000-000000000000', 'business-analyst-voice')
        assert await context.revalidate(authority.session_scope) is True
        result = json.loads(await tools.AnalyticsToolExecutor(authority)('list_analytics_sources', {}))
        assert result['read_only'] is True
        assert result['result']['read_only'] is True
        assert result['result']['assistant_connections']['selected_dialog_copilot'] is False

    asyncio.run(run())
    assert len(connection.execute("SELECT 1 FROM saas_tenant_audit_events WHERE tenant_id=%s AND action='ai_business_analytics_tool_read'", (A,))) == 1
    assert not connection.execute('SELECT 1 FROM saas_chat_ai_runs WHERE tenant_id=%s', (A,))


@pytest.mark.parametrize('change', [
    'demote', 'deactivate', 'consent', 'audio_enabled', 'copilot_enabled',
    'emergency_stop', 'pilot', 'live_identity', 'config_version',
])
def test_owner_voice_rechecks_current_authority_before_read_tool(voice_db, monkeypatch, change):
    connection, _ = voice_db
    current_identity = dict(PROFILE)
    authority = asyncio.run(voice.AnalyticsVoiceAuthority.create(PROFILE, lambda: current_identity))
    if change == 'demote':
        connection.execute("UPDATE saas_tenant_members SET role='admin' WHERE tenant_id=%s AND telegram_id=11", (A,))
    elif change == 'deactivate':
        connection.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s AND telegram_id=11', (A,))
    elif change in {'consent', 'audio_enabled', 'copilot_enabled'}:
        connection.execute("UPDATE saas_tenant_integrations SET metadata=jsonb_set(metadata,%s::text[],'false') WHERE tenant_id=%s AND provider='chat_ai'", ([change], A))
    elif change == 'emergency_stop':
        connection.execute("""INSERT INTO saas_ai_agent_settings(tenant_id,emergency_stop)
          VALUES(%s,TRUE) ON CONFLICT(tenant_id) DO UPDATE SET emergency_stop=TRUE""", (A,))
    elif change == 'pilot':
        monkeypatch.setenv('COPILOT_VOICE_PILOT_ACTOR_ID', '33')
    elif change == 'live_identity':
        current_identity['telegram_id'] = 33
    else:
        connection.execute("UPDATE saas_tenant_integrations SET updated_at=updated_at+interval '1 second' WHERE tenant_id=%s AND provider='chat_ai'", (A,))
    connection.commit()
    execute = Mock(side_effect=AssertionError('Revoked authority must not read source records'))
    monkeypatch.setattr(tools, 'execute', execute)
    with pytest.raises(TenantLinearError) as denied:
        asyncio.run(tools.AnalyticsToolExecutor(authority)('list_analytics_sources', {}))
    assert denied.value.status in {403, 409}
    execute.assert_not_called()


def test_voice_tool_result_is_discarded_after_owner_revocation(voice_db, monkeypatch):
    connection, _ = voice_db
    authority = asyncio.run(voice.AnalyticsVoiceAuthority.create(PROFILE, lambda: PROFILE))

    def revoke_after_read(*_args):
        connection.execute("UPDATE saas_tenant_members SET role='admin' WHERE tenant_id=%s AND telegram_id=11", (A,))
        connection.commit()
        return {'read_only': True, 'synthetic_evidence': 'must not leave the executor'}

    execute = Mock(side_effect=revoke_after_read)
    monkeypatch.setattr(tools, 'execute', execute)
    with pytest.raises(TenantLinearError) as denied:
        asyncio.run(tools.AnalyticsToolExecutor(authority)('list_analytics_sources', {}))
    assert denied.value.status == 403
    execute.assert_called_once()
