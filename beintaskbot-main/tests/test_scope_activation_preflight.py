"""Disposable PGlite inventory tests; no bootstrap/provider calls."""
import importlib.util
import json
from pathlib import Path
import pytest
from test_tenant_support_inbox import Database
PATH=Path(__file__).resolve().parents[1]/'scripts/scope_activation_preflight.py'
spec=importlib.util.spec_from_file_location('preflight',PATH)
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
A='00000000-0000-0000-0000-000000000001'
E='00000000-0000-0000-0000-000000000002'
@pytest.fixture
def db():
    if not (PATH.parents[1]/'.support-test-deps/node_modules/@electric-sql/pglite').exists():pytest.skip('PGlite unavailable')
    d=Database()
    tables={
      'saas_access_migration_issues':'tenant_id uuid,resource_table text,resource_id text',
      'saas_tenant_members':'tenant_id uuid,telegram_id bigint,active bool,department_id uuid,role text',
      'saas_tenant_departments':'tenant_id uuid,id uuid,active bool',
      'saas_tenants':'id uuid,status text,owner_telegram_id bigint',
      'saas_connect_channels':'tenant_id uuid,connect_channel_id uuid',
      'saas_dialog_source_members':'tenant_id uuid,source_id uuid,employee_id bigint,provider text',
      'saas_crm_messages':'tenant_id uuid,channel text,body text',
      'saas_crm_sync_outbox':'id uuid,tenant_id uuid,entity_type text,internal_id uuid,state text,attempts int',
      'saas_crm_legacy_delivery_attempts':'event_id uuid,outcome text,claim_attempt int',
      'saas_crm_entity_links':'tenant_id uuid,entity_type text,internal_id uuid,provider text',
      'saas_crm_relation_identities':'tenant_id uuid,entity_type text,internal_id uuid,provider text,account_id text,connection_generation text',
      'saas_tenant_integrations':'tenant_id uuid,provider text,status text,account_domain text,connected_at timestamptz',
      'saas_crm_scalar_outbox':'id uuid,state text,attempt_outcome text,binding jsonb,external_id text',
      'saas_crm_relation_outbox':'id uuid,state text,attempt_outcome text',
      'saas_crm_scalar_heads':'tenant_id uuid,entity_type text,internal_id uuid',
      'saas_crm_relation_heads':'tenant_id uuid,changed_type text,changed_id uuid',
    }
    for n in ['saas_crm_native_'+x for x in ('contacts','companies','deals','tasks')]+['saas_ai_agents','saas_support_tickets']:
        tables[n]='tenant_id uuid,id uuid,access_responsible_id bigint'
    for n,cols in tables.items():d.execute(f'CREATE TABLE {n} ({cols})')
    d.commit()
    yield d
    d.rollback();d.process.stdin.close();d.process.wait(timeout=20)
def test_empty_not_authorization(db):
    r=p.inventory(db)
    assert r['safe_to_enable'] is False
    assert all(x['status']=='observed' and x['count']==0 for x in r['checks'].values())
    assert len(r['mandatory_external_evidence_not_certified'])==7
    assert not db.transaction
def test_opaque_bounded_unchanged(db):
    db.execute("INSERT INTO saas_tenants VALUES(%s,'active',123456789)",(A,))
    db.execute("INSERT INTO saas_tenant_members VALUES(%s,123456789,true,NULL,'owner')",(A,))
    db.execute('INSERT INTO saas_connect_channels VALUES(%s,%s)',(A,E))
    db.execute("INSERT INTO saas_crm_messages VALUES(%s,'secret-provider','RAW MESSAGE')",(A,))
    db.execute("INSERT INTO saas_crm_sync_outbox VALUES(%s,%s,'contact',%s,'pending',2)",(E,A,E))
    db.execute("INSERT INTO saas_crm_legacy_delivery_attempts VALUES(%s,'not_started',2)",(E,))
    db.execute("INSERT INTO saas_crm_scalar_outbox VALUES(%s,'needs_verification','not_started',NULL,'SECRET EXTERNAL ID')",(E,))
    db.execute('INSERT INTO saas_crm_native_contacts VALUES(%s,%s,NULL)',(A,E));db.commit()
    r=p.inventory(db,sample_limit=1);c=r['checks']
    for k in ('connect_unassigned_sources','kommo_source_mapping_required','legacy_attempted_pending','legacy_claim_aware_unsent','scalar_unverified_binding','unowned_saas_crm_native_contacts'):
        assert c[k]['count']==1 and len(c[k]['sample_ids'])==1
    assert c['legacy_mismatched_unsent_evidence']['count']==0
    for secret in (A,E,'123456789','SECRET','RAW MESSAGE','secret-provider'):assert secret not in json.dumps(r)
    assert db.execute('SELECT count(*) AS n FROM saas_crm_messages')[0]['n']==1
    db.commit();r=p.inventory(db,sample_limit=0)
    assert r['checks']['scalar_unverified_binding']['sample_ids']==[]
    assert r['checks']['scalar_unverified_binding']['sample_truncated']
