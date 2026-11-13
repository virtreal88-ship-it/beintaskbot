"""No provider traffic: independent shared connection scopes and lock admission."""
from unittest.mock import MagicMock, Mock, patch
import pytest
import tenant_shared_integration_access as access
from tenant_linear_policy import TenantLinearError

TENANT = 'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'
OTHER = 'bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb'
SESSION = {'tenant_id': TENANT, 'telegram_id': 2, 'role':'owner'}  # stale/forged role
RESOURCE = {'tenant_id':TENANT, 'responsible_member_id':1, 'responsible_department_id':'sales'}


def profile(scopes=None, **changes):
    return {'tenant_id':TENANT,'telegram_id':2,'role':'manager','active':True,
            'tenant_status':'active','department_id':'sales','company_owner_id':1,
            'workflow':{'policies':{'members':{'2':{'access_scopes':{'integrations':scopes or {'view':'none'}}}}}},
            **changes}


@pytest.fixture(autouse=True)
def gate(monkeypatch):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')


@pytest.mark.parametrize('provider',sorted(access.PROVIDERS))
@pytest.mark.parametrize('action',['view','create','update','delete'])
@pytest.mark.parametrize('scope,expected',[('none',False),('self',False),('department',True),('all',True)])
def test_owner_responsibility_matrix(provider,action,scope,expected,monkeypatch):
    if provider == 'beinsystems_operator':
        monkeypatch.setenv('BEINSYSTEMS_OPERATOR_TENANT_ID', TENANT)
    # Operator connection/link administration is intentionally owner-managed;
    # only its read surface follows delegated integration scopes.
    scoped_expected = expected and not (provider == 'beinsystems_operator' and action != 'view')
    assert access.allowed(profile({action:scope}),RESOURCE,provider,action) is scoped_expected
    if scope != 'all':
        assert not access.allowed(profile({action:scope},department_id='other'),RESOURCE,provider,action)


def test_cross_tenant_inactive_and_missing_owner_department():
    for changes in ({'active':False},{'tenant_status':'suspended'},{'tenant_id':OTHER}):
        assert not access.allowed(profile({'view':'all'},**changes),RESOURCE,'linear','view')
    assert not access.allowed(profile({'view':'department'}),{**RESOURCE,'responsible_member_id':None},'linear','view')
    assert not access.allowed(profile({'view':'department'}),{**RESOURCE,'responsible_department_id':None},'linear','view')


def setup_guard(current):
    factory=MagicMock(); conn=factory.return_value.__enter__.return_value
    cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchall.return_value=[{'telegram_id':1,'department_id':'sales','active':True}]
    cur.fetchone.return_value=None
    return factory,conn,cur,patch.object(access,'read_access_profile',return_value=current)


def context(**extra):
    return {'expected_tenant_id':TENANT,'expected_user_id':'2',**extra}


def test_current_database_role_and_scopes_not_session_role():
    factory,conn,cur,read=setup_guard(profile({'view':'none'}))
    with read,pytest.raises(TenantLinearError):
        with access.guard(factory,SESSION,'linear','view'):pytest.fail('stale owner bypass')
    assert conn.commit.call_count==0


def test_independent_actions_and_fresh_revoke():
    factory,conn,cur,read=setup_guard(profile({'view':'all','delete':'none'}))
    with read as current:
        with access.guard(factory,SESSION,'linear','view'):
            assert access.admitted(SESSION,'linear')
        with pytest.raises(TenantLinearError):
            with access.guard(factory,SESSION,'linear','delete',data=context(confirm_company_impact=True)):pass
        current.return_value=None
        with pytest.raises(TenantLinearError):
            with access.guard(factory,SESSION,'linear','view'):pass
    assert not access.admitted(SESSION,'linear')


@pytest.mark.parametrize('provider,action',[('linear','delete'),('connect','view'),('kommo','update')])
def test_nested_guard_cannot_escalate_or_open_second_transaction(provider,action):
    factory,conn,cur,read=setup_guard(profile({'view':'all','delete':'all'}))
    with read,access.guard(factory,SESSION,'linear','view'):
        with pytest.raises(TenantLinearError):
            with access.guard(factory,SESSION,provider,action,data=context(confirm_company_impact=True)):pass
        with access.guard(factory,SESSION,'linear','view'):pass
    assert factory.call_count==1


