"""Actual isolated PostgreSQL/WASM provenance SQL; no network/provider calls."""
import json
from dataclasses import replace
import pytest
from psycopg.errors import CheckViolation
from test_tenant_support_inbox import database, db, A, B, Cursor
from test_crm_import_provenance_postgres import source, STAMP
from tenant_crm_import_provenance import observe_page
from tenant_crm_native_access import CRMAccessError
from tenant_task_flow import TaskFlowError
import tenant_kommo_board_provenance as proof


@pytest.fixture
def board(source, monkeypatch):
    # PGlite's cursor shim lacks psycopg executemany; execute identical bound SQL.
    if not hasattr(Cursor, 'executemany'):
        monkeypatch.setattr(Cursor, 'executemany', lambda self, sql, rows: [self.execute(sql, row) for row in rows], raising=False)
    with source.cursor() as cur: proof.migrate(cur)
    source.execute('DELETE FROM saas_crm_board_provenance')
    source.commit()
    return source


def observed(resource='tasks', raw=None):
    raw = raw or {'id':12, 'updated_at':100, 'name':'Synthetic'}
    page = observe_page(A,resource, {'provider':'kommo','account_domain':'synthetic.kommo.com','connected_at':STAMP}, {'_embedded':{resource:[raw]}})
    return raw, page.observation


def cache(db, resource, raw, tenant=A):
    table, key = ('saas_crm_tasks','kommo_task_id') if resource=='tasks' else ('saas_crm_deals','kommo_lead_id')
    db.execute(f'''INSERT INTO {table}(tenant_id,{key},raw) VALUES(%s::uuid,%s,%s::jsonb)
      ON CONFLICT(tenant_id,{key}) DO UPDATE SET raw=EXCLUDED.raw''',(tenant,raw['id'],json.dumps(raw)))


def seed(db,resource='tasks',raw=None):
    raw, obs = observed(resource,raw)
    cache(db,resource,raw)
    with db.cursor() as cur: proof.record(cur,A,resource,[{'raw':raw}],obs)
    db.commit()
    return raw,obs


def revisions(db,resource='tasks',**kwargs):
    with db.cursor() as cur: return proof.revisions(cur,A,resource,[12],**kwargs)


@pytest.mark.parametrize('resource',['tasks','leads'])
def test_schema_and_legitimate_observation_exact_digest_revision(board,resource):
    raw,obs=seed(board,resource)
    result=revisions(board,resource,expected_raw={'12':raw})
    assert len(result['12'])==64
    stored=board.execute('SELECT * FROM saas_crm_board_provenance')[0]
    assert stored['raw_digest']==proof.digest(raw) and stored['source_revision']==result['12']
    assert stored['account_id']==obs.binding.account_id and stored['connection_generation']==obs.binding.connection_generation
    with board.cursor() as cur:
        assert proof.require(cur,obs.binding,resource,12,result['12'])==result['12']
    assert revisions(board,resource,expected_raw={'12':{**raw,'updated_at':101}})=={}
    assert revisions(board,resource,expected_raw={})=={}


@pytest.mark.parametrize('mutation',[
    "UPDATE saas_tenant_integrations SET connected_at=connected_at+interval '1 second'",
    "UPDATE saas_tenant_integrations SET account_domain='other.kommo.com'",
    "UPDATE saas_tenant_integrations SET status='disconnected'",
])
def test_connection_changed_between_read_and_record_cannot_stamp(board,mutation):
    raw,obs=observed(); cache(board,'tasks',raw)
    board.execute(mutation)
    with board.cursor() as cur,pytest.raises(CRMAccessError): proof.record(cur,A,'tasks',[{'raw':raw}],obs)
    assert board.execute('SELECT * FROM saas_crm_board_provenance')==[]
    board.rollback()


def test_stale_proof_binding_and_tenant_cannot_read_new_cache(board):
    raw,obs=seed(board)
    board.execute("UPDATE saas_tenant_integrations SET connected_at=connected_at+interval '1 second'"); board.commit()
    assert revisions(board)=={}
    with board.cursor() as cur:
        assert proof.revisions(cur,B,'tasks',[12],replace(obs.binding,tenant_id=B))=={}


def test_unobserved_cache_change_invalidates_even_allow_latest(board):
    raw,obs=seed(board); initial=revisions(board)['12']
    cache(board,'tasks',{**raw,'updated_at':101}); board.commit()
    assert revisions(board)=={}
    with board.cursor() as cur,pytest.raises(TaskFlowError): proof.require(cur,obs.binding,'tasks',12,initial,allow_latest=True)


