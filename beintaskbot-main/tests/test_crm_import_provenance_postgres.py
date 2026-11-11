"""Isolated DB assertions; synthetic source responses are not live verification."""
from dataclasses import replace
from datetime import datetime, timezone
import uuid
import pytest
from psycopg.errors import UniqueViolation

from test_tenant_support_inbox import database, db, A, B
from tenant_crm_import_provenance import observe_page
from tenant_crm_sync_import import import_contacts, import_deals
from tenant_crm_relation_identity import current_binding, bind_identity, read_identity
from tenant_crm_relation_contract import BoundIdentity, RecordRef
from tenant_crm_native_access import CRMAccessError
from tenant_crm_identity import record_link

STAMP = datetime(2026, 11, 10, tzinfo=timezone.utc)


@pytest.fixture
def source(db, monkeypatch):
    # The WASM bridge uses JSON timestamps; match native psycopg values.
    original = getattr(db, 'query', None)
    if original:
        def query(sql, params=()):
            rows = original(sql, params)
            for row in rows:
                for key in ('updated_at', 'connected_at', 'enabled_since', 'last_synced_at'):
                    if isinstance(row.get(key), str):
                        row[key] = datetime.fromisoformat(row[key].replace('Z', '+00:00'))
            return rows
        monkeypatch.setattr(db, 'query', query)
    db.execute('''INSERT INTO saas_tenant_integrations(tenant_id,provider,status,account_domain,connected_at)
      VALUES(%s::uuid,'kommo','connected','synthetic.kommo.com',%s)''', (A, STAMP))
    db.execute('''INSERT INTO saas_crm_provider_settings(tenant_id,entity_type,provider,mode,primary_provider,
      sync_direction,allowed_fields,enabled_since) VALUES(%s::uuid,'contact','kommo','beintaskbot','beintaskbot',
      'from_external','["name","phone","email"]','2026-11-09T00:00:00Z')''', (A,))
    db.commit()
    return db


def observed(resource='contacts', ids=(9,), **changes):
    rows = [{'id':n,'name':'Synthetic','updated_at':1794355200, **changes} for n in ids]
    page = observe_page(A, resource, {'provider':'kommo','account_domain':'synthetic.kommo.com',
        'connected_at':STAMP}, {'_embedded':{resource:rows}})
    return rows, page.observation


def count(db, table):
    return db.execute('SELECT count(*) AS n FROM '+table)[0]['n']


def test_new_contact_mapping_is_atomic_and_replay_preserves_original_evidence(source):
    rows, evidence = observed()
    with source.cursor() as cur:
        assert import_contacts(cur, A, rows, observation=evidence) == 1
    stored = source.execute('SELECT * FROM saas_crm_relation_identities')[0]
    assert stored['external_id'] == '9' and stored['provenance'] == 'imported'
    assert stored['evidence_id'] == evidence.evidence_id and stored['verified_by'] is None
    source.rollback()
    assert count(source, 'saas_crm_native_contacts') == count(source, 'saas_crm_relation_identities') == 0
    with source.cursor() as cur: import_contacts(cur, A, rows, observation=evidence)
    source.commit()
    with source.cursor() as cur: import_contacts(cur, A, rows, observation=observed()[1])
    assert count(source, 'saas_crm_native_contacts') == count(source, 'saas_crm_relation_identities') == 1
    assert source.execute('SELECT evidence_id FROM saas_crm_relation_identities')[0]['evidence_id'] == evidence.evidence_id


@pytest.mark.parametrize('match', ['phone','email','legacy_id','bare_import'])
def test_existing_or_heuristically_matched_contact_never_becomes_verified(source, match):
    rows, evidence = observed(custom_fields_values=[
        {'field_code':'PHONE','values':[{'value':'+9940000000'}]},
        {'field_code':'EMAIL','values':[{'value':'synthetic@example.invalid'}]}])
    if match == 'bare_import':
        with source.cursor() as cur: import_contacts(cur, A, rows)
    else:
        internal = str(uuid.uuid4())
        source.execute('''INSERT INTO saas_crm_native_contacts(id,tenant_id,display_name,phone,email)
          VALUES(%s::uuid,%s::uuid,'Synthetic',%s,%s)''',
          (internal,A,'+9940000000' if match=='phone' else '', 'synthetic@example.invalid' if match=='email' else ''))
        if match == 'legacy_id':
            with source.cursor() as cur:
                record_link(cur,tenant_id=A,provider='kommo',entity_type='contact',internal_id=internal,external_id='9')
    source.commit()
    with source.cursor() as cur: import_contacts(cur, A, rows, observation=evidence)
    assert count(source,'saas_crm_native_contacts') == 1
    assert count(source,'saas_crm_relation_identities') == 0


