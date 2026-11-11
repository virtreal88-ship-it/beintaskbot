"""Synthetic read receipts only: no provider, credentials or network access."""
import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch
import pytest

from tenant_crm_import_provenance import ObservedKommoPage, observe_page
from tenant_kommo import TenantKommo

TENANT = '00000000-0000-0000-0000-000000000101'
STAMP = datetime(2026, 11, 10, tzinfo=timezone.utc)


def credentials(**changes):
    return {'provider': 'kommo', 'auth_mode': 'oauth', 'connected_at': STAMP,
        'account_domain': 'synthetic.kommo.com', 'access_token': 'synthetic-only',
        'refresh_token': 'synthetic-only', 'token_expires_at': '2999-01-01T00:00:00+00:00', **changes}


def response(code=200, *, history=None, payload=None):
    return SimpleNamespace(status_code=code, history=[] if history is None else history,
        content=b'{}', json=lambda: payload or {'_embedded': {'contacts': [{'id': 9}]}})


def service(values):
    context = SimpleNamespace(credentials=Mock(side_effect=values), replace_tokens=Mock(),
        logger=Mock(), platform_error=ValueError)
    client = TenantKommo(context)
    client.settings = lambda: ('synthetic', 'synthetic', 'https://example.invalid/callback')
    return client


@pytest.mark.parametrize('refresh_at', ['expired_before_send', 'unauthorized_response'])
def test_receipt_uses_actual_post_refresh_credentials_without_extra_request(refresh_at):
    before = credentials(token_expires_at='2000-01-01T00:00:00Z') if refresh_at == 'expired_before_send' else credentials()
    after = credentials(account_domain='reconnected.kommo.com', connected_at=STAMP.replace(day=11))
    client = service([before, after])
    with patch('tenant_kommo.wait_slot'), \
         patch('tenant_kommo.requests.request', side_effect=[response()] if refresh_at=='expired_before_send' else [response(401), response()]) as send, \
         patch('tenant_kommo.requests.post', return_value=response(payload={'access_token':'synthetic'})):
        result = asyncio.run(client.request(TENANT, 'GET', 'contacts', params={'limit':250}))
    assert isinstance(result, ObservedKommoPage)
    assert result.observation.binding.account_id == 'reconnected.kommo.com'
    assert result.observation.binding.connection_generation.endswith('2026-11-11T00:00:00+00:00')
    assert send.call_count == (1 if refresh_at=='expired_before_send' else 2)  # No verification GET.
    assert send.call_args.args[1] == 'https://reconnected.kommo.com/api/v4/contacts'
    assert send.call_args.kwargs['params'] == {'limit':250}
    assert json.loads(json.dumps(result)) == {'_embedded': {'contacts': [{'id': 9}]}}
    assert 'synthetic-only' not in repr(result.observation)


@pytest.mark.parametrize('case', ['naked_credentials','redirect','json_metadata','unsupported','non_success'])
def test_unbound_or_redirected_response_never_carries_evidence(case):
    values = credentials()
    if case in ('naked_credentials','json_metadata'): values.pop('connected_at')
    client = service([values])
    reply = response(history=[object()] if case == 'redirect' else [],
        code=302 if case=='non_success' else 200,
        payload={'_embedded': {'contacts': [{'id': 9}]}, 'observation': {'binding':'forged'}})
    with patch('tenant_kommo.wait_slot'), patch('tenant_kommo.requests.request', return_value=reply):
        result = asyncio.run(client.request(TENANT, 'GET', 'account' if case == 'unsupported' else 'contacts'))
    assert type(result) is dict


@pytest.mark.parametrize('rows', [[{'id': 0}], [{'id': True}], [{'id': '01'}], [{'id':9},{'id':9}], [None], [{}]*251])
def test_invalid_or_ambiguous_response_ids_are_not_evidence(rows):
    result = observe_page(TENANT, 'contacts', credentials(), {'_embedded': {'contacts':rows}})
    assert type(result) is dict


def test_manual_source_generation_is_separate_and_no_json_evidence_is_accepted():
    result = observe_page(TENANT, 'contacts', credentials(provider='kommo_manual',auth_mode='manual'),
        {'_embedded': {'contacts':[{'id':9}]}})
    assert result.observation.binding.connection_generation.startswith('kommo_manual:')
    assert result.observation.external_ids == frozenset({'9'})


def test_worker_passes_receipt_separately_without_new_provider_reads():
    from tenant_crm_sync_worker import sync_one_page
    receipt = observe_page(TENANT, 'contacts', credentials(), {'_embedded': {'contacts':[{'id':9}]}})
    store = SimpleNamespace(row={'tenant_id':TENANT,'resource':'contacts','next_page':1},
        connection_is_current=lambda: True, save_page=Mock())
    async def request(*args, **kwargs): return receipt
    asyncio.run(sync_one_page(store, request))
    assert store.save_page.call_args.kwargs['observation'] == receipt.observation
    assert store.save_page.call_args.args[0] == [{'raw': {'id':9}}]


@pytest.mark.parametrize('mode', ['oauth','manual'])
def test_credential_source_metadata_matches_the_selected_token_snapshot(mode):
    from unittest.mock import MagicMock
    import tenant_integration_store as store
    connection=MagicMock()
    conn=connection.return_value.__enter__.return_value
    cur=conn.cursor.return_value.__enter__.return_value
    oauth={'provider':'kommo','status':'connected' if mode=='oauth' else 'awaiting_oauth',
        'account_domain':'oauth.kommo.com','connected_at':STAMP,'metadata':{},
        'secrets':json.dumps({'access_token':'synthetic-oauth','refresh_token':'synthetic-refresh'}).encode()}
    manual={'provider':'kommo_manual','status':'connected','account_domain':'manual.kommo.com',
        'connected_at':STAMP.replace(day=9),'metadata':{},
        'secrets':json.dumps({'tenant_id':TENANT,'provider':'kommo_manual','access_token':'synthetic-manual'}).encode()}
    cur.fetchall.return_value=[oauth,manual]
    cipher=Mock();cipher.decrypt.side_effect=lambda value: value
    with patch.object(store,'_connect',connection), patch.object(store,'_ensure_schema'), \
         patch.object(store,'_fernet',return_value=cipher):
        result=store.kommo_credentials(tenant_id=TENANT)
    selected=oauth if mode=='oauth' else manual
    assert result['provider'] == selected['provider']
    assert result['connected_at'] == selected['connected_at']
    assert result['account_domain'] == selected['account_domain']
    assert result['access_token'] == 'synthetic-'+mode
    assert cur.execute.call_count == 1 and 'connected_at' in cur.execute.call_args.args[0]
    assert cur.execute.call_args.args[0].strip().startswith('SELECT')