def test_initial_revision_vs_latest_verified_same_source(board):
    raw,obs=seed(board); initial=revisions(board)['12']
    raw,obs=seed(board,raw={**raw,'updated_at':101}); latest=revisions(board)['12']
    assert latest!=initial
    with board.cursor() as cur,pytest.raises(TaskFlowError): proof.require(cur,obs.binding,'tasks',12,initial)
    with board.cursor() as cur: assert proof.require(cur,obs.binding,'tasks',12,initial,allow_latest=True)==latest


def test_missing_table_fails_closed_without_request_time_ddl(board):
    raw,obs=seed(board); initial=revisions(board)['12']
    board.execute('DROP TABLE saas_crm_board_provenance')
    try:
        assert revisions(board)=={}
        with board.cursor() as cur,pytest.raises(TaskFlowError): proof.require(cur,obs.binding,'tasks',12,initial)
        assert board.execute("SELECT to_regclass('saas_crm_board_provenance') AS table_name")[0]['table_name'] is None
    finally:
        board.rollback()


def test_command_refresh_and_cache_write_rollback_atomically(board):
    raw,obs=seed(board); initial=revisions(board)['12']; board.commit()
    with board.cursor() as cur:
        assert proof.require(cur,obs.binding,'tasks',12,initial)==initial
        cache(board,'tasks',{**raw,'updated_at':102})
        changed=proof.refresh_after_command(cur,obs.binding,'tasks',12)
        assert changed!=initial
        assert proof.require(cur,obs.binding,'tasks',12,changed)==changed
    board.rollback()
    assert revisions(board)=={'12':initial}
    assert board.execute('SELECT raw FROM saas_crm_tasks')[0]['raw']==raw
    board.commit()
    with board.cursor() as cur:
        proof.require(cur,obs.binding,'tasks',12,initial)
        cache(board,'tasks',{**raw,'updated_at':102})
        changed=proof.refresh_after_command(cur,obs.binding,'tasks',12)
    board.commit()
    assert revisions(board)=={'12':changed}


def test_schema_rejects_invalid_resource_and_nonpositive_id(board):
    for resource,ident in [('contacts',12),('tasks',0)]:
        with pytest.raises(CheckViolation):
            board.execute('''INSERT INTO saas_crm_board_provenance(tenant_id,resource,external_id,account_id,connection_generation,raw_digest,source_revision)
              VALUES(%s::uuid,%s,%s,'test','g','d','r')''',(A,resource,ident))
        board.rollback()


@pytest.mark.parametrize('resource',['tasks','leads'])
def test_same_id_observation_cannot_stamp_changed_source_payload(board,resource):
    raw,obs=observed(resource)
    changed={**raw,'updated_at':101}
    cache(board,resource,changed)
    with board.cursor() as cur,pytest.raises(CRMAccessError,match='board_observation_payload_mismatch'):
        proof.record(cur,A,resource,[{'raw':changed}],obs)
    assert board.execute('SELECT * FROM saas_crm_board_provenance')==[]


def test_observation_digest_survives_mutation_of_shared_payload_object(board):
    raw,obs=observed()
    raw['updated_at']=999
    cache(board,'tasks',raw)
    with board.cursor() as cur,pytest.raises(CRMAccessError,match='board_observation_payload_mismatch'):
        proof.record(cur,A,'tasks',[{'raw':raw}],obs)


def test_legacy_id_only_observation_does_not_mint_board_proof(board):
    raw,obs=observed()
    cache(board,'tasks',raw)
    with board.cursor() as cur,pytest.raises(CRMAccessError,match='board_observation_payload_mismatch'):
        proof.record(cur,A,'tasks',[{'raw':raw}],replace(obs,raw_digests=()))


def test_lead_contact_enrichment_allowed_but_relationship_and_stage_change_denied(board):
    raw,obs=observed('leads',{'id':12,'status_id':4,'_embedded':{'contacts':[{'id':90,'is_main':True}]}})
    enriched={**raw,'_embedded':{'contacts':[{'id':90,'is_main':True,'name':'Enriched','custom_fields_values':[]}]}}
    cache(board,'leads',enriched)
    with board.cursor() as cur: proof.record(cur,A,'leads',[{'raw':enriched}],obs)
    assert revisions(board,'leads')
    for changed in ({**enriched,'status_id':5}, {**enriched,'_embedded':{'contacts':[{'id':91,'is_main':True}]}}):
        with board.cursor() as cur,pytest.raises(CRMAccessError,match='board_observation_payload_mismatch'):
            proof.record(cur,A,'leads',[{'raw':changed}],obs)