@pytest.mark.parametrize('mutation', [
    "UPDATE saas_tenant_integrations SET connected_at=connected_at+interval '1 second'",
    "UPDATE saas_tenant_integrations SET account_domain='other.kommo.com'",
    "UPDATE saas_tenant_integrations SET status='disconnected'",
    "UPDATE saas_tenant_integrations SET provider='kommo_manual'",
])
def test_connection_change_after_response_blocks_import_before_any_mapping(source, mutation):
    rows, evidence = observed()
    source.execute(mutation)
    with source.cursor() as cur, pytest.raises(CRMAccessError, match='relation_connection_changed'):
        import_contacts(cur, A, rows, observation=evidence)
    assert count(source,'saas_crm_native_contacts') == count(source,'saas_crm_relation_identities') == 0


@pytest.mark.parametrize('kind', ['tenant','provider','entity','id','json'])
def test_receipt_scope_mismatch_fails_closed(source, kind):
    rows, evidence = observed()
    if kind == 'tenant': evidence = replace(evidence,binding=replace(evidence.binding,tenant_id=B))
    if kind == 'provider': evidence = replace(evidence,binding=replace(evidence.binding,provider='beinsystems'))
    if kind == 'entity': evidence = replace(evidence,resource='leads')
    if kind == 'id': rows[0]['id'] = 10
    if kind == 'json': evidence = {'binding': evidence.binding}
    with source.cursor() as cur, pytest.raises(CRMAccessError, match='import_observation_mismatch'):
        import_contacts(cur, A, rows, observation=evidence)
    assert count(source,'saas_crm_native_contacts') == 0


def test_existing_external_identity_collision_rolls_back_fresh_import(source):
    internal = str(uuid.uuid4())
    source.execute("INSERT INTO saas_crm_native_contacts(id,tenant_id,display_name) VALUES(%s::uuid,%s::uuid,'Other')",(internal,A))
    with source.cursor() as cur:
        binding = current_binding(cur,A)
        bind_identity(cur,BoundIdentity(RecordRef('contact',internal),'9',binding),provenance='created',evidence_id='synthetic-create')
    source.commit()
    rows,evidence=observed()
    with source.cursor() as cur, pytest.raises(UniqueViolation):
        import_contacts(cur,A,rows,observation=evidence)
    source.rollback()
    assert count(source,'saas_crm_native_contacts') == count(source,'saas_crm_relation_identities') == 1
    assert count(source,'saas_crm_entity_links') == 0


def test_reconnect_does_not_adopt_previously_imported_external_id(source):
    rows, evidence = observed()
    with source.cursor() as cur: import_contacts(cur,A,rows,observation=evidence)
    source.commit()
    source.execute("UPDATE saas_tenant_integrations SET connected_at=connected_at+interval '1 second'")
    with source.cursor() as cur: new_binding = current_binding(cur,A)
    with source.cursor() as cur: import_contacts(cur,A,rows,observation=replace(evidence,binding=new_binding))
    assert count(source,'saas_crm_relation_identities') == 1
    internal = str(source.execute('SELECT id FROM saas_crm_native_contacts')[0]['id'])
    with source.cursor() as cur, pytest.raises(CRMAccessError,match='relation_mapping_unverified'):
        read_identity(cur,new_binding,RecordRef('contact',internal))


