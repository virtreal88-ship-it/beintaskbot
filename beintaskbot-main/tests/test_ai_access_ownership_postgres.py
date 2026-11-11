"""AI responsibility is additive; existing creator/management authority is kept."""
import uuid

import psycopg
from psycopg.rows import dict_row
import pytest

from test_scoped_access_foundation_postgres import db, tid
from test_connect_channel_multiprocess import database
from tenant_scoped_access_schema import migrate_resource_ownership
import tenant_ai_agents_store as agents
from tenant_linear_policy import TenantLinearError


def test_historical_ai_ownership_uses_unambiguous_owner_not_old_creator(db):
    with db() as conn:
        conn.execute('''CREATE TABLE saas_ai_agents(
            id UUID PRIMARY KEY,tenant_id UUID NOT NULL,created_by BIGINT NOT NULL)''')
        for number in range(1, 6):
            conn.execute('INSERT INTO saas_ai_agents VALUES(%s,%s,%s)', (tid(700 + number), tid(number), number * 10 + 3))
        with conn.cursor() as cur:
            migrate_resource_ownership(cur, ('saas_ai_agents',))
    with db() as conn:
        rows = conn.execute('SELECT * FROM saas_ai_agents ORDER BY tenant_id').fetchall()
        assert [r['created_by'] for r in rows] == [13, 23, 33, 43, 53]
        assert [r['access_responsible_id'] for r in rows] == [11, None, None, None, None]
        assert len(conn.execute("SELECT * FROM saas_access_migration_issues WHERE resource_table='saas_ai_agents'").fetchall()) == 4
        conn.execute('INSERT INTO saas_ai_agents(id,tenant_id,created_by) VALUES(%s,%s,13)', (tid(800), tid(1)))
        with conn.cursor() as cur:
            migrate_resource_ownership(cur, ('saas_ai_agents',))
        assert conn.execute('SELECT access_responsible_id FROM saas_ai_agents WHERE id=%s', (tid(800),)).fetchone()['access_responsible_id'] is None
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with db() as conn:
            conn.execute('UPDATE saas_ai_agents SET access_responsible_id=21 WHERE tenant_id=%s', (tid(1),))


def test_new_ai_agent_responsibility_is_its_authorized_creator_and_not_returned(database, monkeypatch):
    tenant = str(uuid.uuid4())
    owner, actor, outsider = 81001, 81002, 81003
    database.execute("INSERT INTO saas_tenants(id,slug,name,status,owner_telegram_id) VALUES(%s,%s,'Synthetic','active',%s)", (tenant, tenant, owner))
    for employee, role in [(owner, 'owner'), (actor, 'admin'), (outsider, 'manager')]:
        database.execute('INSERT INTO saas_tenant_members(tenant_id,telegram_id,role) VALUES(%s,%s,%s)', (tenant, employee, role))
    database.execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value) VALUES(%s,'members','{\"81002\":{\"ai_agents_manage\":true}}')", (tenant,))
    database.commit()
    factory = lambda: psycopg.connect(database.dsn, row_factory=dict_row)
    monkeypatch.setattr(agents, '_connect', factory)
    monkeypatch.setattr(agents, 'platform_service_ready', lambda: True)
    monkeypatch.setattr(agents, 'platform_model_allowed', lambda model: True)
    monkeypatch.delenv('TENANT_SCOPED_ACCESS_ENABLED', raising=False)
    profile = {'tenant_id': tenant, 'telegram_id': actor}
    row = agents.save_agent(profile, {'name': 'Synthetic draft', 'instructions': 'Synthetic only',
                                      'enabled': False, 'model': 'synthetic-model'})
    assert 'access_responsible_id' not in row
    with factory() as conn:
        stored = conn.execute('SELECT created_by,access_responsible_id,enabled,version FROM saas_ai_agents WHERE tenant_id=%s AND id=%s', (tenant, row['id'])).fetchone()
        assert stored['created_by'] == stored['access_responsible_id'] == actor
        assert stored['enabled'] is False
    changed = agents.save_agent(profile, {'name': 'Renamed synthetic', 'expected_version': stored['version']}, agent_id=row['id'])
    assert 'access_responsible_id' not in changed
    assert all('access_responsible_id' not in r for r in agents.list_agents(profile))
    with factory() as conn:
        assert conn.execute('SELECT access_responsible_id FROM saas_ai_agents WHERE id=%s', (row['id'],)).fetchone()['access_responsible_id'] == actor
    with pytest.raises(TenantLinearError):
        agents.save_agent(profile, {'expected_version': changed['version'], 'access_responsible_id': owner}, agent_id=row['id'])
    with pytest.raises(TenantLinearError) as forbidden:
        agents.save_agent({'tenant_id': tenant, 'telegram_id': outsider}, {'name': 'Denied', 'model': 'synthetic-model'})
    assert forbidden.value.status == 403
    # Removing the existing AI-management flag still revokes access. The new
    # responsibility field is metadata, not a replacement authorization grant.
    with factory() as conn:
        conn.execute("UPDATE saas_tenant_workflow_policies SET value='{}' WHERE tenant_id=%s AND policy_key='members'", (tenant,))
    with pytest.raises(TenantLinearError) as revoked:
        agents.list_agents(profile)
    assert revoked.value.status == 403


