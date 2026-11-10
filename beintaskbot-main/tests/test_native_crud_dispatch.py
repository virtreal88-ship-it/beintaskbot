"""The permission checked must match the native operation actually dispatched."""
import asyncio
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
import tenant_crm_native_deal_api as deals
import tenant_crm_native_company_api as companies
import tenant_crm_native_api as contacts
import tenant_crm_native_task_api as tasks

ORIGIN = 'https://crm.test'
IDENT = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'


def request(body, ident=None):
    return SimpleNamespace(method='POST', match_info={'id': ident} if ident else {}, query={},
        content_type='application/json', headers={'Origin': ORIGIN}, json=AsyncMock(return_value=body))


def invoke(module, grant, body, ident=None):
    profile = {'tenant_id': 'company-a', 'telegram_id': 20, 'role': 'manager', 'active': True,
               'permissions': [grant], 'modules': {}}
    return asyncio.run(module.handler(lambda _: profile, ORIGIN, Mock())(request(body, ident)))


@pytest.mark.parametrize('action,grant', [('archive', 'delete'), ('update', 'update'),
    ('link_contact', 'assign'), ('link_deal', 'assign'),
    ('unlink_contact', 'delete'), ('unlink_deal', 'delete')])
def test_company_item_actions_never_create_at_collection(action, grant):
    with patch.object(companies, 'create_company') as create:
        response = invoke(companies, 'customers:' + grant, {'action': action, 'name': 'Unexpected'})
    assert response.status == 400
    create.assert_not_called()


@pytest.mark.parametrize('grant', ['delete', 'create', 'update'])
def test_deal_archive_without_id_never_creates(grant):
    with patch.object(deals, 'create_deal') as create:
        response = invoke(deals, 'deals:' + grant, {'action': 'archive', 'title': 'Unexpected'})
    assert response.status == 400
    create.assert_not_called()


@pytest.mark.parametrize('module,prefix,create_name', [
    (deals, 'deals', 'create_deal'), (companies, 'customers', 'create_company'),
    (contacts, 'customers', 'create_contact'), (tasks, 'tasks', 'create_task')])
@pytest.mark.parametrize('grant', ['view', 'update', 'delete'])
def test_collection_create_requires_create_grant(module, prefix, create_name, grant):
    with patch.object(module, create_name) as create:
        response = invoke(module, prefix + ':' + grant, {'name': 'Unexpected', 'title': 'Unexpected'})
    assert response.status == 403
    create.assert_not_called()


@pytest.mark.parametrize('module,prefix,create_name', [
    (contacts, 'customers', 'create_contact'), (tasks, 'tasks', 'create_task')])
def test_contact_and_task_archive_collection_do_not_fall_through(module, prefix, create_name):
    for grant in ('create', 'delete'):
        with patch.object(module, create_name) as create:
            response = invoke(module, prefix + ':' + grant, {'action': 'archive', 'name': 'Unexpected'})
        assert response.status in (400, 403)
        create.assert_not_called()


@pytest.mark.parametrize('module,prefix,archive_name', [
    (deals, 'deals', 'archive_deal'), (companies, 'customers', 'archive_company')])
def test_existing_item_archive_keeps_delete_authorization(module, prefix, archive_name):
    with patch.object(module, archive_name, return_value=True) as archive:
        response = invoke(module, prefix + ':delete', {'action': 'archive'}, IDENT)
    assert response.status == 200
    archive.assert_called_once_with('company-a', IDENT)
    with patch.object(module, archive_name) as archive:
        response = invoke(module, prefix + ':update', {'action': 'archive'}, IDENT)
    assert response.status == 403
    archive.assert_not_called()


@pytest.mark.parametrize('module,prefix,create_name', [
    (deals, 'deals', 'create_deal'), (companies, 'customers', 'create_company')])
def test_valid_native_create_still_dispatches(module, prefix, create_name):
    # Keep this test isolated from the optional sync outbox too.
    with ExitStack() as stack:
        create = stack.enter_context(patch.object(module, create_name, return_value={'id': IDENT}))
        stack.enter_context(patch('tenant_crm_sync_outbox.enqueue_change'))
        response = invoke(module, prefix + ':create', {'title': 'Deal', 'name': 'Company'})
    assert response.status == 200
    assert create.call_count == 1