@pytest.mark.parametrize('action,data',[('delete',context()),('update',context(api_key='synthetic')),('connect',context(api_key='synthetic'))])
def test_company_impact_required_before_body(action,data):
    factory,conn,cur,read=setup_guard(profile({'create':'all','update':'all','delete':'all'}))
    cur.fetchone.return_value={'exists':True}
    with read,pytest.raises(TenantLinearError,match='integration_company_impact_confirmation_required'):
        with access.guard(factory,SESSION,'linear',action,data=data):pytest.fail('body ran')


@pytest.mark.parametrize('data',[{}, {'expected_tenant_id':OTHER,'expected_user_id':'2'}, {'expected_tenant_id':TENANT,'expected_user_id':'1'}])
def test_mutations_require_current_context(data):
    factory,conn,cur,read=setup_guard(profile({'create':'all'}))
    with read,pytest.raises(TenantLinearError,match='integration_context_changed'):
        with access.guard(factory,SESSION,'linear','connect',data=data):pass


def test_connect_classification_is_current_database_not_payload():
    factory,conn,cur,read=setup_guard(profile({'create':'all','update':'none'}))
    with read:
        with access.guard(factory,SESSION,'linear','connect',data=context()):pass
        cur.fetchone.return_value={'exists':True}
        with pytest.raises(TenantLinearError,match='integration_action_denied'):
            with access.guard(factory,SESSION,'linear','connect',data=context(confirm_company_impact=True)):pass


def test_gate_off_no_database_or_context_change(monkeypatch):
    monkeypatch.delenv('TENANT_SCOPED_ACCESS_ENABLED')
    factory=Mock(side_effect=AssertionError('legacy database changed'))
    with access.guard(factory,None,'unknown','invalid'):pass
    factory.assert_not_called()


def test_status_projection_never_exposes_metadata_or_key():
    factory,conn,cur,read=setup_guard(profile({'view':'none'}))
    rows=access.project_status(cur,profile({'view':'none'}),[{'provider':'linear','status':'connected','secrets':'SENTINEL','metadata':{'token':'SENTINEL'},'account_domain':'SENTINEL'}])
    assert len(rows)==5
    assert 'SENTINEL' not in str(rows)
    assert all(row['status_only'] and not row['management_actions']['view'] for row in rows)


def test_ambiguous_or_inactive_owner_cannot_grant_department():
    factory,conn,cur,read=setup_guard(profile({'view':'department'}))
    for rows in ([],[{'telegram_id':1,'department_id':'sales','active':False}],
                 [{'telegram_id':1,'department_id':'sales','active':True},{'telegram_id':3,'department_id':'sales','active':True}]):
        cur.fetchall.return_value=rows
        assert not access.allowed(profile({'view':'department'}),access.owner_resource(cur,profile()),'linear','view')


def test_delete_scope_cannot_smuggle_chat_settings_update(monkeypatch):
    import tenant_chat_ai_settings as chat
    connection=Mock(side_effect=AssertionError('must reject before storage'))
    monkeypatch.setattr(chat,'_connect',connection)
    with pytest.raises(TenantLinearError,match='integration_key_only_request_invalid'):
        chat.save.__wrapped__(SESSION,context(remove_key=True,enabled=True,consent=True,model='changed'))
    connection.assert_not_called()


def test_key_only_chat_removal_preserves_unknown_settings(monkeypatch):
    import tenant_chat_ai_settings as chat
    factory=MagicMock();cur=factory.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    metadata={'enabled':True,'consent':True,'model':'existing','photo_model':'',
              'video_model':'','video_enabled':False,'copilot_model':'existing',
              'audio_enabled':True,'daily_limit':123,'custom_future_field':'keep'}
    cur.fetchone.side_effect=[{'secrets':b'synthetic','metadata':metadata,'updated_at':'v1'},{}]
    monkeypatch.setattr(chat,'_connect',factory)
    monkeypatch.setattr(chat,'ensure_schema',Mock())
    monkeypatch.setattr(chat,'authorize',Mock(return_value=(TENANT,2)))
    monkeypatch.setattr(chat,'public',lambda row,tenant:row)
    chat.save.__wrapped__(SESSION,context(remove_key=True,preserve_settings=True,expected_updated_at='v1'))
    calls=[call for call in cur.execute.call_args_list if 'INSERT INTO saas_tenant_integrations' in call.args[0]]
    import json
    params=calls[0].args[1]
    result=json.loads(params[2]);assert params[1] is None
    assert result['custom_future_field']=='keep' and result['daily_limit']==123
    assert result['model']=='existing' and result['enabled'] is False