@pytest.mark.parametrize('scope', ['none', 'self', 'department'])
def test_ai_admin_aggregate_lists_cannot_leak_other_dialog_sources(database, monkeypatch, scope):
    import json
    import tenant_ai_state as state
    tenant = str(uuid.uuid4())
    owner, actor = 82001, 82002
    database.execute("INSERT INTO saas_tenants(id,slug,name,status,owner_telegram_id) VALUES(%s,%s,'Synthetic','active',%s)", (tenant, tenant, owner))
    for employee, role in [(owner, 'owner'), (actor, 'admin')]:
        database.execute('INSERT INTO saas_tenant_members(tenant_id,telegram_id,role) VALUES(%s,%s,%s)', (tenant, employee, role))
    settings = {str(actor): {'ai_agents_manage': True, 'access_scopes': {'dialogs': {'view': scope}}}}
    database.execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value) VALUES(%s,'members',%s::jsonb)", (tenant, json.dumps(settings)))
    database.commit()
    factory = lambda: psycopg.connect(database.dsn, row_factory=dict_row)
    monkeypatch.setattr(agents, '_connect', factory)
    monkeypatch.setattr(state, '_connect', factory)
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'true')
    profile = {'tenant_id': tenant, 'telegram_id': actor, 'role': 'owner'}
    for reader in [agents.list_handoffs, agents.list_connect_diagnostics, state.snapshot]:
        with pytest.raises(TenantLinearError) as error:
            reader(profile)
        assert error.value.status == 403
        assert str(error.value) == 'ai_diagnostics_require_unrestricted_dialog_read'


def test_cold_ai_schema_repair_does_not_backfill_new_system_documents(database):
    import tenant_ai_agents_schema as schema
    tenant, agent_id = str(uuid.uuid4()), str(uuid.uuid4())
    database.execute("INSERT INTO saas_tenants(id,slug,name,status,owner_telegram_id) VALUES(%s,%s,'Synthetic','active',83001)", (tenant, tenant))
    database.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role) VALUES(%s,83001,'owner')", (tenant,))
    database.execute("INSERT INTO saas_ai_agents(id,tenant_id,name,created_by) VALUES(%s,%s,'Synthetic system-created',83001)", (agent_id, tenant))
    database.execute('ALTER TABLE saas_ai_agents DROP CONSTRAINT saas_ai_agents_access_owner_fk')
    database.commit()
    with psycopg.connect(database.dsn, row_factory=dict_row) as conn:
        assert not schema._schema_current(conn)
        schema.ensure_schema(conn)
        assert schema._schema_current(conn)
        row = conn.execute('SELECT created_by,access_responsible_id FROM saas_ai_agents WHERE id=%s', (agent_id,)).fetchone()
        assert row == {'created_by': 83001, 'access_responsible_id': None}
