import logging
from unittest.mock import Mock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import pytest

import tenant_personal_dialog_reminders_api as api
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
