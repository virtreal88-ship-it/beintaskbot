"""Pairing unit boundary: synthetic store/provider only, never real WhatsApp."""
import asyncio
from contextlib import nullcontext
import hashlib
import json
from unittest.mock import Mock, patch
import pytest
import tenant_connect_pairing as p
import tenant_connect_transport as transport
from tenant_linear_policy import TenantLinearError
from tenant_platform import TenantPlatformError

T='00000000-0000-0000-0000-000000000101'
R='00000000-0000-0000-0000-000000000102'
C='00000000-0000-0000-0000-000000000103'
PROFILE={'tenant_id':T,'telegram_id':11,'role':'owner'}
KEY='synthetic-only-key'
PR=p._provider_request_id(T,11,R)
def data(action='pairing_start',**extra):
    return dict(action=action,request_id=R,expected_tenant_id=T,expected_user_id='11',confirm_company_impact=True,**extra)

@pytest.fixture
def flow(monkeypatch):
    monkeypatch.setattr(p,'_owner',lambda profile:None)
    monkeypatch.setattr(p,'_tenant_connect_lock',lambda tenant:nullcontext())
    monkeypatch.setattr(p,'_stored_connect_credentials',lambda profile:{'api_key':KEY})
    row={'tenant_id':T,'actor_id':11,'request_id':R,'provider_request_id':PR,'name':'WhatsApp','key_fingerprint':hashlib.sha256(KEY.encode()).hexdigest(),'state':'creating','channel_id':None,'capability':'v1'}
    monkeypatch.setattr(p,'_read',lambda tenant,actor,request_id,**kw:row if tenant==T and actor==11 and (request_id==R or kw.get('pending')) else None)
    def save(value,**kw):
        for k,v in kw.items():
            if v is not None:value[k]=v
    monkeypatch.setattr(p,'_save',save)
    calls=[]
    def request(key,method,path,payload=None,**kw):
        calls.append((key,method,path,payload,kw))
        if path.endswith('/capabilities'):return {'createIdempotency':'v1','channelCreatePath':'/v1/channels/idempotent'}
        if path=='/v1/channels/idempotent':return {'id':C,'provider':'whatsapp','idempotency':{'version':'v1','requestId':PR,'replayed':False}}
        if path.endswith('/qr'):return {'code':'synthetic-qr'}
        if path.endswith('/status'):return {'id':C,'status':'connecting','phone':'','phoneConflict':False}
        raise AssertionError(path)
    monkeypatch.setattr(p,'_request',request)
    configure=Mock();monkeypatch.setattr(p,'_configure_locked',configure)
    return row,calls,request,configure


def test_initial_qr_resume_poll_and_explicit_completion(flow,monkeypatch):
    row,calls,request,configure=flow
    first=p._pairing(PROFILE,data(),'https://crm.test/webhook')['pairing']
    assert first['qr']=='synthetic-qr' and row['channel_id']==C and row['state']=='awaiting_scan'
    configure.assert_not_called()
    assert calls[1][3:]==({'name':'WhatsApp','provider':'whatsapp'},{'idempotency_key':PR})
    def connected(key,method,path,payload=None,**kw):
        if path.endswith('/status'):
            calls.append((key,method,path,payload,kw))
            return {'id':C,'status':'connected','phone':'+994501234567','phoneConflict':False}
        return request(key,method,path,payload,**kw)
    monkeypatch.setattr(p,'_request',connected)
    count=len(calls)
    current=p._pairing(PROFILE,data('pairing_status'),'https://crm.test/webhook')['pairing']
    assert current['state']=='connected' and 'qr' not in current
    assert [call[2] for call in calls[count:]]==['/v1/channels/'+C+'/status']
    configure.assert_not_called()
    p._pairing(PROFILE,data(),'https://crm.test/webhook')
    assert len([c for c in calls if c[1]=='POST'])==1
    result=p._pairing(PROFILE,data('pairing_complete'),'https://crm.test/webhook')
    assert result['pairing']['state']=='complete'
    assert configure.call_args.args[1]['channel_ids']==[C]
    assert configure.call_args.args[1]['api_key']==KEY


