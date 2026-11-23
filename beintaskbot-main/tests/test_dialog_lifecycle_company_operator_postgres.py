"""Real local PostgreSQL ownership/rollback tests; all Operator transport synthetic."""
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import psycopg
import pytest
from cryptography.fernet import Fernet
from psycopg.rows import dict_row

import tenant_beinsystems_operator as provider
import tenant_crm_native_companies as native
import tenant_dialog_company_binding as binding
import tenant_dialog_company_operator as service
from tenant_crm_native_access import CRMAccessError
from tenant_operator_company_name_guard import lock_name_writes
from test_connect_channel_multiprocess import database
from test_dialog_lifecycle_company_completion import STAFF, bindings, companies
from test_dialog_lifecycle_pglite import command, episodes
from test_tenant_support_inbox import A, B, CA, CB, PROFILE, db

OWNER={**PROFILE,'role':'owner','active':True}
ROUTE={'provider':'connect','lead_id':41,'channel_id':CA,'conversation_id':'client'}
FLAG='BEINSYSTEMS_DIALOG_OPERATOR_CHOOSER_ENABLED'


@pytest.fixture
def ready(companies,monkeypatch):
    database,*_=companies
    monkeypatch.setattr(binding,'_connect',lambda:database)
    monkeypatch.setattr(provider,'_connect',lambda:database)
    monkeypatch.setattr(provider,'_ensure_schema',lambda *_:None)
    cipher=Fernet(Fernet.generate_key())
    monkeypatch.setattr(provider,'_fernet',lambda:cipher)
    fake_token='synthetic-only'
    envelope=cipher.encrypt(json.dumps({'tenant_id':A,'owner_telegram_id':11,
       'provider':provider.PROVIDER,'purpose':'pending_auth_verification',
       'auth_contract':next(iter(provider.AUTH_SUPPORTED_CONTRACT_VERSIONS)),
       'token':fake_token}).encode())
    received=database.execute("""INSERT INTO saas_beinsystems_operator_pending_auth_v2
      (tenant_id,owner_telegram_id,token_ciphertext) VALUES(%s::uuid,11,%s)
      RETURNING received_at""",(A,envelope))[0]['received_at']
    instance=str(uuid.uuid4())
    database.execute("""INSERT INTO saas_beinsystems_operator_accounts_capabilities
      (tenant_id,owner_telegram_id,candidate_received_at,operator_identifier_fingerprint,
       connection_generation,connection_instance_id,expires_at)
      VALUES(%s::uuid,11,%s,%s,'test-generation',%s::uuid,now()+interval '5 min')""",
      (A,received,service.imports._identifier_fingerprint('synthetic-owner'),instance))
    database.commit()
    command()
    monkeypatch.setenv(FLAG,'true')
    calls=[]
    def fake_request(endpoint,body,**credentials):
        assert credentials['token']==fake_token and credentials['base_url']==provider.OPERATOR_API_BASE_URL
        calls.append((endpoint,body))
        if endpoint=='me':
            assert body=={} and credentials['requests_me'] is True
            return {'status':'success','data':{'identifier':'synthetic-owner','role':'synthetic'}}
        assert endpoint=='accounts'
        return {'status':'success','data':[{'account':body['search'],'name':'private synthetic'}]}
    monkeypatch.setattr(provider,'_request_operator',fake_request)
    return database,calls,instance


def count(db,table):
    result=db.execute(f'SELECT count(*) AS n FROM {table}')[0]['n']
    db.commit()
    return result


def choose(term,**route):
    return service.chooser(OWNER,**{**ROUTE,**route},term=term,choice=True)['binding']


def test_owner_search_returns_only_account_and_never_creates(ready,monkeypatch):
    db,calls,_=ready
    actor={**STAFF,'role':'operator','active':True}
    for profile,route in ((actor,ROUTE),(OWNER,{**ROUTE,'lead_id':666}),(OWNER,{**ROUTE,'channel_id':CB})):
        with pytest.raises(provider.OperatorError) as denied:
            service.chooser(profile,**route,term='safe-id')
        assert denied.value.status in {403,404}
    assert calls==[]
    assert binding.binding(OWNER,**ROUTE)['can_search_operator'] is True
    assert service.chooser(OWNER,**ROUTE,term=' safe-id ') == {'accounts':[{'account':'safe-id'}]}
    assert calls==[('me',{}),('accounts',{'search':'safe-id','limit':5})]
    assert not bindings(db)
    assert count(db,'saas_beinsystems_operator_company_imports')==0
    monkeypatch.delenv(FLAG)
    assert 'can_search_operator' not in binding.binding(OWNER,**ROUTE)
    with pytest.raises(provider.OperatorError) as disabled:
        service.chooser(OWNER,**ROUTE,term='safe-id')
    assert disabled.value.status==404 and len(calls)==2