@pytest.mark.parametrize('legacy_first', [False, True])
def test_only_new_deal_is_verified_and_no_company_or_bein_mapping(source, legacy_first):
    pipeline,stage = str(uuid.uuid4()),str(uuid.uuid4())
    source.execute("INSERT INTO saas_tenant_pipelines(tenant_id,pipeline_id,name) VALUES(%s::uuid,%s,'Pipeline')",(A,pipeline))
    source.execute("INSERT INTO saas_tenant_pipeline_stages(tenant_id,pipeline_id,stage_id,name) VALUES(%s::uuid,%s,%s,'Stage')",(A,pipeline,stage))
    source.execute('''INSERT INTO saas_crm_provider_settings(tenant_id,entity_type,provider,mode,primary_provider,
      sync_direction,allowed_fields,enabled_since) VALUES(%s::uuid,'deal','kommo','beintaskbot','beintaskbot',
      'from_external','["title","pipeline_id","stage_id"]','2026-11-09T00:00:00Z')''',(A,))
    with source.cursor() as cur:
        for entity,local,external in [('pipeline',pipeline,'1'),('stage',stage,'2')]:
            record_link(cur,tenant_id=A,provider='kommo',entity_type=entity,internal_id=local,external_id=external)
        rows,evidence=observed('leads',pipeline_id=1,status_id=2)
        assert import_deals(cur,A,rows,observation=None if legacy_first else evidence)==1
    source.commit()
    with source.cursor() as cur: import_deals(cur,A,rows,observation=observed('leads')[1])
    assert count(source,'saas_crm_relation_identities') == (0 if legacy_first else 1)
    if legacy_first:
        return
    item=source.execute('SELECT entity_type,provider,evidence_id FROM saas_crm_relation_identities')[0]
    assert item == {'entity_type':'deal','provider':'kommo','evidence_id':evidence.evidence_id}


def make_store(source, monkeypatch):
    import tenant_crm_sync_schema as schema
    from tenant_crm_sync_store import SyncPageStore, connection_id
    monkeypatch.setattr(schema, '_ready', False)
    schema.ensure_sync_schema(source)
    run = str(uuid.uuid4())
    with source.cursor() as cur:
        connection = connection_id(cur, A)
        cur.execute('''INSERT INTO saas_crm_sync_jobs(tenant_id,resource,run_id,connection_id,status)
          VALUES(%s::uuid,'contacts',%s::uuid,%s,'running')''', (A,run,connection))
    source.commit()
    return SyncPageStore(source, {'tenant_id':A, 'resource':'contacts','run_id':run,
        'connection_id':connection,'next_page':1})


def test_contact_checkpoint_mapping_and_insert_share_one_commit(source, monkeypatch):
    store = make_store(source, monkeypatch)
    rows,evidence=observed()
    store.save_page([{'raw': row} for row in rows],done=True,observation=evidence)
    job=source.execute('SELECT next_page,status,records_saved FROM saas_crm_sync_jobs')[0]
    assert job == {'next_page':2,'status':'done','records_saved':1}
    assert count(source,'saas_crm_native_contacts') == count(source,'saas_crm_relation_identities') == 1


def test_page_checkpoint_failure_cannot_leave_native_or_verified_identity(source, monkeypatch):
    store = make_store(source, monkeypatch)
    original = source.query
    def query(sql, params=()):
        if 'next_page=next_page+1' in sql:
            raise RuntimeError('synthetic checkpoint failure')
        return original(sql,params)
    monkeypatch.setattr(source,'query',query)
    rows,evidence=observed()
    with pytest.raises(RuntimeError,match='synthetic checkpoint failure'):
        store.save_page([{'raw': row} for row in rows],done=True,observation=evidence)
    store.fail()
    assert count(source,'saas_crm_native_contacts') == count(source,'saas_crm_relation_identities') == 0
    job=source.execute('SELECT next_page,records_saved FROM saas_crm_sync_jobs')[0]
    assert job == {'next_page':1,'records_saved':0}


def test_response_from_reconnected_account_cannot_be_saved_to_old_job(source, monkeypatch):
    store = make_store(source, monkeypatch)
    rows,evidence=observed()
    source.execute("UPDATE saas_tenant_integrations SET connected_at=connected_at+interval '1 second'")
    with source.cursor() as cur: binding = current_binding(cur,A)
    source.commit()
    # The successful read used the new account/generation after refresh. Its
    # receipt is valid, but the old job cannot adopt that response.
    store.save_page([{'raw': row} for row in rows],done=True,
        observation=replace(evidence,binding=binding))
    assert count(source,'saas_crm_native_contacts') == count(source,'saas_crm_relation_identities') == 0
    assert source.execute('SELECT status,next_page FROM saas_crm_sync_jobs')[0] == {'status':'failed','next_page':1}
