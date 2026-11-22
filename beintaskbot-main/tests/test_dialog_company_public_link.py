from io import BytesIO
from unittest.mock import Mock
from urllib.error import HTTPError
import json
import uuid

import pytest

import tenant_beinsystems_operator as operator
import tenant_dialog_company_public_link as link


ACCOUNT = 'account+a&b'
URL = 'https://1000.az/AbC123'
CID = str(uuid.uuid4())
ROUTE = dict(provider='connect', lead_id=17, channel_id=str(uuid.uuid4()), conversation_id='peer',
             expected_company_id=CID)


def envelope(url=URL, account=ACCOUNT):
    return {'status': 'success', 'data': {'short_url': url, 'account': account}}


@pytest.mark.parametrize('url', [
    'http://portal.example.test/path', 'javascript:alert(1)', 'https://test@portal.example.test/path',
    'https://portal.example.test\\@other.example.test/path', 'https://127.0.0.1/path',
    'https://portal.example.test:444/path', 'https://localhost/path',
    'https://1000.az/ABC12d\n', 'https://1000.az/abc123?q=x', 'https://1000.az/abc123#q',
    'https://1000.az/abc123/xyz', 'https://1000.az/ab123', 'https://1000.az/abc1234',
    'https://1000.az/abc123/', 'https://1000.az.evil.test/abc123', 'https://1000.az:443/abc123', '',
])
def test_unsafe_url_is_never_projected(url):
    with pytest.raises(operator.OperatorError) as error:
        link._public_link(envelope(url), ACCOUNT)
    assert error.value.status == 502


def test_shape_and_account_fail_closed():
    assert link._public_link(envelope(), ACCOUNT) == URL
    assert link._public_link({'status':'success','data':None},ACCOUNT) is None
    assert link._public_link(envelope('https://1000.az/a-_12X'), ACCOUNT) == 'https://1000.az/a-_12X'

    for response in (envelope(URL, 'different'), {'status': 'success', 'data': {'link': URL}},
                     {'status': 'error', 'data': {'url': URL}}):
        with pytest.raises(operator.OperatorError, match='Operator'):
            link._public_link(response, ACCOUNT)


def test_only_get_encodes_account_and_never_retries(monkeypatch):
    monkeypatch.setattr(operator, 'api_base_url', lambda: operator.OPERATOR_API_BASE_URL)
    opener = Mock()
    def response(request, *, timeout):
        assert request.full_url == 'https://operator-api.beinsystems.az/v1/task/getPublicLink?account=account%2Ba%26b'
        assert request.get_method() == 'GET'
        assert request.data is None
        assert request.get_header('Authorization') == 'Bearer synthetic'
        assert timeout == operator.REQUEST_TIMEOUT_SECONDS
        return BytesIO(json.dumps(envelope()).encode())
    base = operator.OPERATOR_API_BASE_URL
    opener.open.side_effect = response
    assert link._lookup_public_link(ACCOUNT, token='synthetic', base_url=base, opener=opener) == URL
    for error, status in (
            (TimeoutError('unconfirmed'), 502),
            (HTTPError(base, 403, 'forbidden', {}, None), 424),
            (HTTPError(base, 302, 'redirect', {}, None), 502)):
        prior = opener.open.call_count
        opener.open.side_effect = error
        with pytest.raises(operator.OperatorError) as denied:
            link._lookup_public_link(ACCOUNT, token='synthetic', base_url=base, opener=opener)
        assert denied.value.status == status and opener.open.call_count == prior + 1
    opener.open.side_effect = HTTPError(base,404,'missing link',{},None)
    assert link._lookup_public_link(ACCOUNT,token='synthetic',base_url=base,opener=opener) is None
    assert opener.open.call_count == 5
    with pytest.raises(operator.OperatorError):
        link._lookup_public_link(ACCOUNT, token='synthetic', base_url='https://unknown.test', opener=opener)
    assert opener.open.call_count == 5


