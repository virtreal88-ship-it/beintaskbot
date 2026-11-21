import logging
from unittest.mock import Mock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import pytest

import tenant_personal_dialog_reminders as reminders
import tenant_personal_dialog_reminders_api as api
from tenant_linear_policy import TenantLinearError
from tenant_policy import TenantPolicy


def test_profile_capability_absent_when_disabled_or_no_dialog_access(monkeypatch):
    profile = {'tenant_id':'tenant-a','telegram_id':11,'role':'worker','active':True,
               'permissions':['dialogs']}
    monkeypatch.delenv('TENANT_PERSONAL_DIALOG_REMINDERS_ENABLED', raising=False)
    assert 'personal_dialog_reminders' not in TenantPolicy(profile).public_capabilities()
    monkeypatch.setenv('TENANT_PERSONAL_DIALOG_REMINDERS_ENABLED', '1')
    assert TenantPolicy(profile).public_capabilities()['personal_dialog_reminders'] is True
    assert 'personal_dialog_reminders' not in TenantPolicy({**profile,'permissions':[]}).public_capabilities()
    assert 'personal_dialog_reminders' not in TenantPolicy({**profile,'active':False}).public_capabilities()


@pytest.mark.asyncio
async def test_identity_origin_and_disabled_gate_precede_store(monkeypatch):
    store = Mock(return_value={'reminders':[]})
    monkeypatch.setattr(api, 'execute', store)
    profile = {'tenant_id':'tenant-a','telegram_id':11}
    app = web.Application()
    handle = api.handler(lambda request: profile, 'https://crm.test', logging.getLogger(__name__))
    app.router.add_get('/reminders', handle)
    app.router.add_post('/reminders', handle)
    async with TestClient(TestServer(app)) as client:
        monkeypatch.setenv('TENANT_PERSONAL_DIALOG_REMINDERS_ENABLED', '0')
        good = {'expected_tenant_id':'tenant-a','expected_user_id':'11'}
        assert (await client.get('/reminders', params=good)).status == 404
        store.assert_not_called()
        monkeypatch.setenv('TENANT_PERSONAL_DIALOG_REMINDERS_ENABLED', '1')
        stale = await client.get('/reminders', params={**good,'expected_user_id':'12'})
        assert stale.status == 409
        assert (await stale.json())['error_code'] == 'reminder_session_unavailable'
        assert (await client.post('/reminders', json={**good,'action':'complete'},
                                  headers={'Origin':'https://somewhere.test'})).status == 403
        assert (await client.post('/reminders', json={**good,'expected_tenant_id':'tenant-b'},
                                  headers={'Origin':'https://crm.test'})).status == 409
        async def oversized():
            yield b'{' + b'x' * 4096
        assert (await client.post('/reminders', data=oversized(), headers={
            'Origin':'https://crm.test','Content-Type':'application/json'})).status == 413
        store.assert_not_called()
        result = await client.get('/reminders', params=good)
        assert result.status == 200 and result.headers['Cache-Control'] == 'no-store'
        assert (await result.json())['result'] == {'reminders':[]}
        result = await client.post('/reminders', json={**good,'action':'complete','id':'fake'},
                                   headers={'Origin':'https://crm.test'})
        assert result.status == 200
        store.assert_called_with(profile, {'action':'complete','id':'fake'})


PILOT = 'a0000000-0000-4000-8000-000000000001'
OTHER = 'b0000000-0000-4000-8000-000000000002'


@pytest.mark.asyncio
async def test_exact_beinsystems_pilot_gate_in_profile_api_and_store(monkeypatch):
    monkeypatch.delenv(reminders.LEGACY_ENABLE, raising=False)
    monkeypatch.setenv(reminders.WORKSPACE_ENABLE, '1')
    monkeypatch.setenv('BEINSYSTEMS_OPERATOR_TENANT_ID', PILOT)
    pilot = {'tenant_id':PILOT,'telegram_id':11,'role':'worker','active':True,'permissions':['dialogs']}
    other = {**pilot,'tenant_id':OTHER}
    assert reminders.capability(TenantPolicy(pilot))
    assert not reminders.capability(TenantPolicy({**pilot,'permissions':[]}))
    assert not reminders.capability(TenantPolicy(other))
    import tenant_platform
    connect = Mock(side_effect=AssertionError('another workspace must not reach DB'))
    monkeypatch.setattr(tenant_platform, '_connect', connect)
    with pytest.raises(TenantLinearError) as rejected:
        reminders.execute(other)
    assert rejected.value.status == 404
    connect.assert_not_called()

    active_profile = pilot
    app = web.Application()
    handler = api.handler(lambda _request: active_profile, 'https://crm.test', logging.getLogger(__name__))
    app.router.add_get('/reminders', handler)
    app.router.add_post('/reminders', handler)
    backend = Mock(return_value={'reminders':[]})
    monkeypatch.setattr(api, 'execute', backend)
    expected = {'expected_tenant_id':PILOT,'expected_user_id':'11'}
    async with TestClient(TestServer(app)) as client:
        active_profile = other
        assert (await client.get('/reminders', params=expected)).status == 404
        assert (await client.post('/reminders', json={**expected,'action':'create'},
                                  headers={'Origin':'https://crm.test'})).status == 404
        backend.assert_not_called()
        active_profile = pilot
        assert (await client.get('/reminders', params=expected)).status == 200
        backend.assert_called_once_with(pilot)
        monkeypatch.setenv('BEINSYSTEMS_OPERATOR_TENANT_ID', 'not-an-id')
        assert not reminders.capability(TenantPolicy(pilot))
        assert (await client.get('/reminders', params=expected)).status == 404
        # Existing explicitly global installs retain their former behavior.
        monkeypatch.setenv(reminders.LEGACY_ENABLE, '1')
        active_profile = other
        assert reminders.capability(TenantPolicy(other))
        assert (await client.get('/reminders', params={**expected,'expected_tenant_id':OTHER})).status == 200
        backend.assert_called_with(other)
