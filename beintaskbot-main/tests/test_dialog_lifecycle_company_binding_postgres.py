"""Source/tenant proofs through actual disposable PostgreSQL dialogue projections."""
import uuid

import pytest

import tenant_dialog_company_binding as store
import tenant_operator_dialogs as dialogs
from tenant_beinsystems_operator import OperatorError
from test_connect_channel_multiprocess import database
from test_dialog_lifecycle_company_completion import STAFF, bindings, companies
from test_dialog_lifecycle_pglite import command, episodes
from test_tenant_support_inbox import A, B, CA, CB, PROFILE, db

OWNER_B = {'tenant_id':B,'telegram_id':22}


@pytest.fixture
def local(companies,monkeypatch):
    database,*_ = companies
    monkeypatch.setattr(store,'_connect',lambda:database)
    monkeypatch.setattr(dialogs,'_connect',lambda:database)
    # Test only. The serving handler must not provision anything.
    yield companies


def _route(provider, lead, channel, conversation):
    return dict(provider=provider,lead_id=lead,channel_id=channel,conversation_id=conversation)


def _deal(database,lead=503):
    database.execute("INSERT INTO saas_crm_deals(tenant_id,kommo_lead_id,name) VALUES(%s::uuid,%s,'Synthetic deal')",(A,lead))
    database.execute("""INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body)
      VALUES(%s::uuid,%s::uuid,%s,'synthetic-kommo','email','incoming','Synthetic message')""",(str(uuid.uuid4()),A,lead))
    database.commit()
    return _route('kommo',lead,'email','')


def test_native_direct_uses_existing_legacy_binding_and_owner_acl(local,monkeypatch):
    database, company, _, foreign, _ = local
    route = _route('connect',41,CA,'client')
    assert store.binding(PROFILE,**route)['can_manage'] is False  # waiting
    assert dialogs.history(PROFILE,'connect',41,CA,'client')['dialog']['company_binding']['can_manage'] is False
    command()
    accepted = dialogs.history(PROFILE,'connect',41,CA,'client')['dialog']['company_binding']
    assert accepted['can_manage'] is True
    assert accepted['company_id'] is None
    expected = {'company_id':company,'company_name':'Synthetic company','can_manage':True}
    assert store.binding(PROFILE,**route,company_id=company) == expected
    assert dialogs.history(PROFILE,'connect',41,CA,'client')['dialog']['company_binding'] == expected
    assert [entry['company_id'] for entry in bindings(database)] == [company]
    visible = dialogs.listing(PROFILE, lifecycle='all')['dialogs']
    assert next(row for row in visible if row['lead_id'] == 41)['dialog_company_name'] == 'Synthetic company'
    assert all('dialog_company_name' not in row for row in dialogs.listing(OWNER_B, lifecycle='all')['dialogs'])
    for invalid in ({'lead_id':42},{'channel_id':CB},{'conversation_id':'fabricated'}):
        with pytest.raises(OperatorError) as denied:
            store.binding(PROFILE,**{**route,**invalid})
        assert denied.value.status == 404
    with pytest.raises(OperatorError) as denied:
        store.binding(PROFILE,**route,company_id=foreign)
    assert denied.value.status == 403
    with pytest.raises(OperatorError) as denied:
        store.binding(OWNER_B,**route)
    assert denied.value.status == 404
    with pytest.raises(OperatorError) as denied:
        store.binding(STAFF,**route,company_id=None)
    assert denied.value.status in {403,404}
    assert len(bindings(database))==1


def test_group_binding_targets_only_the_selected_source(local):
    database,company,*_ = local
    left,right=CA,str(uuid.uuid4())
    group='120000000000000001@g.us'
    database.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'Synthetic second')",(A,right))
    for lead,channel in ((51,left),(52,right)):
        database.execute("""INSERT INTO saas_connect_conversations
          (tenant_id,connect_channel_id,conversation_id,conversation_kind,lead_id)
          VALUES(%s::uuid,%s::uuid,%s,'group',%s)""",(A,channel,group,lead))
    database.commit()
    from test_dialog_lifecycle_pglite import payload
    from tenant_dialog_lifecycle import mutate
    exact=_route('connect',51,left,group)
    mutate(PROFILE,{**payload(),**exact})
    assert store.binding(PROFILE,**exact,company_id=company)['company_id']==company
    assert store.binding(PROFILE,**_route('connect',52,right,group))['company_id'] is None
    assert [(r['channel_id'],r['conversation_id']) for r in bindings(database)]==[(left,group)]
    combined = dialogs.listing(PROFILE, lifecycle='all')['dialogs']
    assert 'dialog_company_name' not in next(row for row in combined if row['conversation_id'] == group)
    one_source = dialogs.listing(PROFILE, channel='connect:'+left, lifecycle='all')['dialogs']
    assert next(row for row in one_source if row['conversation_id'] == group)['dialog_company_name'] == 'Synthetic company'


