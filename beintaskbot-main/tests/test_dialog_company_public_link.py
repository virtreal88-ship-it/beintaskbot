from io import BytesIO
from unittest.mock import MagicMock, Mock
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
    session=MagicMock()
    response=MagicMock()
    session.__enter__.return_value=session
    session.get.return_value.__enter__.return_value=response
    monkeypatch.setattr(operator,'_NoRedirectRequestsSession',lambda:session)
    response.status_code=200
    response.iter_content.return_value=[json.dumps(envelope()).encode()]
    base=operator.OPERATOR_API_BASE_URL
    assert link._lookup_public_link(ACCOUNT,token='synthetic',base_url=base)==URL
    session.get.assert_called_once_with(base+'/task/getPublicLink?account=account%2Ba%26b',
        headers={'Authorization':'Bearer synthetic','Accept':'application/json'},
        timeout=operator.REQUEST_TIMEOUT_SECONDS,allow_redirects=False,stream=True)
    assert session.trust_env is False
    for status,expected in ((403,424),(404,424),(302,502),(500,502)):
        response.status_code=status
        before=session.get.call_count
        with pytest.raises(operator.OperatorError) as error:
            link._lookup_public_link(ACCOUNT,token='synthetic',base_url=base)
        assert error.value.status==expected and session.get.call_count==before+1
    session.post.assert_not_called()
    with pytest.raises(operator.OperatorError):
        link._lookup_public_link(ACCOUNT,token='synthetic',base_url='https://unknown.test')
    assert session.get.call_count==5


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


def test_company_profile_projects_only_exact_verified_account_and_keeps_zero():
    as_read = {'status':'success', 'data':{'account':ACCOUNT,'phone':'+994 50 123 45 67',
        'balance':0,'partnerpin':'sensitive','email':'private@example.test'}}
    assert link._company_profile_fields(as_read, ACCOUNT) == {
        'registration_phone':'+994 50 123 45 67','balance':0}
    as_read['data']['phone'] = 'unknown'
    as_read['data']['balance'] = float('nan')
    assert link._company_profile_fields(as_read, ACCOUNT) == {'registration_phone':None,'balance':None}
    as_read['data']['account'] = 'different'
    with pytest.raises(operator.OperatorError) as denied:
        link._company_profile_fields(as_read, ACCOUNT)
    assert denied.value.status == 403


def test_company_profile_drops_provider_reply_after_rebinding(monkeypatch):
    monkeypatch.setattr(operator, '_tenant', lambda p: 'tenant')
    monkeypatch.setattr(operator, '_record_operator_read', Mock())
    monkeypatch.setattr(operator, '_credentials', Mock(return_value={
        'generation':'g', 'connection_instance_id':'i','token':'synthetic','base_url':operator.OPERATOR_API_BASE_URL}))
    before = ('tenant', ACCOUNT, 'g', 'i')
    monkeypatch.setattr(link, '_snapshot', Mock(return_value=before))
    monkeypatch.setattr(link.dialogue,'_connect',MagicMock())
    monkeypatch.setattr(link, '_locked_snapshot', Mock(return_value=('tenant','other','g','i')))
    read = Mock(return_value={'status':'success','data':{'account':ACCOUNT,'balance':10.5}})
    monkeypatch.setattr(operator, '_request_operator', read)
    with pytest.raises(operator.OperatorError) as changed:
        link.lookup_profile({}, **ROUTE)
    assert changed.value.status == 409
    read.assert_called_once_with('account_info', {'account':ACCOUNT}, requests_account_info=True,
        token='synthetic', base_url=operator.OPERATOR_API_BASE_URL)