def test_exact_selection_creates_once_then_reuses_and_refuses_replacement(ready):
    db,calls,_=ready
    one=choose('exact-one')
    assert one['company_name']=='exact-one' and one['can_search_operator'] is True
    assert choose('exact-one')==one
    assert calls==[('me',{}),('accounts',{'search':'exact-one','limit':1})]*2
    with pytest.raises(provider.OperatorError) as refuses:
        choose('exact-other')
    assert refuses.value.status==409
    assert [row['company_id'] for row in bindings(db)]==[one['company_id']]
    assert count(db,'saas_beinsystems_operator_company_imports')==1
    assert db.execute("SELECT count(*) AS n FROM saas_crm_native_companies WHERE name IN ('exact-one','exact-other')")==[{'n':1}]
    assert db.execute("SELECT count(*) AS n FROM saas_tenant_audit_events WHERE action='operator_dialog_company_selected'")==[{'n':1}]
    db.commit()


def test_inline_company_replacement_rejects_a_stale_link(ready):
    db,_,_=ready
    original=choose('inline-original',expected_company_id=None)
    changed=choose('inline-next',expected_company_id=original['company_id'])
    assert changed['company_name']=='inline-next'
    with pytest.raises(provider.OperatorError) as stale:
        choose('stale-inline',expected_company_id=original['company_id'])
    assert stale.value.status==409
    with pytest.raises(provider.OperatorError) as stale_unlink:
        binding.binding(OWNER,**ROUTE,company_id=None,expected_company_id=original['company_id'])
    assert stale_unlink.value.status==409
    assert [row['company_id'] for row in bindings(db)]==[changed['company_id']]
    assert binding.binding(OWNER,**ROUTE,company_id=None,expected_company_id=changed['company_id'])['company_id'] is None
    assert count(db,'saas_beinsystems_operator_company_imports')==2


def test_same_name_has_no_identity_evidence(ready):
    db,_,_=ready
    db.execute("INSERT INTO saas_crm_native_companies (id,tenant_id,name) VALUES(%s::uuid,%s::uuid,'Same Name')",(str(uuid.uuid4()),A))
    db.commit()
    with pytest.raises(provider.OperatorError) as duplicate:
        choose('same name')
    assert duplicate.value.status==409
    assert not bindings(db)
    assert count(db,'saas_beinsystems_operator_company_imports')==0


def test_provider_result_and_current_pending_credential_are_rechecked(ready,monkeypatch):
    db,_,_=ready
    original=provider._request_operator
    def wrong_account(endpoint,body,**credentials):
        if endpoint=='me':
            return original(endpoint,body,**credentials)
        return {'status':'success','data':[{'account':'different'}]}
    monkeypatch.setattr(provider,'_request_operator',wrong_account)
    with pytest.raises(provider.OperatorError) as wrong:
        choose('correct')
    assert wrong.value.status==409
    def changed(endpoint,body,**credentials):
        if endpoint=='me':
            return original(endpoint,body,**credentials)
        db.execute("UPDATE saas_beinsystems_operator_accounts_capabilities SET expires_at=now() WHERE tenant_id=%s::uuid",(A,))
        db.commit()
        return {'status':'success','data':[{'account':body['search']}]}
    monkeypatch.setattr(provider,'_request_operator',changed)
    with pytest.raises(provider.OperatorError) as changed_auth:
        choose('correct')
    assert changed_auth.value.status==409
    assert not bindings(db)
    assert count(db,'saas_beinsystems_operator_company_imports')==0


def test_failed_binding_rolls_back_local_company_and_mapping(ready,monkeypatch):
    db,_,_=ready
    def fail(*_args,**_kwargs):
        raise provider.OperatorError('synthetic failed binding',409)
    monkeypatch.setattr(service,'_bind_once',fail)
    with pytest.raises(provider.OperatorError):
        choose('rolled-back')
    assert db.execute("SELECT count(*) AS n FROM saas_crm_native_companies WHERE name='rolled-back'")==[{'n':0}]
    db.commit()
    assert not bindings(db)
    assert count(db,'saas_beinsystems_operator_company_imports')==0