def test_missing_schema_unknown(db):
    db.execute('DROP TABLE saas_access_migration_issues');db.commit()
    r=p.inventory(db)
    assert r['checks']['historical_ownership_issues']=={'status':'unknown','reason':'query_unavailable_or_failed'}
    assert r['checks']['inactive_members']['count']==0
def test_database_rejects_write(db,monkeypatch):
    original=db.query;checked=[]
    def spy(sql,params=()):
        r=original(sql,params)
        if sql=='SHOW transaction_read_only':
            original('SAVEPOINT prove_readonly')
            with pytest.raises(Exception) as exc:original("INSERT INTO saas_tenants VALUES('00000000-0000-0000-0000-000000000003','active',1)")
            assert 'read-only' in str(exc.value).lower()
            original('ROLLBACK TO SAVEPOINT prove_readonly');original('RELEASE SAVEPOINT prove_readonly');checked.append(True)
        return r
    monkeypatch.setattr(db,'query',spy);p.inventory(db)
    assert checked==[True]
def test_cli_no_ambient_connection(monkeypatch,tmp_path,capsys):
    import psycopg
    monkeypatch.setenv('DATABASE_URL','postgresql://secret:password@production/db')
    monkeypatch.setattr(psycopg,'connect',lambda *a,**k:pytest.fail('must not connect'))
    with pytest.raises(SystemExit):p.main([])
    f=tmp_path/'dsn';f.write_text('dbname=postgres')
    assert p.main(['--dsn-file',str(f)])==3
    assert 'password' not in capsys.readouterr().out
def test_invalid_limit():
    with pytest.raises(ValueError):p.inventory(None,sample_limit=101)

def test_stale_generation_and_mismatched_unsent(db):
    db.execute("INSERT INTO saas_tenant_integrations VALUES(%s,'kommo','connected','private.example','2026-11-11T12:00:00+00:00')",(A,))
    db.execute("INSERT INTO saas_crm_relation_identities VALUES(%s,'contact',%s,'kommo','private.example','kommo:2026-11-11T12:00:00+00:00')",(A,E))
    db.execute("INSERT INTO saas_crm_sync_outbox VALUES(%s,%s,'contact',%s,'pending',3)",(E,A,E))
    db.execute("INSERT INTO saas_crm_legacy_delivery_attempts VALUES(%s,'not_started',2)",(E,));db.commit()
    r=p.inventory(db)
    assert r['checks']['stale_account_or_generation_identities']['count']==0
    assert r['checks']['legacy_mismatched_unsent_evidence']['count']==1
    assert r['checks']['legacy_claim_aware_unsent']['count']==0
    db.execute("UPDATE saas_tenant_integrations SET connected_at='2026-11-12T12:00:00+00:00'");db.commit()
    r=p.inventory(db)
    assert r['checks']['stale_account_or_generation_identities']['count']==1
    assert 'private.example' not in json.dumps(r)

@pytest.mark.parametrize('owner', [None, 99])
def test_null_and_mismatched_company_owner_are_ambiguous(db,owner):
    db.execute("INSERT INTO saas_tenants VALUES(%s,'active',%s)",(A,owner))
    db.execute("INSERT INTO saas_tenant_members VALUES(%s,11,true,NULL,'owner')",(A,));db.commit()
    assert p.inventory(db)['checks']['ambiguous_company_owners']['count']==1
    db.execute('UPDATE saas_tenants SET owner_telegram_id=11');db.commit()
    assert p.inventory(db)['checks']['ambiguous_company_owners']['count']==0
