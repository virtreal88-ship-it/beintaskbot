"""Upstream owner analyst keeps current-DB authority with scoped employees.

All records are synthetic in PGlite. No external provider or production DB.
"""
import json
import uuid
from unittest.mock import Mock

from cryptography.fernet import Fernet
import pytest

from test_tenant_support_inbox import database, db, A, PROFILE
from test_tenant_operator_workspace import operator_db
from test_dialog_source_scopes import scoped, grants
from tenant_linear_policy import TenantLinearError
import tenant_ai_business_analyst as analyst


@pytest.fixture
def analyst_db(scoped, monkeypatch):
    connection, person = scoped
    monkeypatch.setattr(analyst, '_connect', lambda: connection)
    provider = Mock(side_effect=AssertionError('External provider must not run'))
    monkeypatch.setattr(analyst, 'generate_with_tools', provider)
    return connection, person, provider


@pytest.mark.parametrize('view', ['none', 'self', 'department', 'all'])
@pytest.mark.parametrize('role', ['manager', 'admin'])
def test_scoped_employee_cannot_enter_owner_analyst_with_forged_profile(analyst_db, role, view):
    connection, person, provider = analyst_db
    grants(connection, 33, view, 'all')
    connection.execute("UPDATE saas_tenant_members SET role=%s WHERE tenant_id=%s AND telegram_id=33", (role, A))
    connection.execute("""UPDATE saas_tenant_workflow_policies
      SET value=jsonb_set(value,'{33,ai_agents_manage}','true')
      WHERE tenant_id=%s AND policy_key='members'""", (A,))
    connection.commit()
    with pytest.raises(TenantLinearError) as denied:
        analyst.run({**person, 'role': 'owner', 'permissions': ['dialogs', 'ai_agents_manage']},
                    prompt='Synthetic question', request_id=str(uuid.uuid4()))
    assert denied.value.status == 403
    provider.assert_not_called()
    assert not connection.execute('SELECT 1 FROM saas_chat_ai_runs WHERE tenant_id=%s', (A,))
    assert not connection.execute("SELECT 1 FROM saas_tenant_audit_events WHERE tenant_id=%s AND action='ai_business_analyst_query'", (A,))


def configure_owner(connection, monkeypatch):
    cipher = Fernet(Fernet.generate_key())
    monkeypatch.setattr(analyst, '_fernet', lambda: cipher)
    secret = cipher.encrypt(json.dumps({'tenant_id': A, 'provider': 'chat_ai', 'api_key': 'synthetic-never-dispatched'}).encode())
    metadata = {'enabled': True, 'consent': True, 'copilot_enabled': True,
                'copilot_model': 'synthetic-model', 'daily_limit': 5, 'copilot_daily_limit': 2}
    connection.execute("""INSERT INTO saas_tenant_integrations(tenant_id,provider,status,secrets,metadata)
      VALUES(%s,'chat_ai','connected',%s,%s::jsonb)""", (A, secret, json.dumps(metadata)))
    connection.commit()
    return analyst._load({**PROFILE, 'role': 'owner'})


@pytest.mark.parametrize('change', ['demote', 'deactivate', 'revoke_consent'])
def test_owner_admission_rechecks_current_authority_after_preflight(analyst_db, monkeypatch, change):
    connection, _, provider = analyst_db
    current = configure_owner(connection, monkeypatch)
    if change == 'demote':
        connection.execute("UPDATE saas_tenant_members SET role='admin' WHERE tenant_id=%s AND telegram_id=11", (A,))
    elif change == 'deactivate':
        connection.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s AND telegram_id=11', (A,))
    else:
        connection.execute("UPDATE saas_tenant_integrations SET metadata=jsonb_set(metadata,'{consent}','false') WHERE tenant_id=%s AND provider='chat_ai'", (A,))
    connection.commit()
    with pytest.raises(TenantLinearError) as denied:
        analyst._reserve({**PROFILE, 'role': 'owner'}, str(uuid.uuid4()), current['version'])
    assert denied.value.status == (409 if change == 'revoke_consent' else 403)
    provider.assert_not_called()
    assert not connection.execute('SELECT 1 FROM saas_chat_ai_runs WHERE tenant_id=%s', (A,))


def test_analyst_admission_counts_existing_connect_usage_in_shared_cap(analyst_db, monkeypatch):
    connection, _, provider = analyst_db
    current = configure_owner(connection, monkeypatch)
    connection.execute("""INSERT INTO saas_chat_ai_runs(tenant_id,request_id,requested_by,lead_id,input_hash,
      config_version,status,reserved_calls,provider,channel_id,conversation_id)
      VALUES(%s,%s,11,41,'synthetic','synthetic','completed',1,'connect','synthetic','synthetic')""",
      (A, str(uuid.uuid4())))
    connection.commit()
    request_id = str(uuid.uuid4())
    analyst._reserve(PROFILE, request_id, current['version'])
    with pytest.raises(TenantLinearError) as duplicate:
        analyst._reserve(PROFILE, request_id, current['version'])
    assert duplicate.value.status == 409
    with pytest.raises(TenantLinearError) as capped:
        analyst._reserve(PROFILE, str(uuid.uuid4()), current['version'])
    assert capped.value.status == 429
    rows = connection.execute('SELECT provider,reserved_calls FROM saas_chat_ai_runs WHERE tenant_id=%s ORDER BY provider', (A,))
    assert rows == [{'provider': 'business_analyst', 'reserved_calls': 1}, {'provider': 'connect', 'reserved_calls': 1}]
    provider.assert_not_called()
