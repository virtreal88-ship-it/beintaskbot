import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock, patch
import pytest
import tenant_group_participants as module
from tenant_linear_policy import TenantLinearError

NOW=datetime(2026,11,14,12,tzinfo=timezone.utc)
def snapshot(**kw):
    return {'status':'available','observedAt':NOW.isoformat(),'phones':['+994500000001'],'total':2,'unresolved':1,**kw}

def test_only_recent_valid_numbers_are_projected():
    assert module.projection(snapshot(),NOW)['phones']==['+994500000001']
    for change in [{'phones':['123456789@lid']},{'phones':['<script>']},{'total':True},
            {'unresolved':3},{'observedAt':'2026-11-14T10:00:00Z'},{'observedAt':'2026-11-15T12:00:00Z'},
            {'observedAt':'2026-11-14T12:00:00'},{'phones':None}]:
        assert module.projection(snapshot(**change),NOW)=={'status':'unavailable'}
    assert module.projection(snapshot(phones=[],total=0,unresolved=0),NOW)['total']==0

def test_no_fetch_before_authority_and_recheck_after_fetch():
    route={'tenant':'t','channel_id':'a','conversation_id':'1@g.us','api_key':'synthetic'}
    fetch=AsyncMock(return_value={'status':'available'})
    with patch.object(module,'authorized_route',side_effect=TenantLinearError('denied',403)),patch.object(module,'fetch_cached',fetch):
        with pytest.raises(TenantLinearError):asyncio.run(module.get_participants({},{}))
        fetch.assert_not_called()
    with patch.object(module,'authorized_route',side_effect=[route,{**route,'api_key':'replaced'}]),patch.object(module,'fetch_cached',fetch):
        with pytest.raises(TenantLinearError):asyncio.run(module.get_participants({},{}))

def test_route_must_match_exact_authorized_source_and_group():
    selected={'lead_id':12,'connect_channel_id':'a','conversation_id':'1@g.us'}
    conn=Mock();cur=Mock();conn.__enter__=Mock(return_value=conn);conn.__exit__=Mock(return_value=False);cur.__enter__=Mock(return_value=cur);cur.__exit__=Mock(return_value=False);conn.cursor.return_value=cur
    params={'provider':'connect','channel_id':'a','conversation_id':'1@g.us','lead_id':12}
    with patch.object(module,'_connect',return_value=conn),patch.object(module,'access',return_value=('tenant','actor','policy')),patch.object(module,'assert_route',return_value=selected) as authorized,patch.object(module,'connect_route',return_value={'channel_id':'b','conversation_id':'1@g.us'}):
        with pytest.raises(TenantLinearError):module.authorized_route({},params)
        authorized.assert_called_once_with(cur,'tenant','policy','connect','1@g.us','a',12)
        cur.execute.assert_not_called()
    with pytest.raises(TenantLinearError):module.authorized_route({}, {**params,'conversation_id':'123@s.whatsapp.net'})