def test_native_writers_share_guard_and_reject_only_protected_target_names(ready,monkeypatch):
    db,_,_=ready
    dsn=db.dsn
    monkeypatch.setattr(native,'_connect',lambda:psycopg.connect(dsn,row_factory=dict_row))
    monkeypatch.setattr(native,'_ensure_schema',lambda *_:None)
    with psycopg.connect(dsn,row_factory=dict_row) as first:
        assert lock_name_writes(first.cursor(),A)
        with pytest.raises(CRMAccessError) as busy:
            native.create_company(A,'protected-name',actor_id=11)
        assert busy.value.code=='company_name_busy'
        # An unrelated tenant does not take the lock or reserve names.
        native.create_company(B,'protected-name',actor_id=22)
    ident=choose('protected-name')['company_id']
    for later in (lambda:native.create_company(A,'PROTECTED-NAME',actor_id=11),
                  lambda:native.update_company(A,native.create_company(A,'Ordinary',actor_id=11)['id'],
                                               'PROTECTED-NAME',actor_id=11)):
        with pytest.raises(CRMAccessError) as conflict:
            later()
        assert conflict.value.code=='company_name_conflict'
    assert native.find_or_create_company(A,'protected-name')['id']==uuid.UUID(ident)
    assert native.update_company(A,ident,'New label',actor_id=11)['name']=='New label'
    with pytest.raises(CRMAccessError) as reserved:
        native.find_or_create_company(A,'protected-name')
    assert reserved.value.code=='company_name_conflict'
    assert native.create_company(A,'Unrelated',actor_id=11)['name']=='Unrelated'



def test_disabled_chooser_leaves_existing_ordinary_name_rules_unchanged(ready,monkeypatch):
    db,_,_=ready
    dsn=db.dsn
    monkeypatch.setattr(native,'_connect',lambda:psycopg.connect(dsn,row_factory=dict_row))
    monkeypatch.setattr(native,'_ensure_schema',lambda *_:None)
    chosen=choose('old-compatible')
    monkeypatch.delenv(FLAG)
    with psycopg.connect(dsn,row_factory=dict_row) as held:
        held.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",(A+':operator-company-import',))
        other=native.create_company(A,'old-compatible',actor_id=11)
    assert str(other['id'])!=chosen['company_id']


def test_pending_me_identity_must_match_before_requesting_accounts(ready,monkeypatch):
    db,_,_=ready
    requests=[]
    def wrong_identity(endpoint,body,**_credentials):
        requests.append(endpoint)
        return {'status':'success','data':{'identifier':'other-synthetic','role':'synthetic'}}
    monkeypatch.setattr(provider,'_request_operator',wrong_identity)
    with pytest.raises(provider.OperatorError) as denied:
        choose('chosen-account')
    assert denied.value.status==409 and requests==['me']
    assert not bindings(db)


def test_exact_dialogue_is_rechecked_after_live_accounts(ready,monkeypatch):
    db,_,_=ready
    original=provider._request_operator
    def route_changes(endpoint,body,**credentials):
        if endpoint=='accounts':
            assert command('transfer',1,target_employee_id=33)['lifecycle']['operator_id']==33
        return original(endpoint,body,**credentials)
    monkeypatch.setattr(provider,'_request_operator',route_changes)
    with pytest.raises(provider.OperatorError) as stale:
        choose('moved-account')
    assert stale.value.status in {404,409}
    assert not bindings(db) and count(db,'saas_beinsystems_operator_company_imports')==0


def _activate_synthetic(db,instance):
    secret=provider._fernet().encrypt(json.dumps({'tenant_id':A,'provider':provider.PROVIDER,
      'connection_instance_id':instance,'token':'synthetic-only'}).encode())
    db.execute("""INSERT INTO saas_tenant_integrations (tenant_id,provider,status,metadata,secrets)
        VALUES(%s::uuid,%s,'connected',%s::jsonb,%s)""",
       (A,provider.PROVIDER,json.dumps({'connection_instance_id':instance,'generation':'test-generation'}),secret))
    db.commit()