def test_gate_off_decorator_preserves_legacy_none_profile(monkeypatch):
    monkeypatch.delenv('TENANT_SCOPED_ACCESS_ENABLED')
    @access.operation('linear','view')
    def sample(profile):return profile
    assert sample(None) is None


def test_bein_create_cannot_change_primary_without_update(monkeypatch):
    import tenant_bein_integration as bein
    factory,conn,cur,read=setup_guard(profile({'create':'all','update':'none'}))
    existing={'kommo':{'status':'connected','metadata':{'primary':True}}}
    def query(sql,params=None):
        if 'provider=ANY' in sql:
            cur.fetchone.return_value=next((existing[p] for p in params[1] if p in existing),None)
    cur.execute.side_effect=query
    monkeypatch.setattr(bein,'_connect',factory)
    monkeypatch.setattr(bein,'_ensure_schema',Mock())
    crypto=Mock(side_effect=AssertionError('crypto before U authorization'))
    provider=Mock(side_effect=AssertionError('provider before U authorization'))
    monkeypatch.setattr(bein,'_fernet',crypto);monkeypatch.setattr(bein,'BeinCrm',provider)
    with read,pytest.raises(TenantLinearError,match='integration_action_denied'):
        bein.save(SESSION,token='bei_int_synthetic_token_never_sent',primary=True,authorization=context())
    crypto.assert_not_called();provider.assert_not_called()
    assert existing['kommo']['metadata']['primary'] is True
    assert not any(call.args[0].lstrip().startswith(('INSERT','UPDATE','DELETE')) for call in cur.execute.call_args_list)


def test_bein_primary_requires_both_create_and_update():
    for scopes,denied in [({'create':'all','update':'all'},False),({'create':'none','update':'all'},True),({'create':'all','update':'none'},True)]:
        factory,conn,cur,read=setup_guard(profile(scopes))
        with read:
            if denied:
                with pytest.raises(TenantLinearError):
                    with access.guard(factory,SESSION,'beinsystems','connect',data=context(),required_actions=('update',)):pass
            else:
                with access.guard(factory,SESSION,'beinsystems','connect',data=context(),required_actions=('update',)):pass


def test_connect_retained_channels_require_update_not_create():
    factory,conn,cur,read=setup_guard(profile({'create':'all','update':'none'}))
    cur.fetchone.side_effect=[None,{'channel':True}]
    with read,pytest.raises(TenantLinearError,match='integration_action_denied'):
        with access.guard(factory,SESSION,'connect','connect',data=context(confirm_company_impact=True)):pass


@pytest.mark.parametrize('role',['manager','operator'])
def test_connect_legacy_status_preserved_without_view_cell(monkeypatch,role):
    import tenant_connect_transport as transport
    factory,conn,cur,read=setup_guard(profile(role=role,workflow={}))
    monkeypatch.setattr(transport,'_connect',factory);monkeypatch.setattr(transport,'_schema',Mock())
    cur.fetchall.return_value=[];cur.fetchone.return_value=None
    actual={'tenant_id':TENANT,'telegram_id':2,'role':role}
    with read:
        enabled=transport.status(actual)
        monkeypatch.delenv('TENANT_SCOPED_ACCESS_ENABLED')
        disabled=transport.status(actual)
    assert enabled==disabled=={'connected':False,'channels':[],'updated_at':None,'metadata':{}}


