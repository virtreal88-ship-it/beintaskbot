"""Synthetic PostgreSQL onboarding; no live devices, credentials or messages."""
from unittest.mock import Mock
import pytest
from test_whatsapp_native_rollout import staged_source
from test_call_sources import managed
from test_browser_calls import gateway
from test_call_recordings import pilot
from test_tenant_support_inbox import database,db,A,B,CA,CB,PROFILE
from tenant_linear_policy import TenantLinearError
import tenant_whatsapp_integration as app
import tenant_whatsapp_native_rollout as rollout
import tenant_call_sources as sources
import tenant_connect_transport as transport


@pytest.fixture
def setup(staged_source,monkeypatch):
    db,account,legacy,remote=staged_source
    monkeypatch.setattr(app,'_connect',lambda:db)
    monkeypatch.setattr(app,'legacy_inventory',lambda _:[])
    monkeypatch.setattr(sources,'remote',remote)
    remote.return_value={'public_key':'a'*64,'qr':'YWJj'}
    for flag in ('INBOX','PROJECTION','SEND'):monkeypatch.setenv('BEINTASKBOT_UNIFIED_WHATSAPP_'+flag,'1')
    yield db,account,legacy,remote
    db.rollback();db.execute('DROP TABLE IF EXISTS saas_whatsapp_onboarding');db.commit()


def test_fresh_number_retries_same_source_gateway_and_activates_exact_account(setup):
    db,account,legacy,remote=setup
    phone='994100000777'
    first=app.begin(PROFILE,phone,'Synthetic')
    assert first['state']=='pairing' and first['activation_pending']
    source=first['channel_id']
    before=db.execute('SELECT gateway_id,encrypted_token FROM saas_browser_call_sources');db.commit()
    assert app.begin(PROFILE,phone,'Changed')['channel_id']==source
    assert db.execute('SELECT gateway_id,encrypted_token FROM saas_browser_call_sources')==before;db.commit()
    legacy.assert_not_called()
    remote.return_value={'connected':True,'account_jid':'994100000778@s.whatsapp.net'}
    with pytest.raises(TenantLinearError,match='native_pairing_unconfirmed'):app.activate(PROFILE,source)
    remote.return_value={'connected':True,'account_jid':phone+'@s.whatsapp.net'}
    assert app.activate(PROFILE,source)['activation_pending'] is False
    assert app.activate(PROFILE,source)['state']=='ready'
    assert db.execute('SELECT enabled,projection_enabled FROM saas_whatsapp_capture_generations')[0]=={'enabled':True,'projection_enabled':True};db.commit()


@pytest.mark.parametrize('local_active',[False,True])
def test_transfer_requires_disconnected_legacy_and_preserves_identity_history_permissions(setup,monkeypatch,local_active):
    db,account,legacy,remote=setup
    phone=account.split('@')[0]
    db.execute('UPDATE saas_connect_channels SET active=%s WHERE tenant_id=%s AND connect_channel_id=%s',(local_active,A,CA));db.commit()
    other=db.execute('SELECT * FROM saas_connect_channels WHERE tenant_id=%s',(B,));db.commit()
    grants=db.execute('SELECT * FROM saas_dialog_source_members ORDER BY tenant_id,source_id,employee_id');db.commit()
    old={'id':CA,'phone':phone,'status':'connected'}
    monkeypatch.setattr(app,'legacy_inventory',lambda _:[old])
    with pytest.raises(TenantLinearError,match='Сначала отключите'):app.begin(PROFILE,phone,'Synthetic')
    assert not db.execute('SELECT 1 FROM saas_whatsapp_onboarding');db.commit()
    old['status']='disconnected'
    history=db.execute('SELECT id,external_id FROM saas_crm_messages ORDER BY id');db.commit()
    legacy.side_effect=lambda key,method,path,*args: ({'channelId':CA,'status':'disabled'} if method=='POST' else {'id':CA,'phone':phone,'status':'disabled'})
    assert app.begin(PROFILE,phone,'Do not rename')['channel_id']==CA
    assert db.execute('SELECT fresh FROM saas_whatsapp_onboarding')[0]['fresh'] is False
    assert db.execute('SELECT id,external_id FROM saas_crm_messages ORDER BY id')==history;db.commit()
    assert db.execute('SELECT active,status FROM saas_connect_channels WHERE tenant_id=%s AND connect_channel_id=%s',(A,CA))[0]=={'active':False,'status':'disconnected'}
    assert db.execute('SELECT * FROM saas_connect_channels WHERE tenant_id=%s',(B,))==other
    assert db.execute('SELECT * FROM saas_dialog_source_members ORDER BY tenant_id,source_id,employee_id')==grants;db.commit()
    assert legacy.call_args_list[0].args[2].endswith('/native-transfer')


