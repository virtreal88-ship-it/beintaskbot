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


def relation_profile(grants, overrides=None):
    return {'tenant_id': 'company-a', 'telegram_id': 20, 'role': 'manager', 'active': True,
            'permissions': grants, 'modules': {'customers': True, 'deals': True},
            'workflow': {'policies': {'members': {'20': {'native_crm_actions': overrides or {}}}}}}


def invoke_relation(profile, action):
    return asyncio.run(companies.handler(lambda _: profile, ORIGIN, Mock())(
        request({'action': action, 'entity_id': IDENT}, IDENT)))


@pytest.mark.parametrize('action', ['link_contact', 'link_deal', 'unlink_contact', 'unlink_deal'])
@pytest.mark.parametrize('grants', [['customers', 'deals'], ['customers:assign', 'customers:delete', 'deals:manage']])
def test_explicit_readonly_vetoes_legacy_relation_mutations(action, grants):
    profile = relation_profile(grants, {module: {'view': True, 'update': False, 'delete': False}
                                       for module in ('customers', 'deals')})
    with patch.object(companies, 'link_entity') as link, patch.object(companies, 'unlink_entity') as unlink:
        assert invoke_relation(profile, action).status == 403
    link.assert_not_called()
    unlink.assert_not_called()
    assert not companies.relation_actions(companies.TenantPolicy(profile))[action]


@pytest.mark.parametrize('action', ['link_contact', 'link_deal'])
def test_explicit_update_allows_relations_without_legacy_assign_or_delete(action):
    profile = relation_profile([], {module: {'update': True, 'delete': False}
                                    for module in ('customers', 'deals')})
    operation = 'unlink_entity' if action.startswith('unlink') else 'link_entity'
    with patch.object(companies, operation, return_value=True) as mutate:
        assert invoke_relation(profile, action).status == 200
    mutate.assert_called_once_with('company-a', IDENT, action.split('_')[1], IDENT)


@pytest.mark.parametrize('action', ['link_contact', 'link_deal'])
def test_assign_only_no_longer_authorizes_native_relationship_editing(action):
    with patch.object(companies, 'link_entity') as mutate:
        assert invoke_relation(relation_profile(['customers:assign']), action).status == 403
    mutate.assert_not_called()


@pytest.mark.parametrize('action', ['unlink_contact', 'unlink_deal'])
def test_unlink_retains_delete_contract(action):
    with patch.object(companies, 'unlink_entity', return_value=True) as mutate:
        assert invoke_relation(relation_profile(['customers:delete']), action).status == 200
    assert mutate.call_count == 1
    with patch.object(companies, 'unlink_entity') as mutate:
        assert invoke_relation(relation_profile([], {'customers': {'update': True, 'delete': False},
                                                  'deals': {'update': True}}), action).status == 403
    mutate.assert_not_called()


@pytest.mark.parametrize('action', ['link_deal'])
@pytest.mark.parametrize('overrides', [
    {'customers': {'update': True}, 'deals': {'update': False}},
    {'customers': {'update': False}, 'deals': {'update': True}},
    {'customers': {'update': True}},
])
def test_native_deal_relations_require_update_on_both_entities(action, overrides):
    profile = relation_profile(['customers:assign', 'customers:delete'], overrides)
    with patch.object(companies, 'link_entity') as link, patch.object(companies, 'unlink_entity') as unlink:
        assert invoke_relation(profile, action).status == 403
    link.assert_not_called()
    unlink.assert_not_called()


@pytest.mark.parametrize('action', ['assign', 'delete', 'link_task', 'link_deal ', '', None])
def test_company_forged_actions_never_dispatch(action):
    with patch.object(companies, 'link_entity') as link, patch.object(companies, 'update_company') as update:
        assert invoke_relation(relation_profile(['customers', 'deals']), action).status == 400
    link.assert_not_called()
    update.assert_not_called()


@pytest.mark.parametrize('action', ['link_contact', 'link_deal'])
@pytest.mark.parametrize('grants', [['customers', 'deals'], ['customers:update', 'deals:update']])
def test_existing_update_grants_authorize_company_links(action, grants):
    with patch.object(companies, 'link_entity', return_value=True) as mutate:
        assert invoke_relation(relation_profile(grants), action).status == 200
    assert mutate.call_count == 1


@pytest.mark.parametrize('role,active,status', [('owner', True, 200), ('owner', False, 403), ('manager', False, 403)])
def test_company_relation_owner_and_inactive_contract(role, active, status):
    profile = {**relation_profile(['customers', 'deals'], {'customers': {'update': False}}),
               'role': role, 'active': active}
    with patch.object(companies, 'link_entity', return_value=True) as mutate:
        assert invoke_relation(profile, 'link_deal').status == status
    assert mutate.call_count == (1 if status == 200 else 0)