def test_confirm_bound_company_after_live_operator_selection(ready):
    db,_,instance=ready
    _activate_synthetic(db,instance)
    company=str(uuid.uuid4())
    db.execute("INSERT INTO saas_crm_native_companies (id,tenant_id,name) VALUES(%s::uuid,%s::uuid,'example')",(company,A))
    db.commit()
    binding.binding(OWNER,**ROUTE,company_id=company,expected_company_id=None)
    selection=dict(term='example',choice=True,expected_company_id=company)
    assert service.chooser(OWNER,**ROUTE,**selection)=={'confirm_existing_company':True}
    assert count(db,'saas_beinsystems_operator_company_imports')==0
    for _ in range(2):
        result=service.chooser(OWNER,**ROUTE,**selection,confirm_existing=True)
        assert result['binding']['company_id']==company
    assert count(db,'saas_beinsystems_operator_company_imports')==1


def test_accepted_api_operator_can_select_and_detach_without_company_crud(ready):
    db,_,instance=ready
    _activate_synthetic(db,instance)
    db.execute("""UPDATE saas_tenant_members SET permissions='["dialogs","support","operator_api"]'::jsonb
        WHERE tenant_id=%s::uuid AND telegram_id=33""",(A,))
    db.commit()
    command('transfer',1,target_employee_id=33)
    result=service.chooser(STAFF,**ROUTE,term='example',choice=True,expected_company_id=None)
    selected=result['binding']
    assert selected['company_name']=='example' and selected['can_unlink']
    detached=binding.binding(STAFF,**ROUTE,company_id=None,expected_company_id=selected['company_id'])
    assert detached['company_id'] is None
    assert count(db,'saas_beinsystems_operator_company_imports')==1
    with pytest.raises(provider.OperatorError):
        binding.binding(STAFF,**{**ROUTE,'channel_id':CB},company_id=None,expected_company_id=None)


def test_owner_reads_bound_company_after_transfer_without_taking_dialogue(ready):
    from tenant_dialog_company_public_link import _snapshot
    db,_,instance=ready
    _activate_synthetic(db,instance)
    company=choose('example',expected_company_id=None)['company_id']
    command('transfer',1,target_employee_id=33)
    visible=binding.binding(OWNER,**ROUTE)
    assert visible['company_id']==company and visible['can_manage'] is False
    route=binding._key(ROUTE['provider'],ROUTE['lead_id'],ROUTE['channel_id'],ROUTE['conversation_id'])
    assert _snapshot(OWNER,route,company,owner_read=True)[1]=='example'
    assert db.execute("SELECT operator_id FROM saas_dialog_workflows WHERE tenant_id=%s AND channel_id=%s AND conversation_id=%s",
                      (A,CA,ROUTE['conversation_id']))[0]['operator_id']==33
    db.commit()


def test_employee_reads_active_connection_for_owned_route_only(ready):
    db,calls,instance=ready
    _activate_synthetic(db,instance)
    db.execute("""UPDATE saas_tenant_members SET permissions='["dialogs","support","operator_data"]'::jsonb
        WHERE tenant_id=%s::uuid AND telegram_id=33""",(A,))
    db.execute("""INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value)
        VALUES(%s::uuid,'modules','{"operator_data":true}'::jsonb) ON CONFLICT(tenant_id,policy_key)
        DO UPDATE SET value=EXCLUDED.value""",(A,))
    db.commit()
    command('transfer',1,target_employee_id=33)
    assert service.chooser(STAFF,**ROUTE,term='lo')=={'accounts':[{'account':'lo'}]}
    assert calls==[('accounts',{'search':'lo','limit':5})]
    with pytest.raises(provider.OperatorError):
        service.chooser(STAFF,**{**ROUTE,'channel_id':CB},term='lo')
    assert len(calls)==1
    db.execute("""UPDATE saas_tenant_members SET permissions='["dialogs","support"]'::jsonb
        WHERE tenant_id=%s::uuid AND telegram_id=33""",(A,))
    db.commit()
    with pytest.raises(provider.OperatorError):
        service.chooser(STAFF,**ROUTE,term='lo')
    assert len(calls)==1


