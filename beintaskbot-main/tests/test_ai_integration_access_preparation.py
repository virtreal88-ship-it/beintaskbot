"""Unsupported management matrices stay unavailable; authorization precedes I/O."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from tenant_ai_agents_policy import agent
from tenant_linear_policy import TenantLinearError
from tenant_member_access import access_snapshot
from tenant_scoped_access import validate_access_scopes
from test_tenant_ai_agents_api_diagnostics import load_handler, run_without_event_loop


def test_unsupported_management_matrices_are_not_advertised_or_accepted(monkeypatch):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'true')
    profile = {'tenant_id': 'test', 'telegram_id': 11, 'role': 'owner', 'active': True}
    snapshot = access_snapshot(profile)
    assert snapshot['scope_catalog']['integrations'] == ['view','create','update','delete']
    validate_access_scopes({'integrations':{'view':'department','update':'all'}})
    assert snapshot['scope_catalog']['ai_agents'] == ['view','create','update','delete']
    validate_access_scopes({'ai_agents': {'view': 'self', 'create':'department', 'update':'all'}})
    validate_access_scopes({'ai_agents': {'delete': 'self'}})
    with pytest.raises(ValueError):
        validate_access_scopes({'ai_agents': {'manage': 'all'}})


@pytest.mark.parametrize('creating', [True, False])
def test_agent_ownership_is_not_a_browser_editable_field(creating):
    with pytest.raises(TenantLinearError):
        agent({'name': 'Synthetic', 'access_responsible_id': 999}, creating=creating)


def test_unauthorized_options_never_call_the_external_provider():
    forbidden = Mock(side_effect=TenantLinearError('Permission revoked', 403))
    remote = AsyncMock(return_value={'_embedded': {'pipelines': []}})
    refresh = Mock()
    handler, _ = load_handler(configuration_options=forbidden, refresh_configuration_catalog=refresh)
    profile = {'tenant_id': 'verified-tenant', 'telegram_id': 42}
    response = run_without_event_loop(handler(Mock(return_value=profile), 'https://crm.pro.az', Mock(),
        'options', request_provider=remote)(SimpleNamespace(method='GET', headers={}, query={})))
    assert response.status == 403
    remote.assert_not_called()
    refresh.assert_not_called()


def test_options_authorize_before_provider_and_refresh_revalidates_afterward():
    events = []
    def cached(profile):
        events.append('authorized_cached')
        return {'pipelines': ['cached']}
    async def remote(*args):
        events.append('remote_read')
        return {'_embedded': {'pipelines': [{'id': 10}]}}
    def refresh(profile, pipelines):
        events.append('authorized_refresh')
        return {'pipelines': pipelines}
    handler, _ = load_handler(configuration_options=cached, refresh_configuration_catalog=refresh)
    response = run_without_event_loop(handler(Mock(return_value={'tenant_id': 'verified', 'telegram_id': 42}),
        'https://crm.pro.az', Mock(), 'options', request_provider=remote)(
            SimpleNamespace(method='GET', headers={}, query={})))
    assert response.status == 200
    assert events == ['authorized_cached', 'remote_read', 'authorized_refresh']
    assert json.loads(response.text)['result']['pipelines'] == [{'id': 10}]


def test_provider_failure_uses_already_authorized_cache():
    cached = Mock(return_value={'pipelines': ['cached']})
    remote = AsyncMock(side_effect=RuntimeError('Synthetic provider unavailable'))
    handler, _ = load_handler(configuration_options=cached)
    response = run_without_event_loop(handler(Mock(return_value={'tenant_id': 'verified', 'telegram_id': 42}),
        'https://crm.pro.az', Mock(), 'options', request_provider=remote)(
            SimpleNamespace(method='GET', headers={}, query={})))
    assert response.status == 200
    assert json.loads(response.text)['result']['pipelines'] == ['cached']
    assert cached.call_count == 1


@pytest.mark.parametrize('scope,allowed', [(None, True), ('all', True), ('none', False), ('self', False), ('department', False)])
def test_aggregate_ai_diagnostics_do_not_bypass_narrow_dialog_scopes(monkeypatch, scope, allowed):
    import tenant_ai_management_access as access
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'true')
    settings = {'ai_agents_manage': True}
    if scope is not None:
        settings['access_scopes'] = {'dialogs': {'view': scope}}
    current = {'tenant_id': 'test', 'telegram_id': 42, 'active': True, 'role': 'admin',
               'workflow': {'policies': {'members': {'42': settings}}}}
    read = Mock(return_value=current)
    monkeypatch.setattr(access, 'read_access_profile', read)
    cursor = object()
    stale = {'tenant_id': 'test', 'telegram_id': 42, 'role': 'owner'}
    if allowed:
        access.require_unrestricted_dialog_read(cursor, stale)
    else:
        with pytest.raises(TenantLinearError) as error:
            access.require_unrestricted_dialog_read(cursor, stale)
        assert error.value.status == 403
    read.assert_called_once_with(cursor, 'test', 42, lock=True)


def test_aggregate_diagnostics_guard_is_inert_until_rollout(monkeypatch):
    import tenant_ai_management_access as access
    monkeypatch.delenv('TENANT_SCOPED_ACCESS_ENABLED', raising=False)
    read = Mock(side_effect=AssertionError('Legacy calls must retain their current protocol'))
    monkeypatch.setattr(access, 'read_access_profile', read)
    access.require_unrestricted_dialog_read(object(), {'tenant_id': 'test', 'telegram_id': 42})
    read.assert_not_called()