def test_lost_reply_retries_same_key_and_body_only_after_capability(flow,monkeypatch):
    row,calls,request,configure=flow
    def lost(key,method,path,payload=None,**kw):
        if method=='POST':
            calls.append((key,method,path,payload,kw));raise TenantPlatformError('timeout after commit')
        return request(key,method,path,payload,**kw)
    monkeypatch.setattr(p,'_request',lost)
    assert p._pairing(PROFILE,data(),'x')['pairing']['state']=='unknown'
    assert row['channel_id'] is None
    original=calls[-1]
    count=len(calls)
    assert p._pairing(PROFILE,data('pairing_status'),'x')['pairing']['state']=='unknown'
    assert len(calls)==count
    monkeypatch.setattr(p,'_request',request)
    p._pairing(PROFILE,data(),'x')
    assert [c for c in calls if c[1]=='POST']==[original,original]
    configure.assert_not_called()

@pytest.mark.parametrize('bad', [{}, {'createIdempotency':'v0'}, {'createIdempotency':'v1'}, {'createIdempotency':'v1','channelCreatePath':'/v1/channels'}])
def test_no_legacy_fallback(flow,monkeypatch,bad):
    row,calls,request,configure=flow
    def absent(key,method,path,payload=None,**kw):
        assert method=='GET' and path.endswith('/capabilities');return bad
    monkeypatch.setattr(p,'_request',absent)
    with pytest.raises(TenantLinearError):p._pairing(PROFILE,data(),'x')
    assert not calls and row['channel_id'] is None

@pytest.mark.parametrize('echo',[None,{}, {'version':'v1','requestId':C,'replayed':False}, {'version':'v1','requestId':R,'replayed':'false'}])
def test_bad_echo_blocks_new_creation_not_retry(flow,monkeypatch,echo):
    row,calls,request,configure=flow
    monkeypatch.setattr(p,'_request',lambda key,method,path,payload=None,**kw: {'createIdempotency':'v1','channelCreatePath':'/v1/channels/idempotent'} if method=='GET' else {'id':C,'provider':'whatsapp','idempotency':echo})
    with pytest.raises(TenantLinearError):p._pairing(PROFILE,data(),'x')
    assert row['state']=='blocked' and row['channel_id'] is None
    forbidden=Mock(side_effect=AssertionError('must not retry'));monkeypatch.setattr(p,'_request',forbidden)
    with pytest.raises(TenantLinearError):p._pairing(PROFILE,data(),'x')
    forbidden.assert_not_called()

@pytest.mark.parametrize('current',[
 {'id':C,'status':'connecting','phone':'+994501234567'},
 {'id':C,'status':'connected','phone':''},
 {'id':C,'status':'connected','phone':'not a phone'},
 {'id':C,'status':'connected','phone':'+994501234567','phoneConflict':True},
 {'id':R,'status':'connected','phone':'+994501234567'},
])
def test_complete_requires_fresh_exact_id_connected_valid_phone_no_conflict(flow,monkeypatch,current):
    row,calls,request,configure=flow;row.update(channel_id=C,state='awaiting_scan')
    monkeypatch.setattr(p,'_request',lambda *a,**kw:current)
    with pytest.raises(TenantLinearError):p._pairing(PROFILE,data('pairing_complete'),'x')
    configure.assert_not_called();assert row['state']!='complete'


def test_key_rotation_payload_drift_foreign_actor_and_context_denied(flow,monkeypatch):
    row,calls,request,configure=flow
    monkeypatch.setattr(p,'_stored_connect_credentials',lambda profile:{'api_key':'rotated'})
    with pytest.raises(TenantLinearError,match='key_changed'):p._pairing(PROFILE,data(),'x')
    monkeypatch.setattr(p,'_stored_connect_credentials',lambda profile:{'api_key':KEY})
    with pytest.raises(TenantLinearError,match='payload_changed'):p._pairing(PROFILE,data(name='different'),'x')
    foreign={**PROFILE,'telegram_id':12};payload={**data('pairing_status'),'expected_user_id':'12'}
    with pytest.raises(TenantLinearError,match='not_found'):p._pairing(foreign,payload,'x')
    with pytest.raises(TenantLinearError,match='context_changed'):p._pairing(PROFILE,{**data(),'expected_user_id':'12'},'x')
    with pytest.raises(TenantLinearError,match='confirmation_required'):p._pairing(PROFILE,{**data(),'confirm_company_impact':False},'x')
    assert calls==[]