@pytest.mark.parametrize('first',['manual','chooser','two-choosers'])
def test_manual_identity_and_live_chooser_share_two_connection_winner_order(ready,monkeypatch,first):
    db,_,instance=ready
    _activate_synthetic(db,instance)
    target=db.execute("SELECT id::text AS id FROM saas_crm_native_companies WHERE tenant_id=%s::uuid ORDER BY id LIMIT 1",(A,))[0]['id']
    db.commit()
    dsn=db.dsn
    def connect():
        return psycopg.connect(dsn,row_factory=dict_row)
    monkeypatch.setattr(provider,'_connect',connect)
    monkeypatch.setattr(binding,'_connect',connect)
    started=Event()
    commit_first=Event()
    next_entered=Event()
    account='race-identity'
    import tenant_operator_company_name_guard as guard
    if first=='chooser':
        manual_lock=guard.lock_name_writes
        def observe_manual_acquisition(*args,**kwargs):
            next_entered.set()
            return manual_lock(*args,**kwargs)
        monkeypatch.setattr(guard,'lock_name_writes',observe_manual_acquisition)
    elif first=='two-choosers':
        authorize_second=service._authorized
        def observe_second_route(*args):
            if started.is_set():
                next_entered.set()
            return authorize_second(*args)
        monkeypatch.setattr(service,'_authorized',observe_second_route)
    else:
        source_for_second=service.imports._selected_source_credentials
        def observe_second_preflight(profile):
            if started.is_set():
                next_entered.set()
            return source_for_second(profile)
        monkeypatch.setattr(service.imports,'_selected_source_credentials',observe_second_preflight)
    if first!='manual':
        original=service._bind_once
        def hold_before_binding(*args):
            started.set()
            assert commit_first.wait(15), 'test did not release first transaction'
            return original(*args)
        monkeypatch.setattr(service,'_bind_once',hold_before_binding)
    else:
        original=guard.require_unreserved_account_pair
        def hold_before_confirmation(*args):
            original(*args)
            started.set()
            assert commit_first.wait(15), 'test did not release first transaction'
        monkeypatch.setattr(guard,'require_unreserved_account_pair',hold_before_confirmation)
    def manual():
        return provider.confirm_account_link(OWNER,local_entity_type='company',local_entity_id=target,
          external_account=account,evidence_summary='Synthetic reviewed evidence only')
    def chooser():
        return choose(account)
    wins, follows=(manual,chooser) if first=='manual' else (chooser,manual)
    if first=='two-choosers':
        follows=lambda:choose('competing-identity')
    with ThreadPoolExecutor(max_workers=2) as pool:
        ahead=pool.submit(wins)
        try:
            assert started.wait(15), 'first transaction did not reach reserved mutation'
            behind=pool.submit(follows)
            assert next_entered.wait(10)
        finally:
            commit_first.set()
        result=ahead.result(20)
        if first=='manual':
            assert result['status']=='confirmed'
            assert behind.result(20)['company_id']==target
            assert count(db,'saas_beinsystems_operator_company_imports')==0
            monkeypatch.setattr(native,'_connect',connect)
            monkeypatch.setattr(native,'_ensure_schema',lambda *_:None)
            with pytest.raises(CRMAccessError) as conflict:
                native.create_company(A,account.upper(),actor_id=11)
            assert conflict.value.code=='company_name_conflict'
        else:
            assert result['company_id']!=target
            with pytest.raises(provider.OperatorError) as conflict:
                behind.result(20)
            assert conflict.value.status==409
            assert count(db,'saas_beinsystems_operator_company_imports')==1
    bound=bindings(db)
    assert len(bound)==1 and bound[0]['company_id']==(target if first=='manual' else result['company_id'])



