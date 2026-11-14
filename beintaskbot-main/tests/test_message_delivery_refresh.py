import asyncio
from unittest.mock import AsyncMock, Mock, patch
import pytest
import tenant_message_delivery as module
from tenant_linear_policy import TenantLinearError

CHANNEL='00000000-0000-4000-8000-000000000001'
ID='00000000-0000-4000-8000-000000000002'
ROUTE={'channel_id':CHANNEL,'conversation_id':'123@g.us','api_key':'synthetic','tenant_id':'t'}
KEY=(CHANNEL,'123@g.us',12)

def message(**kw):
    return {'id':'row','external_id':'connect:'+ID,'channel':'connect:'+CHANNEL,'source':'connect','source_channel_id':CHANNEL,
            'source_lead_id':12,'source_conversation_id':'123@g.us','direction':'outgoing','delivery_state':'pending',**kw}

def result(*rows):
    return {'dialog':{'provider':'connect','lead_id':12,'conversation_id':'123@g.us'},'messages':list(rows or [message()])}

def receipt(**kw):
    return {'channelId':CHANNEL,'conversationId':'123@g.us','data':[{'id':ID,'externalMessageId':'PROVIDER123','status':'delivered'}],**kw}


def test_candidates_never_guess_from_text_or_other_sources():
    rows=[message(),message(direction='incoming'),message(external_id='connect:PROVIDER123'),message(source='kommo')]
    assert len(module.candidates(result(*rows))[KEY])==1
    assert module.candidates({'dialog':{'provider':'kommo'},'messages':rows})=={}


def test_batch_is_bounded_and_exact_route():
    rows=[message(external_id='connect:'+f'00000000-0000-4000-8000-{i:012d}') for i in range(70)]
    assert len(module.candidates(result(*rows))[KEY])==50


def test_status_evidence_must_match_exact_source_and_id():
    requested={ID:message()}
    assert module.validated_statuses(receipt(),ROUTE,requested)=={ID:'delivered'}
    for wrong in [receipt(channelId='other'),receipt(conversationId='other'),receipt(data=[{'id':'other','status':'read'}]),
                  receipt(data=[{'id':ID,'status':'read'}]),receipt(data=[{'id':ID,'externalMessageId':'x','status':'read'}]),
                  receipt(data=[{'id':ID,'externalMessageId':'PROVIDER123','status':'unexpected'}])]:
        assert module.validated_statuses(wrong,ROUTE,requested)=={}
    assert module.validated_statuses(receipt(),ROUTE,{ID:message(provider_message_id='DIFFERENT123')})=={}


@pytest.mark.parametrize('old,new,expected',[
    ('pending','sent','sent'),('sent','delivered','delivered'),('delivered','read','read'),
    ('read','delivered','read'),('delivered','failed','delivered'),('sent','pending','sent'),
    ('failed','sent','failed'),('failed','delivered','delivered'),('pending','unknown','unknown')])
def test_monotonic_evidence(old,new,expected):
    row=message(delivery_state=old);module.apply_status(row,new)
    assert row['delivery_state']==expected


def test_history_read_refreshes_without_mutating_storage_or_content():
    output=result();fetch=AsyncMock(return_value={ID:'read'})
    with patch.object(module,'authorized_routes',return_value={KEY:ROUTE}) as auth,patch.object(module,'fetch_statuses',fetch):
        assert asyncio.run(module.enrich_history({},output))['messages'][0]['delivery_state']=='read'
        assert auth.call_count==2
        fetch.assert_awaited_once()
        assert output['messages'][0]['external_id']=='connect:'+ID


