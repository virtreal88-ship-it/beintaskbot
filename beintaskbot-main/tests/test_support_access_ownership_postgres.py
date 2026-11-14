"""Additive support responsibility, using disposable localhost PostgreSQL only."""
import uuid

import psycopg
import pytest

from test_scoped_access_foundation_postgres import db as historical_db, tid
from test_tenant_support_inbox import A, B, CA, PROFILE, db
from test_connect_channel_multiprocess import database
from tenant_linear_policy import TenantLinearError
from tenant_scoped_access_schema import migrate_resource_ownership
import tenant_ai_agents_schema as schema
import tenant_support_inbox_store as store


def test_historical_support_ownership_preserves_executor_and_creator(historical_db):
    with historical_db() as conn:
        conn.execute('''CREATE TABLE saas_support_tickets(
            tenant_id UUID NOT NULL,id UUID PRIMARY KEY,assigned_to BIGINT,created_by BIGINT)''')
        for n in range(1, 6):
            conn.execute('INSERT INTO saas_support_tickets VALUES(%s,%s,%s,%s)',
                         (tid(n), tid(900+n), n*10+3, n*10+4))
        with conn.cursor() as cur:
            migrate_resource_ownership(cur, ('saas_support_tickets',))
    with historical_db() as conn:
        rows = conn.execute('SELECT * FROM saas_support_tickets ORDER BY tenant_id').fetchall()
        assert [r['access_responsible_id'] for r in rows] == [11, None, None, None, None]
        assert [r['assigned_to'] for r in rows] == [13, 23, 33, 43, 53]
        assert [r['created_by'] for r in rows] == [14, 24, 34, 44, 54]
        issues = conn.execute("SELECT * FROM saas_access_migration_issues WHERE resource_table='saas_support_tickets'").fetchall()
        assert len(issues) == 4
        assert {r['reason'] for r in issues} == {'sole_active_company_owner_required'}
        # Later system-created cases are not historical records on a rerun.
        conn.execute('INSERT INTO saas_support_tickets(tenant_id,id) VALUES(%s,%s)', (tid(1), tid(990)))
        with conn.cursor() as cur:
            migrate_resource_ownership(cur, ('saas_support_tickets',))
        assert conn.execute('SELECT access_responsible_id FROM saas_support_tickets WHERE id=%s', (tid(990),)).fetchone()['access_responsible_id'] is None
        assert len(conn.execute('SELECT * FROM saas_access_schema_migrations').fetchall()) == 1
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with historical_db() as conn:
            conn.execute('UPDATE saas_support_tickets SET access_responsible_id=21 WHERE tenant_id=%s', (tid(1),))


def test_manual_support_creator_is_responsible_not_executor_and_hidden(db, monkeypatch):
    monkeypatch.setattr(store, 'summarize_created', lambda *_: None)
    db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,permissions) VALUES(%s,33,'operator','[\"support\"]')", (A,))
    db.commit()
    request = {'request_id': str(uuid.uuid4()), 'lead_id': 41}
    result = store.create(PROFILE, request)
    key = result['ticket']['id']
    assert 'access_responsible_id' not in result['ticket']
    assert store.create(PROFILE, request)['ticket']['id'] == key
    stored = db.execute('SELECT created_by,assigned_to,access_responsible_id FROM saas_support_tickets WHERE tenant_id=%s AND id=%s', (A, key))[0]
    assert stored == {'created_by': 11, 'assigned_to': None, 'access_responsible_id': 11}
    db.commit()
    changed = store.update(PROFILE, key, {'assigned_to': 33, 'description': 'Changed by the owner'})
    assert changed['ticket']['assigned_to'] == 33
    assert 'access_responsible_id' not in changed['ticket']
    with db.cursor() as cur:
        # A new event on the existing case must not transfer responsibility.
        again = store.create_in_transaction(cur, A, CA, 'client', 'Same case', 'other-actor-event', actor=33, source='manual')
    db.commit()
    assert again == {'id': key, 'created': False}
    assert db.execute('SELECT created_by,access_responsible_id FROM saas_support_tickets WHERE tenant_id=%s AND id=%s', (A, key))[0] == {'created_by': 11, 'access_responsible_id': 11}
    db.commit()
    assert all('access_responsible_id' not in r for r in store.listing(PROFILE)['tickets'])
    with pytest.raises(TenantLinearError):
        store.update(PROFILE, key, {'access_responsible_id': 33})
    with pytest.raises(TenantLinearError):
        store.create(PROFILE, {**request, 'access_responsible_id': 33})
    with pytest.raises(TenantLinearError) as foreign:
        store.detail({'tenant_id': B, 'telegram_id': 22}, key)
    assert foreign.value.status == 404
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.execute('UPDATE saas_support_tickets SET access_responsible_id=22 WHERE tenant_id=%s AND id=%s', (A, key))
    db.rollback()


def test_system_support_stays_unowned_after_cold_schema_repair(db):
    with db.cursor() as cur:
        receipt = store.create_in_transaction(cur, A, CA, 'client', 'Synthetic handoff', 'system-event')
    db.commit()
    key = receipt['id']
    assert db.execute('SELECT created_by,access_responsible_id FROM saas_support_tickets WHERE tenant_id=%s AND id=%s', (A, key))[0] == {'created_by': None, 'access_responsible_id': None}
    db.execute('ALTER TABLE saas_support_tickets DROP CONSTRAINT saas_support_tickets_access_owner_fk')
    db.commit()
    assert schema._schema_current(db) is False
    schema.ensure_schema(db)
    assert schema._schema_current(db) is True
    assert db.execute('SELECT access_responsible_id FROM saas_support_tickets WHERE tenant_id=%s AND id=%s', (A, key))[0]['access_responsible_id'] is None
    db.commit()


def test_support_responsibility_is_not_a_report_field_and_catalog_has_no_delete():
    from tenant_scoped_access import SCOPE_ACTION_CATALOG
    from tenant_night_report_store import public
    assert SCOPE_ACTION_CATALOG['support'] == ('view', 'create', 'update')
    raw = {'id': 'case', 'assigned_to': 33, 'created_by': 11, 'access_responsible_id': 11}
    expected = {'id': 'case', 'assigned_to': 33, 'created_by': 11}
    assert store.public(raw) == expected
    assert public(raw) == expected
    assert raw['access_responsible_id'] == 11

# Run the same missing-object/field upgrade matrix against native PostgreSQL,
# not just PGlite. This module's database fixture supplies disposable localhost DBs.
from test_tenant_support_inbox import test_support_sla_upgrade_cannot_be_hidden_by_schema_readiness


def test_sla_only_upgrade_busy_table_fails_bounded_without_partial_commit(database):
    from psycopg.rows import dict_row
    with psycopg.connect(database.dsn) as conn:
        conn.execute('ALTER TABLE saas_support_tickets DROP COLUMN risk_assessment')
    try:
        with psycopg.connect(database.dsn) as holder:
            holder.execute('LOCK TABLE saas_support_tickets IN ACCESS SHARE MODE')
            with psycopg.connect(database.dsn, row_factory=dict_row) as repair:
                repair.execute("SET lock_timeout='100ms'")
                assert schema._schema_current(repair, include_support_sla=False)
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    schema.ensure_schema(repair)
                repair.rollback()
                assert not schema._schema_current(repair)
            holder.rollback()
        with psycopg.connect(database.dsn, row_factory=dict_row) as conn:
            schema.ensure_schema(conn)
            assert schema._schema_current(conn)
    finally:
        with psycopg.connect(database.dsn, row_factory=dict_row) as conn:
            schema.ensure_schema(conn)