def test_dialogue_lists_are_scoped_and_project_only_visible_fields():
    row = {'account':ACCOUNT,'linear_id':'BS-7','title':'Example','status':'Done','description':'private'}
    response={'status':'success','data':[row]}
    assert link._section_rows(response,'company_tasks',ACCOUNT)=={'rows':[['BS-7','Example','Done']]}
    for invalid in ({**row,'account':'wrong'},{**row,'title':None}):
        with pytest.raises(operator.OperatorError):
            link._section_rows({**response,'data':[invalid]},'company_tasks',ACCOUNT)
    assert link._section_rows({**response,'data':[]},'company_modules',ACCOUNT)=={'rows':[]}
    with pytest.raises(operator.OperatorError):
        link._section_rows({**response,'data':None},'company_history',ACCOUNT)


def test_first_copy_posts_only_the_verified_account_to_operator(monkeypatch):
    session=MagicMock()
    session.__enter__.return_value=session
    reply=session.post.return_value.__enter__.return_value
    reply.status_code=200
    reply.iter_content.return_value=[json.dumps(envelope()).encode()]
    monkeypatch.setattr(operator,'_NoRedirectRequestsSession',lambda:session)
    base=operator.OPERATOR_API_BASE_URL
    assert link._issue(ACCOUNT,{'token':'synthetic','base_url':base})==URL
    session.post.assert_called_once_with(base+'/task/publicLink',
        json={'account':ACCOUNT,'public_base_url':'https://operator.beinsystems.az'},
        headers={'Authorization':'Bearer synthetic','Accept':'application/json'},
        timeout=operator.REQUEST_TIMEOUT_SECONDS,allow_redirects=False,stream=True)
    assert session.trust_env is False


@pytest.mark.parametrize('allowed',[False,True])
def test_task_options_project_verified_account_and_current_action_permissions(monkeypatch, allowed):
    import tenant_dialog_company_actions as actions
    identity={'telegram_id':17}
    monkeypatch.setattr(operator,'_tenant',lambda p:'tenant')
    monkeypatch.setattr(operator,'_record_operator_read', Mock())
    monkeypatch.setattr(operator,'_credentials',Mock(return_value={
        'generation':'g','connection_instance_id':'i','token':'synthetic','base_url':operator.OPERATOR_API_BASE_URL}))
    scope=('tenant',ACCOUNT,'g','i')
    monkeypatch.setattr(link,'_snapshot',Mock(return_value=scope))
    monkeypatch.setattr(link,'_locked_snapshot',Mock(return_value=scope))
    monkeypatch.setattr(operator,'_request_operator',Mock(return_value={'status':'success','data':{'account':ACCOUNT}}))
    monkeypatch.setattr(link.dialogue,'_connect',MagicMock())
    monkeypatch.setattr(operator,'_current_profile',Mock(return_value=identity))
    gate=Mock(return_value={'task':allowed,'credit':not allowed})
    monkeypatch.setattr(actions,'capabilities',gate)
    result=link.lookup_profile(identity,**ROUTE)
    gate.assert_called_once_with(identity)
    assert '_exact_operator_account' not in result
    assert len(result['action_binding'])==64
    assert result['actions']=={'task':allowed,'credit':not allowed}
    if allowed:
        assert result['task_options']=={'account':ACCOUNT,'priorities':actions.PRIORITIES,'projects':actions.PROJECTS}
    else:
        assert 'task_options' not in result
    gate.return_value={'task':False,'credit':False}
    assert 'action_binding' not in link.lookup_profile(identity,**ROUTE)


def test_payment_history_projects_only_explicit_currency_and_original_amount():
    response={'status':'success','data':[
        {'account':ACCOUNT,'moment':'2026-11-03','amount':'-4.00','payment_source':'daily_expense'},
        {'account':ACCOUNT,'moment':'2026-11-04','amount':'+10.00','currency':'USD','payment_source':'credit',
         'customer_email':'private@example.test'},
        {'account':ACCOUNT,'moment':'unknown date','amount':'unknown','currency':'not an ISO code','payment_source':'provider text'}]}
    assert link._section_rows(response,'company_history',ACCOUNT)=={'rows':[
        ['2026-11-03','-4.00','daily_expense'],['2026-11-04','+10.00','credit','USD'],
        ['unknown date','unknown','provider text']]}