@pytest.mark.parametrize('local_active',[False,True])
def test_transfer_failure_never_creates_gateway_or_enables_sender(setup,monkeypatch,local_active):
    db,account,legacy,remote=setup
    db.execute('UPDATE saas_connect_channels SET active=%s WHERE tenant_id=%s AND connect_channel_id=%s',(local_active,A,CA));db.commit()
    before=db.execute('SELECT * FROM saas_connect_channels ORDER BY tenant_id,connect_channel_id');db.commit()
    monkeypatch.setattr(app,'legacy_inventory',lambda _:[{'id':CA,'phone':account.split('@')[0],'status':'disconnected'}])
    legacy.return_value={'status':'unknown'}
    with pytest.raises(TenantLinearError,match='не подтвердил остановку'):app.begin(PROFILE,account.split('@')[0],'')
    remote.assert_not_called()
    assert db.execute('SELECT * FROM saas_connect_channels ORDER BY tenant_id,connect_channel_id')==before;db.commit()
    assert not db.execute('SELECT 1 FROM saas_whatsapp_native_routes');db.commit()
    row=app.inventory(PROFILE)['channels'][0]
    assert row['id']==CA and row['state']=='preparing' and row['activation_pending']


@pytest.mark.parametrize('change',[{'status':'connected'},{'phone':'994100000999'},{'phoneConflict':True}])
def test_transfer_does_not_clear_local_selection_without_exact_disabled_account(setup,monkeypatch,change):
    db,account,legacy,remote=setup
    phone=account.split('@')[0]
    db.execute('UPDATE saas_connect_channels SET active=TRUE WHERE tenant_id=%s AND connect_channel_id=%s',(A,CA));db.commit()
    monkeypatch.setattr(app,'legacy_inventory',lambda _:[{'id':CA,'phone':phone,'status':'disconnected'}])
    legacy.side_effect=lambda key,method,path,*args: ({'channelId':CA,'status':'disabled'} if method=='POST' else {'id':CA,'phone':phone,'status':'disabled',**change})
    with pytest.raises(TenantLinearError,match='native_legacy_not_disabled'):app.begin(PROFILE,phone,'')
    assert db.execute('SELECT active FROM saas_connect_channels WHERE tenant_id=%s AND connect_channel_id=%s',(A,CA))[0]['active'] is True
    assert not db.execute('SELECT 1 FROM saas_whatsapp_native_routes');db.commit()
    remote.assert_not_called()


def test_new_source_can_send_without_connect_integration(setup):
    db,account,legacy,remote=setup
    phone='994100000777';source=app.begin(PROFILE,phone,'Synthetic')['channel_id']
    remote.return_value={'connected':True,'account_jid':phone+'@s.whatsapp.net'}
    app.activate(PROFILE,source)
    # Rebind an existing synthetic conversation to prove route resolution does
    # not require legacy credentials. No provider submit is made.
    db.execute('UPDATE saas_connect_conversations SET connect_channel_id=%s WHERE tenant_id=%s',(source,A))
    rows=db.execute('SELECT lead_id FROM saas_connect_conversations WHERE tenant_id=%s',(A,))
    db.execute("DELETE FROM saas_tenant_integrations WHERE tenant_id=%s AND provider='connect'",(A,));db.commit()
    assert rows
    with db.cursor() as cur:
        route=transport.connect_route(cur,A,rows[0]['lead_id'])
        assert route['native']['scope']['source']==source
        from tenant_operator_access import access
        from tenant_operator_dialogs import projection
        _,_,policy=access(cur,PROFILE,'dialogs')
        sql,scopes=projection(cur,policy)
        cur.execute(sql+' SELECT connected FROM visible WHERE provider=%s AND channel_id=%s',(A,A,*scopes,False,'connect',source))
        visible=cur.fetchall()
        assert visible and all(row['connected'] for row in visible)
    db.commit()


def test_cross_company_revocation_and_pending_limit_fail_closed(setup):
    db,account,legacy,remote=setup
    one=app.begin(PROFILE,'994100000771','Synthetic')
    with pytest.raises(TenantLinearError):app.pairing({'tenant_id':B,'telegram_id':11},one['channel_id'])
    app.begin(PROFILE,'994100000772','Synthetic');app.begin(PROFILE,'994100000773','Synthetic')
    with pytest.raises(TenantLinearError,match='Завершите ранее'):app.begin(PROFILE,'994100000774','Synthetic')
    db.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s',(A,));db.commit()
    with pytest.raises(TenantLinearError):app.pairing(PROFILE,one['channel_id'])
    db.execute('UPDATE saas_tenant_members SET active=TRUE WHERE tenant_id=%s',(A,));db.commit()


def test_reserve_connector_cannot_enable_same_native_number_under_another_id(setup):
    db,account,legacy,remote=setup
    app.begin(PROFILE,'994100000777','Synthetic')
    legacy.return_value={'data':[{'id':CA,'phone':'994100000777','status':'connected'}]}
    with pytest.raises(Exception,match='Второй отправитель'):
        transport._configure_locked({**PROFILE,'role':'owner'},{'api_key':'synthetic','channel_ids':[CA]},'https://unused.invalid')
    assert all(call.args[1]=='GET' for call in legacy.call_args_list)
