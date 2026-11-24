"""Real local PostgreSQL ownership/rollback tests; all Operator transport synthetic."""
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import psycopg
import pytest
from cryptography.fernet import Fernet
from psycopg.rows import dict_row

import tenant_beinsystems_operator as provider
import tenant_crm_native_companies as native
import tenant_dialog_company_binding as binding
import tenant_dialog_company_operator as service
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


def choose(term,**options):
    return service.chooser(OWNER,**{**ROUTE,**options},term=term,choice=True)['binding']


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


def test_exact_selection_reuses_the_direct_binding_and_refuses_blind_replacement(ready):
    db,calls,_=ready
    company_count=count(db,'saas_crm_native_companies')
    one=choose('exact-one')
    assert one['operator_account']=='exact-one' and one['can_search_operator'] is True
    assert choose('exact-one')==one
    assert calls==[('me',{}),('accounts',{'search':'exact-one','limit':5})]*2
    with pytest.raises(provider.OperatorError) as refuses:
        choose('exact-other')
    assert refuses.value.status==409
    assert binding.binding(OWNER,**ROUTE)==one
    assert count(db,'saas_operator_dialog_accounts')==1
    assert count(db,'saas_crm_native_companies')==company_count
    assert not bindings(db)
    assert count(db,'saas_beinsystems_operator_company_imports')==0


def test_inline_replace_and_unlink_require_current_exact_binding(ready):
    db,_,_=ready
    original=choose('inline-original',expected_company_id=None)
    changed=choose('inline-next',expected_company_id=original['company_id'])
    assert changed['operator_account']=='inline-next'
    with pytest.raises(provider.OperatorError) as stale:
        choose('stale-inline',expected_company_id=original['company_id'])
    assert stale.value.status==409
    with pytest.raises(provider.OperatorError) as stale_unlink:
        binding.binding(OWNER,**ROUTE,company_id=None,expected_company_id=original['company_id'])
    assert stale_unlink.value.status==409
    assert binding.binding(OWNER,**ROUTE,company_id=None,expected_company_id=changed['company_id'])['company_id'] is None
    assert not bindings(db) and count(db,'saas_beinsystems_operator_company_imports')==0


@pytest.mark.parametrize('active',[False,True])
def test_uniset_exact_result_is_verified_again_and_linked_without_touching_crm(ready,monkeypatch,active):
    db,calls,instance=ready
    if active:
        _activate_synthetic(db,instance)
    for name in ('UniSet','UNISET'):
        db.execute("INSERT INTO saas_crm_native_companies (id,tenant_id,name) VALUES(%s::uuid,%s::uuid,%s)",(str(uuid.uuid4()),A,name))
    db.commit()
    original=provider._request_operator
    def ranked(endpoint,body,**credentials):
        if endpoint=='me':
            return original(endpoint,body,**credentials)
        calls.append((endpoint,body))
        assert body=={'search':'unis','limit':5}
        return {'status':'success','data':[{'account':'uniset-other'},{'account':'uniset'}]}
    monkeypatch.setattr(provider,'_request_operator',ranked)
    assert service.chooser(OWNER,**ROUTE,term='unis')['accounts'][1]=={'account':'uniset'}
    selected=choose('uniset',expected_company_id=None,selection_search='unis')
    assert selected['operator_account']=='uniset'
    assert binding.binding(OWNER,**ROUTE)==selected
    with pytest.raises(provider.OperatorError) as different:
        choose('UniSet',expected_company_id=selected['company_id'],selection_search='unis')
    assert different.value.status==409
    assert binding.binding(OWNER,**ROUTE)==selected
    assert count(db,'saas_beinsystems_operator_company_imports')==0
    assert not bindings(db)
    assert db.execute("SELECT count(*) AS n FROM saas_crm_native_companies WHERE lower(name)='uniset'")==[{'n':2}]
    db.commit()


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
    assert count(db,'saas_beinsystems_operator_company_imports')==0
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


def test_concurrent_initial_choices_have_one_winner(ready,monkeypatch):
    db,_,_=ready
    original=provider._request_operator
    barrier=Barrier(2)
    def simultaneous(endpoint,body,**credentials):
        if endpoint=='accounts':
            barrier.wait(timeout=10)
        return original(endpoint,body,**credentials)
    monkeypatch.setattr(provider,'_request_operator',simultaneous)
    monkeypatch.setattr(binding,'_connect',lambda:psycopg.connect(db.dsn,row_factory=dict_row))
    monkeypatch.setattr(provider,'_connect',lambda:psycopg.connect(db.dsn,row_factory=dict_row))
    def pick(account):
        try:
            return choose(account,expected_company_id=None)['operator_account']
        except provider.OperatorError as error:
            assert error.status==409
            return None
    with ThreadPoolExecutor(2) as workers:
        futures=[workers.submit(pick,account) for account in ('uniset','other')]
        results=[future.result(timeout=15) for future in futures]
    winner=next(result for result in results if result)
    assert results.count(None)==1
    assert binding.binding(OWNER,**ROUTE)['operator_account']==winner
    assert not bindings(db) and count(db,'saas_beinsystems_operator_company_imports')==0


def test_kommo_requires_exact_route_and_keeps_connect_binding_separate(ready):
    from test_dialog_lifecycle_company_binding_postgres import _deal
    db,calls,_=ready
    route=_deal(db)
    with pytest.raises(provider.OperatorError) as invalid:
        choose('kommo-picked',**{**route,'conversation_id':'fabricated'})
    assert invalid.value.status==404 and calls==[]
    chosen=choose('kommo-picked',**route)
    assert binding.binding(OWNER,**route)==chosen
    assert binding.binding(OWNER,**ROUTE)['operator_account'] is None
    assert not bindings(db)
    assert count(db,'saas_beinsystems_operator_company_imports')==0
