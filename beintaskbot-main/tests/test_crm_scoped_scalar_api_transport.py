"""No database or provider access; API provenance routing and bound transport."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest
import tenant_crm_native_api as contacts_api
import tenant_crm_native_deal_api as deals_api
import tenant_crm_sync_outbox as legacy
import tenant_kommo as kommo
from tenant_crm_relation_contract import ProviderBinding

TENANT='00000000-0000-0000-0000-000000000101'
RECORD='00000000-0000-0000-0000-000000000501'
PROFILE={'tenant_id':TENANT,'telegram_id':11,'role':'owner','active':True,
         'permissions':['customers','deals'],'modules':{'customers':True,'deals':True}}


@pytest.mark.parametrize('api,entity,body',[
    (contacts_api,'contact',{'action':'update','name':'New'}),
    (deals_api,'deal',{'action':'update','title':'New','pipeline_id':'p','stage_id':'s'})])
@pytest.mark.parametrize('scoped',[False,True])
def test_api_does_not_duplicate_scoped_store_event_in_legacy_queue(monkeypatch,api,entity,body,scoped):
    result={'id':RECORD}
    if scoped:result['sync']={'mode':'scoped_scalar','state':'pending','event_id':RECORD}
    monkeypatch.setattr(api,'update_contact' if entity=='contact' else 'update_deal',Mock(return_value=result))
    monkeypatch.setattr(api,'_expected_identity' if entity=='contact' else 'require_expected_context',Mock())
    queued=Mock(return_value=1);monkeypatch.setattr(legacy,'enqueue_change',queued)
    request=SimpleNamespace(method='POST',match_info={'id':RECORD},query={},
        content_type='application/json',headers={'Origin':'https://crm.test'},json=AsyncMock(return_value=body))
    response=asyncio.run(api.handler(lambda _:PROFILE,'https://crm.test',Mock())(request))
    assert response.status==200
    if scoped:queued.assert_not_called()
    else:queued.assert_called_once_with(TENANT,entity,RECORD)


@pytest.fixture
def transport(monkeypatch):
    context=SimpleNamespace(platform_error=ValueError,credentials=Mock(return_value={
        'account_domain':'synthetic.kommo.com','access_token':'synthetic-only',
        'auth_mode':'manual','token_expires_at':''}))
    send=Mock(return_value=SimpleNamespace(status_code=200,content=b'{}',json=lambda:{'id':10,'name':'New'}))
    refresh=Mock(side_effect=AssertionError('Bound transport must never refresh'))
    monkeypatch.setattr(kommo.requests,'request',send);monkeypatch.setattr(kommo.requests,'post',refresh)
    monkeypatch.setattr(kommo,'wait_slot',Mock())
    binding=ProviderBinding(TENANT,'kommo','synthetic.kommo.com','kommo_manual:synthetic')
    return kommo.TenantKommo(context),binding,send,context


def test_bound_transport_uses_one_object_patch_without_retry(transport):
    service,binding,send,_=transport
    asyncio.run(service.request_bound(binding,'PATCH','leads/10',json_body={'name':'New'}))
    assert send.call_count==1
    assert send.call_args.args==('PATCH','https://synthetic.kommo.com/api/v4/leads/10')
    assert send.call_args.kwargs['json']=={'name':'New'}
    assert send.call_args.kwargs['allow_redirects'] is False
    send.return_value.status_code=401
    with pytest.raises(ValueError):asyncio.run(service.request_bound(binding,'PATCH','leads/10',json_body={'name':'Again'}))
    assert send.call_count==2


@pytest.mark.parametrize('body',[{'_embedded':{'contacts':[{'id':1}]}},{'contact_id':'1'}, {'partner_id':'x'}, {}])
def test_bound_transport_rejects_relationship_or_empty_body(transport,body):
    service,binding,send,_=transport
    with pytest.raises(ValueError):asyncio.run(service.request_bound(binding,'PATCH','leads/10',json_body=body))
    send.assert_not_called()


def test_bound_transport_rejects_different_account_before_network(transport):
    service,binding,send,context=transport
    context.credentials.return_value['account_domain']='other.kommo.com'
    with pytest.raises(ValueError):asyncio.run(service.request_bound(binding,'GET','leads/10'))
    send.assert_not_called()


def test_expiring_oauth_does_not_deadlock_guard_via_refresh(transport):
    service,_,send,context=transport
    context.credentials.return_value.update(auth_mode='oauth',token_expires_at='2000-01-01T00:00:00+00:00')
    binding=ProviderBinding(TENANT,'kommo','synthetic.kommo.com','kommo:synthetic')
    with pytest.raises(ValueError):asyncio.run(service.request_bound(binding,'GET','leads/10'))
    send.assert_not_called()


@pytest.mark.parametrize('status', [301, 302, 303, 307, 308])
@pytest.mark.parametrize('destination', ['/api/v4/leads/999', 'https://other.kommo.com/api/v4/leads/10'])
def test_bound_transport_does_not_follow_redirect_to_another_record_or_account(transport, monkeypatch, status, destination):
    """Exercise Requests' real redirect machinery; only adapter I/O is fake."""
    import requests
    service, binding, _, _ = transport
    calls = []
    def adapter_send(adapter, request, **kwargs):
        calls.append((request.method, request.url, request.body))
        response = requests.Response()
        response.status_code = status
        response.url = request.url
        response.request = request
        response.headers['Location'] = destination
        response._content = b'{}'
        return response
    monkeypatch.setattr(requests, 'request', requests.api.request)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', adapter_send)
    with pytest.raises(ValueError, match='not accepted'):
        asyncio.run(service.request_bound(binding, 'PATCH', 'leads/10', json_body={'name': 'New'}))
    assert calls == [('PATCH', 'https://synthetic.kommo.com/api/v4/leads/10', b'{"name": "New"}')]