def test_owned_write_only_receipt_not_inventory(monkeypatch):
    from test_shared_integration_scopes import setup_guard,profile,SESSION,context
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    factory,conn,cur,read=setup_guard(profile({'view':'none','create':'all','update':'all'}))
    monkeypatch.setattr(p,'_connect',factory);monkeypatch.setattr(p,'_schema',lambda c:None)
    own={'pairing':{'request_id':R,'channel_id':C,'qr':'own-new-source'}}
    body={**context(confirm_company_impact=True),'action':'pairing_start','request_id':R}
    worker=Mock(return_value=own);monkeypatch.setattr(p,'_pairing',worker)
    with read: assert p.pairing(SESSION,body,'x')==own
    denied=profile({'view':'all','create':'none','update':'none'})
    with patch('tenant_shared_integration_access.read_access_profile',return_value=denied):
        with pytest.raises(TenantLinearError):p.pairing(SESSION,body,'x')
    assert worker.call_count==1


def test_request_sends_idempotency_header_only_on_channel_create(monkeypatch):
    response=Mock(status_code=201);response.json.return_value={'id':C}
    req=Mock(return_value=response);monkeypatch.setattr(transport.requests,'request',req)
    transport._request(KEY,'POST','/v1/channels/idempotent',{'provider':'whatsapp'},idempotency_key=R)
    assert req.call_args.kwargs['headers']['Idempotency-Key']==R
    assert req.call_args.kwargs['allow_redirects'] is False
    with pytest.raises(TenantPlatformError):transport._request(KEY,'POST','/v1/webhooks',{},idempotency_key=R)
    assert req.call_count==1


def test_provider_uuid_namespace_isolated_for_shared_keys():
    assert p._provider_request_id(T,11,R)==p._provider_request_id(T,11,R)
    assert len({p._provider_request_id(T,11,R),p._provider_request_id(T,12,R),p._provider_request_id(C,11,R)})==3
    assert p._provider_request_id(T,11,R)!=R


@pytest.mark.parametrize('remote',[
 {'id':C,'status':'connecting','phone':'+994501234567'},
 {'id':C,'status':'connected','phone':'+994509999999'},
 {'id':C,'status':'connected','phone':'+994501234567','phoneConflict':True},
])
def test_final_configure_revalidates_snapshot_before_webhook_or_local_write(monkeypatch,remote):
    monkeypatch.setattr(transport,'_owner',lambda _:None)
    request=Mock(return_value={'data':[remote]});monkeypatch.setattr(transport,'_request',request)
    connection=Mock(side_effect=AssertionError('no database mutation before verification'))
    monkeypatch.setattr(transport,'_connect',connection)
    with pytest.raises(TenantLinearError,match='connection_changed'):
        transport._configure_locked(PROFILE,{'api_key':KEY,'channel_ids':[C]},'x',expected_pairing={'id':C,'phone':'+994501234567'})
    assert request.call_count==1 and request.call_args.args[1:3]==('GET','/v1/channels')
    connection.assert_not_called()


def test_http_pairing_post_only_origin_guard_and_no_store(monkeypatch):
    import tenant_connect_api as api
    class Request:
        method='POST';content_type='application/json';headers={'Origin':'https://crm.test'}
        async def json(self):return data()
    worker=Mock(return_value={'pairing':{'request_id':R,'state':'unknown'}})
    monkeypatch.setattr(p,'pairing',worker)
    handler=api.settings_handler(lambda _:PROFILE,'https://crm.test',Mock())
    response=asyncio.run(handler(Request()))
    assert response.status==200 and response.headers['Cache-Control']=='no-store'
    worker.assert_called_once_with(PROFILE,data(),'https://crm.test/api/platform/connect/webhook')
    request=Request();request.headers={'Origin':'https://foreign.test'}
    assert asyncio.run(handler(request)).status==403
    assert worker.call_count==1
    assert asyncio.run(api.settings_handler(lambda _:None,'https://crm.test',Mock())(Request())).status==403
    assert worker.call_count==1