def test_no_provider_call_without_proven_mapping_and_recheck_after(monkeypatch):
    monkeypatch.setattr(operator, '_tenant', lambda p: 'tenant')
    credentials = {'generation': 'g', 'connection_instance_id': 'i', 'token': 'synthetic',
                   'base_url': operator.OPERATOR_API_BASE_URL}
    authorize = Mock(return_value='not_connected')
    get_credentials = Mock(return_value=credentials)
    audit = Mock()
    upstream = Mock(return_value=URL)
    monkeypatch.setattr(link, '_snapshot', authorize)
    monkeypatch.setattr(operator, '_credentials', get_credentials)
    monkeypatch.setattr(operator, '_record_operator_read', audit)
    monkeypatch.setattr(link, '_lookup_public_link', upstream)
    assert link.lookup_link({}, **ROUTE) == {'state': 'not_connected'}
    authorize.return_value = 'not_linked'
    assert link.lookup_link({}, **ROUTE) == {'state': 'not_linked'}
    for mock in (get_credentials, audit, upstream):
        mock.assert_not_called()
    before = ('tenant', ACCOUNT, 'g', 'i')
    authorize.side_effect = [before, before, before]
    assert link.lookup_link({}, **ROUTE) == {'state': 'available', 'url': URL}
    upstream.assert_called_once_with(ACCOUNT,
        token='synthetic', base_url=operator.OPERATOR_API_BASE_URL)
    authorize.side_effect = [before, before, ('tenant', 'different', 'g', 'i')]
    with pytest.raises(operator.OperatorError) as changed:
        link.lookup_link({}, **ROUTE)
    assert changed.value.status == 409
    upstream.reset_mock()
    authorize.side_effect = [before, ('tenant', 'different', 'g', 'i')]
    with pytest.raises(operator.OperatorError):
        link.lookup_link({}, **ROUTE)
    upstream.assert_not_called()


def test_mapping_requires_current_generation_and_no_conflicting_accounts():
    cursor = Mock()
    cursor.fetchall.return_value = [{'account': ACCOUNT, 'current': False}]
    assert link._linked_account(cursor, 'tenant', CID, 'g', 'i') is None
    cursor.fetchall.return_value = [{'account': ACCOUNT, 'current': True},
                                   {'account': 'other', 'current': False}]
    assert link._linked_account(cursor, 'tenant', CID, 'g', 'i') is None
    cursor.fetchall.return_value = [{'account': ACCOUNT, 'current': True},
                                   {'account': ACCOUNT, 'current': False}]
    cursor.fetchone.return_value = {'conflict': False}
    assert link._linked_account(cursor, 'tenant', CID, 'g', 'i') == ACCOUNT
    cursor.fetchone.return_value = {'conflict': True}
    assert link._linked_account(cursor, 'tenant', CID, 'g', 'i') is None


@pytest.mark.asyncio
async def test_handler_rejects_get_cross_origin_forged_account_and_never_caches(monkeypatch):
    from types import SimpleNamespace
    from tenant_beinsystems_operator_api import dialog_company_public_link_handler

    profile = {'tenant_id': str(uuid.uuid4()), 'telegram_id': 17}
    monkeypatch.setenv('BEINSYSTEMS_OPERATOR_TENANT_ID', profile['tenant_id'])
    lookup = Mock(return_value={'state': 'available', 'url': URL})
    monkeypatch.setattr(link, 'lookup_link', lookup)
    origin = 'https://crm.test'
    handler = dialog_company_public_link_handler(lambda request: profile, origin, Mock())
    query = {**ROUTE, 'expected_tenant_id': profile['tenant_id'], 'expected_user_id': '17'}
    async def send(body=query, method='POST', request_origin=origin):
        payload = json.dumps(body).encode()
        async def read():
            return payload
        return await handler(SimpleNamespace(method=method, headers={'Origin': request_origin},
                              content_type='application/json', content_length=len(payload), read=read))
    results = [await send({**query, 'account': 'forged'}),
               await send({**query, 'expected_user_id': '18'}),
               await send(method='GET'), await send(request_origin='https://outside.test')]
    assert [response.status for response in results] == [400, 409, 403, 403]
    for response in results:
        assert response.headers['Cache-Control'] == 'no-store'
        assert URL.encode() not in response.body
    lookup.assert_not_called()
    response = await send()
    assert response.status == 200
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['Vary'] == 'Cookie'
    assert URL.encode() in response.body
    lookup.assert_called_once_with(profile, **ROUTE)