@pytest.mark.parametrize('writer',['create','rename','ai'])
@pytest.mark.parametrize('first',['native','chooser'])
def test_real_ordinary_writer_and_chooser_winner_before_commit(ready,monkeypatch,writer,first):
    db,_,_=ready
    dsn=db.dsn
    def connect():
        return psycopg.connect(dsn,row_factory=dict_row)
    monkeypatch.setattr(provider,'_connect',connect)
    monkeypatch.setattr(binding,'_connect',connect)
    monkeypatch.setattr(native,'_connect',connect)
    monkeypatch.setattr(native,'_ensure_schema',lambda *_:None)
    paused,release=Event(),Event()
    name='one-safe-name'
    renamed=native.create_company(A,'prior-unrelated-name',actor_id=11)['id'] if writer=='rename' else None
    def native_mutation():
        if writer=='create':
            return native.create_company(A,name,actor_id=11)
        if writer=='rename':
            return native.update_company(A,renamed,name,actor_id=11)
        return native.find_or_create_company(A,name)
    if first=='native':
        class PauseCommit:
            """Hold a real native mutation transaction after INSERT/UPDATE."""
            def __init__(self):
                self.connection=connect()
            def __enter__(self):
                self.connection.__enter__()
                return self
            def __exit__(self,*args):
                return self.connection.__exit__(*args)
            def cursor(self):
                return self.connection.cursor()
            def commit(self):
                with self.cursor() as cur:
                    cur.execute("SELECT id FROM saas_crm_native_companies WHERE tenant_id=%s::uuid AND name=%s",(A,name))
                    assert cur.fetchone() is not None
                paused.set()
                assert release.wait(20)
                self.connection.commit()
        monkeypatch.setattr(native,'_connect',PauseCommit)
    else:
        bind=service._bind_once
        def pause_bind(*args):
            bind(*args)
            paused.set()
            assert release.wait(20)
        monkeypatch.setattr(service,'_bind_once',pause_bind)
    with ThreadPoolExecutor(max_workers=2) as pool:
        winner=pool.submit(native_mutation if first=='native' else lambda:choose(name))
        try:
            assert paused.wait(15), 'expected real first transaction has not changed the local row'
            with connect() as observation:
                assert observation.execute("SELECT count(*) AS n FROM saas_crm_native_companies WHERE tenant_id=%s::uuid AND name=%s", (A,name)).fetchone()['n']==0
            loser=pool.submit((lambda:choose(name)) if first=='native' else native_mutation)
            if first=='native':
                with pytest.raises(provider.OperatorError) as busy:
                    loser.result(15)
                assert busy.value.status==409 and 'сейчас обновляются' in str(busy.value)
            else:
                with pytest.raises(CRMAccessError) as busy:
                    loser.result(15)
                assert busy.value.code=='company_name_busy'
        finally:
            release.set()
        row=winner.result(20)
    if first=='native':
        with pytest.raises(provider.OperatorError) as conflict:
            choose(name)
        assert conflict.value.status==409 and not bindings(db)
        assert count(db,'saas_beinsystems_operator_company_imports')==0
        ident=str(row['id'])
    else:
        ident=row['company_id']
        if writer=='ai':
            assert str(native_mutation()['id'])==ident  # Existing verified AI behavior.
        else:
            with pytest.raises(CRMAccessError) as conflict:
                native_mutation()
            assert conflict.value.code=='company_name_conflict'
        assert count(db,'saas_beinsystems_operator_company_imports')==1
    assert db.execute("SELECT id::text AS id FROM saas_crm_native_companies WHERE tenant_id=%s::uuid AND lower(name)=lower(%s)", (A,name))==[{'id':ident}]
    db.commit()


@pytest.mark.parametrize('takeover',[False,True])
def test_kommo_exact_route_and_manual_takeover_are_fenced(ready,monkeypatch,takeover):
    from test_dialog_lifecycle_company_binding_postgres import _deal
    db,calls,_=ready
    route=_deal(db)
    with db.cursor() as cur:
        binding.provision_kommo(cur)
    db.commit()
    with pytest.raises(provider.OperatorError) as invalid:
        choose('kommo-picked',**{**route,'conversation_id':'fabricated'})
    assert invalid.value.status==404 and calls==[]
    if takeover:
        manual=db.execute("SELECT id::text AS id FROM saas_crm_native_companies WHERE tenant_id=%s::uuid ORDER BY id LIMIT 1",(A,))[0]['id']
        db.commit()
        underlying=provider._request_operator
        def manual_between_reads(endpoint,body,**credentials):
            if endpoint=='accounts':
                binding.binding(OWNER,**route,company_id=manual)
            return underlying(endpoint,body,**credentials)
        monkeypatch.setattr(provider,'_request_operator',manual_between_reads)
        with pytest.raises(provider.OperatorError) as conflict:
            choose('kommo-picked',**route)
        assert conflict.value.status==409
        assert count(db,'saas_beinsystems_operator_company_imports')==0
        wanted=manual
    else:
        row=choose('kommo-picked',**route)
        assert row['company_name']=='kommo-picked' and count(db,'saas_beinsystems_operator_company_imports')==1
        wanted=row['company_id']
    assert binding.binding(OWNER,**route)['company_id']==wanted
    assert not bindings(db)  # Never wrote to the same tenant's Connect route.