def test_kommo_requires_visible_exact_source_and_explicit_schema(local):
    database,company,*_ = local
    route=_deal(database)
    # This fixture may share a migrated disposable DB only inside this test
    # module; absence here verifies that no request path creates the table.
    database.execute(f'DROP TABLE IF EXISTS {store.KOMMO}')
    database.commit()
    with pytest.raises(OperatorError) as unavailable:
        store.binding(PROFILE,**route)
    assert unavailable.value.status==503
    assert database.execute('SELECT to_regclass(%s) AS tbl',(store.KOMMO,))==[{'tbl':None}]
    database.commit()
    with database.cursor() as cur:
        store.provision_kommo(cur)
        store.provision_kommo(cur)
    database.commit()
    for bad in ({'channel_id':'whatsapp'},{'conversation_id':'fabricated'},{'lead_id':504}):
        with pytest.raises(OperatorError) as denied:
            store.binding(PROFILE,company_id=company,**{**route,**bad})
        assert denied.value.status==404
    assert store.binding(PROFILE,**route,company_id=company)['company_id']==company
    assert not bindings(database)
    assert store.binding(PROFILE,**route,company_id=None)['company_id'] is None


def test_kommo_binding_reads_mask_out_of_scope_company_and_denied_source(local,monkeypatch):
    database, company, _, _, hidden=local
    route=_deal(database)
    with database.cursor() as cur:
        store.provision_kommo(cur)
    database.commit()
    store.binding(PROFILE,**route,company_id=hidden)
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    import json
    def rights(dialogs):
        row={'33':{'access_scopes':{'dialogs':{'view':dialogs,'update':dialogs},'companies':{'view':'self','update':'self'}}}}
        database.execute("""INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value)
          VALUES(%s::uuid,'members',%s::jsonb) ON CONFLICT(tenant_id,policy_key)
          DO UPDATE SET value=EXCLUDED.value""",(A,json.dumps(row)))
        database.commit()
    rights('all')
    assert store.binding(STAFF,**route)=={'company_id':None,'company_name':None,'can_manage':False}
    listed = lambda: next(row for row in dialogs.listing(STAFF, lifecycle='all')['dialogs'] if row['lead_id'] == route['lead_id'])
    assert 'dialog_company_name' not in listed()
    with pytest.raises(OperatorError) as denied:
        store.binding(STAFF,**route,company_id=company)
    assert denied.value.status==403
    store.binding(PROFILE,**route,company_id=company)
    assert store.binding(STAFF,**route)['company_id']==company
    assert listed()['dialog_company_name']=='Synthetic company'
    rights('self')
    with pytest.raises(OperatorError) as denied:
        store.binding(STAFF,**route)
    assert denied.value.status in {403,404}


def test_scoped_feature_without_operator_connection(monkeypatch):
    monkeypatch.setenv('BEINSYSTEMS_OPERATOR_TENANT_ID', A)
    profile={**PROFILE,'tenant_status':'active','active':True,'role':'owner'}
    assert store.feature_for(profile)
    assert not store.feature_for({**profile,'tenant_id':B})
    assert not store.feature_for({**profile,'active':False})
    assert not store.feature_for({**profile,'role':'operator','permissions':[]})


@pytest.mark.parametrize('alter',[
    'ADD COLUMN unexpected TEXT',
    'ALTER COLUMN created_at DROP DEFAULT',
    'ENABLE ROW LEVEL SECURITY',
    'DROP CONSTRAINT beinsystems_kommo_binding_channel',
])
def test_unexpected_kommo_shape_does_not_adopt_or_write(local,alter):
    database,company,*_=local
    route=_deal(database)
    database.execute(f'DROP TABLE IF EXISTS {store.KOMMO}')
    database.commit()
    with database.cursor() as cur:
        store.provision_kommo(cur)
        cur.execute(f'ALTER TABLE {store.KOMMO} {alter}')
    database.commit()
    with database.cursor() as cur,pytest.raises(RuntimeError,match='Unexpected Kommo'):
        store.provision_kommo(cur)
    database.rollback()
    for extra in ({},{'company_id':company}):
        with pytest.raises(OperatorError) as unavailable:
            store.binding(PROFILE,**route,**extra)
        assert unavailable.value.status==503
    assert database.execute(f'SELECT count(*) AS n FROM {store.KOMMO}')==[{'n':0}]
    database.execute(f'DROP TABLE {store.KOMMO}')
    database.commit()