def test_connect_legacy_status_exception_does_not_grant_management_or_explicit_view():
    for scopes,legacy_status in [(None,False),({'view':'none'},True),({'view':'self'},True)]:
        current=profile(scopes) if scopes else profile(workflow={})
        factory,conn,cur,read=setup_guard(current)
        with read,pytest.raises(TenantLinearError):
            with access.guard(factory,SESSION,'connect','view',legacy_member_read=legacy_status):pass
    factory,conn,cur,read=setup_guard(None)
    with read,pytest.raises(TenantLinearError):
        with access.guard(factory,SESSION,'connect','view',legacy_member_read=True):pass


@pytest.mark.parametrize('provider,action', [('connect','delete'),('connect','update'),('linear','delete'),('chat_ai','update'),('chat_ai','delete')])
def test_mutation_wrappers_do_not_leak_previous_settings(monkeypatch,provider,action):
    factory,conn,cur,read=setup_guard(profile({action:'all','view':'none'}))
    monkeypatch.setitem(globals(),'_connect',factory)
    @access.operation(provider,action,data_arg='data')
    def mutation(profile,data):
        return {'status':'connected','updated_at':'v2','channels':[{'name':'PRIVATE_SENTINEL'}],
                'metadata':{'secret':'PRIVATE_SENTINEL'},'settings':{'members':'PRIVATE_SENTINEL'},
                'media_hosts':['PRIVATE_SENTINEL'],'copilot_model':'PRIVATE_SENTINEL'}
    with read:result=mutation(SESSION,context(confirm_company_impact=True))
    assert result=={'provider':provider,'status':'connected','updated_at':'v2'}
    assert 'PRIVATE_SENTINEL' not in str(result)


def test_connect_reconnect_normalizes_nested_action_without_second_fence(monkeypatch):
    import tenant_connect_transport as transport
    factory,conn,cur,read=setup_guard(profile({'update':'all','view':'none','create':'none'}))
    cur.fetchone.return_value={'exists':True}
    monkeypatch.setattr(transport,'_connect',factory);monkeypatch.setattr(transport,'_schema',Mock())
    monkeypatch.setattr(transport,'_stored_connect_credentials',lambda profile:{'api_key':'synthetic'})
    configure=Mock(return_value={'metadata':{'old':'PRIVATE_SENTINEL'}})
    monkeypatch.setattr(transport,'_configure_locked',configure)
    from contextlib import nullcontext
    monkeypatch.setattr(transport,'_tenant_connect_lock',lambda tenant:nullcontext())
    remote=Mock(return_value={'status':'connected','phone':'PRIVATE_SENTINEL'})
    monkeypatch.setattr(transport,'_request',remote)
    with read:
        result=transport.channel_status(SESSION,'11111111-1111-4111-8111-111111111111',reconnect=True,
            callback_url='https://synthetic.invalid/hook',authorization=context(confirm_company_impact=True))
    configure.assert_called_once();remote.assert_called_once()
    assert result=={'provider':'connect','status':'connected'}
    # One initializer and one authorization fence; nested configure does not
    # reacquire the shared advisory lock in another transaction.
    assert sum('shared-integration:' in str(call.args) for call in cur.execute.call_args_list)==1


@pytest.mark.parametrize('scopes,confirmed',[({'create':'all','update':'none'},True),({'update':'all'},False)])
def test_connect_reconnect_denied_before_remote_mutation(monkeypatch,scopes,confirmed):
    import tenant_connect_transport as transport
    factory,conn,cur,read=setup_guard(profile(scopes));cur.fetchone.return_value={'exists':True}
    monkeypatch.setattr(transport,'_connect',factory);monkeypatch.setattr(transport,'_schema',Mock())
    monkeypatch.setattr(transport,'_stored_connect_credentials',lambda profile:{'api_key':'synthetic'})
    configure=Mock(side_effect=AssertionError('configure unauthorized'))
    monkeypatch.setattr(transport,'_configure_locked',configure)
    remote=Mock(side_effect=AssertionError('remote unauthorized'));monkeypatch.setattr(transport,'_request',remote)
    with read,pytest.raises(TenantLinearError):
        transport.channel_status(SESSION,'11111111-1111-4111-8111-111111111111',reconnect=True,
            authorization=context(confirm_company_impact=confirmed))
    configure.assert_not_called();remote.assert_not_called()