def test_denied_and_revoked_scope_never_release_receipts():
    fetch=AsyncMock(return_value={ID:'read'})
    with patch.object(module,'authorized_routes',side_effect=TenantLinearError('denied',403)),patch.object(module,'fetch_statuses',fetch):
        with pytest.raises(TenantLinearError):asyncio.run(module.enrich_history({},result()))
        fetch.assert_not_called()
    with patch.object(module,'authorized_routes',side_effect=[{KEY:ROUTE},{KEY:{**ROUTE,'api_key':'rotated'}}]),patch.object(module,'fetch_statuses',fetch):
        with pytest.raises(TenantLinearError):asyncio.run(module.enrich_history({},result()))


def test_lookup_failure_keeps_saved_status_never_infers_delivery():
    with patch.object(module,'authorized_routes',return_value={KEY:ROUTE}),patch.object(module,'fetch_statuses',AsyncMock(return_value={})):
        assert asyncio.run(module.enrich_history({},result()))['messages'][0]['delivery_state']=='pending'


def test_direct_conversation_is_scoped_independently():
    row=message(source_conversation_id='123@s.whatsapp.net')
    assert (CHANNEL,'123@s.whatsapp.net',12) in module.candidates(result(row))


def test_http_scope_content_limits_and_transport_fail_closed():
    async def run(headers, body, status=200):
        response=Mock(status=status,headers=headers)
        async def chunks(_):yield body
        response.content.iter_chunked=chunks
        request=Mock();request.__aenter__=AsyncMock(return_value=response);request.__aexit__=AsyncMock(return_value=False)
        session=Mock();session.get=Mock(return_value=request);session.__aenter__=AsyncMock(return_value=session);session.__aexit__=AsyncMock(return_value=False)
        with patch.object(module.aiohttp,'ClientSession',return_value=session),patch.object(module.aiohttp,'TCPConnector'),patch.object(module,'CONNECT_URL','https://connect.example'):
            found=await module.fetch_statuses(ROUTE,{ID:message()})
            assert session.get.call_args.kwargs['allow_redirects'] is False
            return found
    headers={'X-Connect-Channel-Id':CHANNEL,'X-Connect-Conversation-Id':'123@g.us','Content-Type':'application/json'}
    import json
    body=json.dumps(receipt()).encode()
    assert asyncio.run(run(headers,body))=={ID:'delivered'}
    for bad,content,status in [({**headers,'X-Connect-Channel-Id':'other'},body,200),
            ({**headers,'Content-Encoding':'gzip'},body,200),(headers,b'x'*32769,200),
            (headers,b'invalid',200),(headers,body,503)]:
        assert asyncio.run(run(bad,content,status))=={}


def test_authorized_routes_rejects_unrelated_source_and_never_writes():
    conn=Mock();cur=Mock();conn.__enter__=Mock(return_value=conn);conn.__exit__=Mock(return_value=False)
    cur.__enter__=Mock(return_value=cur);cur.__exit__=Mock(return_value=False);conn.cursor.return_value=cur
    selected={'lead_id':12,'connect_channel_id':CHANNEL,'conversation_id':'123@g.us'}
    with patch.object(module,'_connect',return_value=conn),patch.object(module,'access',return_value=('t','actor','policy')),patch.object(module,'assert_route',return_value=selected),patch.object(module,'connect_route',return_value=ROUTE):
        assert module.authorized_routes({}, {KEY:{ID:message()}})=={KEY:ROUTE}
        cur.execute.assert_not_called()
    with patch.object(module,'_connect',return_value=conn),patch.object(module,'access',return_value=('t','actor','policy')),patch.object(module,'assert_route',return_value={**selected,'conversation_id':'other'}),patch.object(module,'connect_route') as resolve:
        assert module.authorized_routes({}, {KEY:{ID:message()}})=={}
        resolve.assert_not_called()


def test_group_history_adds_exact_group_id_for_client_receipt_retention():
    from pathlib import Path
    source=Path('tenant_operator_dialogs.py').read_text()
    group=source[source.index('messages=group_history('):source.index('            else:',source.index('messages=group_history('))]
    assert "message['source_conversation_id']=conversation" in group
