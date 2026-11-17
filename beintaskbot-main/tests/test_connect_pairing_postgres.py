"""Disposable localhost PostgreSQL only; provider calls are synthetic mocks."""
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
import pytest
from test_employee_card_consistency_postgres import cards
from test_company_employees_navigation_postgres import A, B, database, db, staff
import tenant_connect_pairing as pairing
import tenant_connect_transport as transport
from tenant_linear_policy import TenantLinearError
from tenant_platform import TenantPlatformError

REQUEST='00000000-0000-0000-0000-000000008001'
CHANNEL='00000000-0000-0000-0000-000000008002'
OTHER='00000000-0000-0000-0000-000000008003'

@pytest.fixture
def flow(cards,monkeypatch):
    f=cards;monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    for module in (pairing,transport):monkeypatch.setattr(module,'_connect',f['factory'])
    with f['factory']() as conn:
        transport._schema(conn)
        conn.execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value) VALUES(%s,'members',%s::jsonb) ON CONFLICT(tenant_id,policy_key) DO UPDATE SET value=EXCLUDED.value",
            (A,json.dumps({'61':{'access_scopes':{'integrations':{'view':'none','create':'all','update':'all'}}}})))
        conn.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name,status,active) VALUES(%s,%s,'Existing','connected',TRUE)",(A,OTHER))
    f['profile']={'tenant_id':A,'telegram_id':61,'role':'owner'}
    f['payload']={'action':'pairing_start','request_id':REQUEST,'expected_tenant_id':A,'expected_user_id':'61','confirm_company_impact':True}
    monkeypatch.setattr(pairing,'_stored_connect_credentials',lambda profile:{'api_key':'synthetic-only'})
    f['calls']=[];f['lost']=False;f['connected']=False
    def remote(key,method,path,payload=None,**kw):
        f['calls'].append((method,path,payload,kw))
        if path.endswith('/capabilities'):return {'createIdempotency':'v1','channelCreatePath':'/v1/channels/idempotent'}
        if method=='POST':
            assert path=='/v1/channels/idempotent' and kw=={'idempotency_key':pairing._provider_request_id(A,61,REQUEST)}
            if f['lost']:raise TenantPlatformError('synthetic lost reply after create')
            return {'id':CHANNEL,'provider':'whatsapp','idempotency':{'version':'v1','requestId':pairing._provider_request_id(A,61,REQUEST),'replayed':True}}
        if path.endswith('/qr'):return {'code':'synthetic-test-qr'}
        if path.endswith('/status'):return {'id':CHANNEL,'status':'connected' if f['connected'] else 'connecting','phone':'+994501234567' if f['connected'] else '', 'phoneConflict':False}
        raise AssertionError(path)
    monkeypatch.setattr(pairing,'_request',remote)
    def configure(profile,payload,url,*,expected_pairing):
        assert expected_pairing=={'id':CHANNEL,'phone':'+994501234567'}
        assert payload['channel_ids']==[CHANNEL] and payload['api_key']=='synthetic-only'
        with f['factory']() as conn:
            conn.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name,status,active) VALUES(%s,%s,'New','connected',TRUE) ON CONFLICT(tenant_id,connect_channel_id) DO NOTHING",(A,CHANNEL))
    f['configure']=Mock(side_effect=configure);monkeypatch.setattr(pairing,'_configure_locked',f['configure'])
    return f


def call(f,action='pairing_start',**overrides):
    return pairing.pairing(f['profile'],{**f['payload'],'action':action,**overrides},'https://synthetic.test/webhook')['pairing']


def test_durable_unknown_resume_and_no_preactivation(flow):
    f=flow;f['lost']=True
    assert call(f)['state']=='unknown'
    with f['factory']() as conn:
        row=conn.execute('SELECT * FROM saas_connect_pairing_intents WHERE tenant_id=%s AND actor_id=61',(A,)).fetchone()
        assert str(row['request_id'])==REQUEST and row['channel_id'] is None
        assert 'synthetic-only' not in json.dumps(dict(row),default=str)
        assert not conn.execute('SELECT 1 FROM saas_connect_channels WHERE tenant_id=%s AND connect_channel_id=%s',(A,CHANNEL)).fetchone()
    assert call(f,'pairing_status')['state']=='unknown'
    assert len([r for r in f['calls'] if r[0]=='POST'])==1
    f['lost']=False
    resumed=call(f,request_id=OTHER)
    assert resumed['request_id']==REQUEST and resumed['qr']=='synthetic-test-qr'
    f['configure'].assert_not_called()
    with pytest.raises(TenantLinearError):call(f,'pairing_complete')
    f['connected']=True
    assert call(f,'pairing_status')['state']=='connected'
    assert call(f,'pairing_complete')['state']=='complete'
    with f['factory']() as conn:
        assert conn.execute('SELECT active FROM saas_connect_channels WHERE tenant_id=%s AND connect_channel_id=%s',(A,OTHER)).fetchone()['active'] is True
        assert conn.execute('SELECT active FROM saas_connect_channels WHERE tenant_id=%s AND connect_channel_id=%s',(A,CHANNEL)).fetchone()['active'] is True
    assert len([r for r in f['calls'] if r[0]=='POST'])==2


def test_actor_tenant_and_fresh_revoke_are_fenced(flow):
    f=flow;call(f)
    # Same tenant, another otherwise-authorized actor cannot read the QR/intent.
    with f['factory']() as conn:
        conn.execute("UPDATE saas_tenant_workflow_policies SET value=jsonb_set(value,'{62}',%s::jsonb,TRUE) WHERE tenant_id=%s AND policy_key='members'",
                     (json.dumps({'access_scopes':{'integrations':{'update':'all'}}}),A))
    other={**f['profile'],'telegram_id':62}
    with pytest.raises(TenantLinearError,match='not_found'):
        pairing.pairing(other,{**f['payload'],'action':'pairing_status','expected_user_id':'62'},'x')
    before=len(f['calls'])
    with f['factory']() as conn:conn.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s AND telegram_id=61',(A,))
    with pytest.raises(TenantLinearError):call(f,'pairing_refresh')
    assert len(f['calls'])==before


def test_parallel_start_creates_only_one_provider_channel(flow):
    f=flow
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:call(f),range(2)))
    assert all(r['request_id']==REQUEST and r['channel_id']==CHANNEL for r in results)
    assert len([r for r in f['calls'] if r[0]=='POST'])==1
    with f['factory']() as conn:
        assert conn.execute('SELECT count(*) AS n FROM saas_connect_pairing_intents WHERE tenant_id=%s AND actor_id=61',(A,)).fetchone()['n']==1
