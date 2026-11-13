"""Provider read scope does not grant native actions or external sends."""
from unittest.mock import MagicMock, Mock
import pytest
from tenant_policy import TenantPolicy
from tenant_dialog_access import lead_message_filter, require_kommo_lead_read, require_source
from tenant_linear_policy import TenantLinearError
from test_tenant_member_access import profile

TENANT='aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'


def person(scope='none'):
    p=profile(tenant_id=TENANT,permissions=[],modules={})
    p['workflow']['pipelines']=[{'pipeline_id':'10','active':True},{'pipeline_id':'11','active':True}]
    p['workflow']['policies']['members']['20']={'pipeline_ids':['10'],'access_scopes':{
        'dialogs':{a:scope for a in ('view','create','update','delete')},
        'deals':{a:'none' for a in ('view','create','update','delete')}}}
    return p


def deal(**kw):
    return {'tenant_id':TENANT,'kommo_lead_id':7,'pipeline_id':10,'raw':{},**kw}


@pytest.mark.parametrize('scope',['none','self','department','all'])
def test_assigned_pipeline_history_ignores_native_scope_but_send_does_not(monkeypatch,scope):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    policy=TenantPolicy(person(scope)); cur=MagicMock();cur.fetchone.return_value=None
    assert policy.can_read_kommo_chat(deal())
    assert lead_message_filter(cur,TENANT,policy,7,kommo_deal=deal())=="channel NOT LIKE 'connect:%%'"
    assert not policy.public_capabilities()['actions']['deals']['update']
    if scope!='all':
        with pytest.raises(TenantLinearError):require_source(cur,TENANT,policy,'kommo','','update')


@pytest.mark.parametrize('row',[deal(pipeline_id=11),deal(tenant_id='other'),deal(raw={'source':'connect'})])
def test_other_pipeline_tenant_and_connect_are_not_kommo_grants(row):
    policy=TenantPolicy(person());cur=MagicMock()
    with pytest.raises(TenantLinearError):require_kommo_lead_read(cur,TENANT,policy,7,row)
    cur.execute.assert_not_called()


def test_missing_deal_revoked_assignment_and_inactive_employee_denied():
    p=person();cur=MagicMock();cur.fetchone.return_value=None
    with pytest.raises(TenantLinearError):require_kommo_lead_read(cur,TENANT,TenantPolicy(p),7)
    p['workflow']['policies']['members']['20']['pipeline_ids']=[]
    with pytest.raises(TenantLinearError):require_kommo_lead_read(cur,TENANT,TenantPolicy(p),7,deal())
    p=person();p['active']=False
    assert not TenantPolicy(p).can_read_kommo_chat(deal())


def test_connect_route_blocks_forged_kommo_record():
    cur=MagicMock();cur.fetchone.return_value={'connect_channel_id':'source'}
    with pytest.raises(TenantLinearError):require_kommo_lead_read(cur,TENANT,TenantPolicy(person()),7,deal())


def test_history_reads_fresh_membership_and_excludes_connect_messages(monkeypatch):
    import test_tenant_hot_orders as loaders
    import tenant_scoped_access_store
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    p={**person(),'tenant_status':'active'}
    monkeypatch.setattr(tenant_scoped_access_store,'read_access_profile',Mock(return_value=p))
    connect=MagicMock();cur=connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect=[deal(),None]
    cur.fetchall.return_value=[]
    module=loaders.load('tenant_chat_history_store',{'_connect':connect,'ensure_schema':Mock(),
        '_read_tenant_workflow':Mock(return_value=p['workflow'])},{'tenant_platform','tenant_chat_history_schema'})
    result=module.page(p,7)
    assert result['messages']==[]
    sql,params=cur.execute.call_args.args
    assert "channel NOT LIKE 'connect:%%'" in sql
    assert params==[TENANT,7,121]
    assert tenant_scoped_access_store.read_access_profile.call_args.kwargs['lock'] is True
    cur.reset_mock();cur.fetchone.side_effect=[deal(pipeline_id=11),None,deal(pipeline_id=11)]
    with pytest.raises(TenantLinearError):module.page(p,7)
    cur.fetchall.assert_not_called()

@pytest.mark.asyncio
async def test_chat_api_pipeline_only_rechecks_after_refresh(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import sys
    import json
    from tenant_chat_api import ChatApiContext,handlers
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    p=person(); page=Mock(return_value={'messages':[]})
    monkeypatch.setitem(sys.modules,'tenant_chat_history_store',SimpleNamespace(page=page))
    sync=AsyncMock()
    handler=handlers(ChatApiContext(member=lambda _:p,policy=TenantPolicy,
        get_deal=lambda **_:deal(),list_messages=Mock(),sync_chat=sync,
        platform_error=RuntimeError,logger=Mock()))['chat']
    request=SimpleNamespace(rel_url=SimpleNamespace(query={'lead_id':'7','refresh':'true'}))
    response=await handler(request)
    assert response.status==200
    assert page.call_count==2
    sync.assert_awaited_once()
    page.side_effect=[{'messages':[]},TenantLinearError('revoked',403)]
    assert (await handler(request)).status==403


def test_kommo_attachment_native_deny_and_mixed_source_guard(monkeypatch):
    import test_tenant_hot_orders as loaders
    import tenant_scoped_access_store
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    p={**person(),'tenant_status':'active'}
    monkeypatch.setattr(tenant_scoped_access_store,'read_access_profile',lambda *a,**kw:p)
    connect=MagicMock();cur=connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    row={'external_id':'msg','channel':'kommo:one'}
    connection={'status':'connected','secrets':b'synthetic'}
    cur.fetchone.side_effect=[deal(),row,None,connection]
    module=loaders.load('tenant_chat_attachment_store',{'_connect':connect,'_fernet':Mock(),
        '_read_tenant_workflow':lambda *a:p['workflow']},{'tenant_platform'})
    assert module.load(p,7,'msg')['row']==row
    for item in [deal(pipeline_id=11),deal(tenant_id='other')]:
        cur.reset_mock();cur.fetchone.side_effect=[item]
        with pytest.raises(TenantLinearError):module.load(p,7,'msg')
        assert cur.execute.call_count==1
    cur.fetchone.side_effect=[deal(),{**row,'channel':'connect:another'},None]
    with pytest.raises(TenantLinearError):module.load(p,7,'msg')
