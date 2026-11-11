"""Additive scoped-access schema and one-time, auditable historical ownership.

No membership grants or existing responsible/assignee values are rewritten.
Unambiguous historical documents are assigned to the company's sole active
owner. Missing or conflicting owners leave documents unowned and record issues.
A durable per-table marker prevents later system-created NULL documents from
being accidentally treated as historical records on another startup.
"""

RESOURCE_TABLES = ('saas_crm_native_contacts', 'saas_crm_native_companies',
                   'saas_crm_native_deals', 'saas_crm_native_tasks')


def migrate_department_schema(cur):
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_tenant_departments (
        tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
        id UUID NOT NULL,
        name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 120),
        active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY(tenant_id,id)
    )''')
    cur.execute('''CREATE UNIQUE INDEX IF NOT EXISTS saas_tenant_departments_name_idx
        ON saas_tenant_departments(tenant_id,lower(trim(name))) WHERE active''')
    cur.execute('ALTER TABLE saas_tenant_members ADD COLUMN IF NOT EXISTS department_id UUID NULL')
    cur.execute('''DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='saas_tenant_member_department_fk'
            AND conrelid='saas_tenant_members'::regclass) THEN
            ALTER TABLE saas_tenant_members ADD CONSTRAINT saas_tenant_member_department_fk
                FOREIGN KEY(tenant_id,department_id) REFERENCES saas_tenant_departments(tenant_id,id);
        END IF;
    END $$''')
    cur.execute('''CREATE INDEX IF NOT EXISTS saas_tenant_member_department_idx
        ON saas_tenant_members(tenant_id,department_id,telegram_id)''')


def migrate_scoped_access_schema(cur):
    # Shared transaction/advisory lock covers concurrent startup callers too.
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended('tenant-scoped-access-schema',0))")
    migrate_department_schema(cur)
    migrate_resource_ownership(cur, RESOURCE_TABLES)
    from tenant_dialog_access import migrate_dialog_access_schema
    migrate_dialog_access_schema(cur)


def migrate_resource_ownership(cur, tables):
    """Install ownership for an explicit, code-owned resource table allowlist."""
    if not set(tables) <= set(RESOURCE_TABLES) | {'saas_ai_agents', 'saas_support_tickets'}:
        raise ValueError('unsupported_scoped_ownership_table')
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended('tenant-scoped-access-schema',0))")
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_access_schema_migrations (
        migration_key TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_access_migration_issues (
        tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
        resource_table TEXT NOT NULL,
        resource_id TEXT NOT NULL,
        reason TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY(tenant_id,resource_table,resource_id)
    )''')
    for table in tables:
        cur.execute('SELECT to_regclass(%s) AS relation', (table,))
        if not (cur.fetchone() or {}).get('relation'):
            continue
        cur.execute(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS access_responsible_id BIGINT NULL')
        constraint = table + '_access_owner_fk'
        cur.execute('SELECT 1 FROM pg_constraint WHERE conname=%s AND conrelid=%s::regclass', (constraint, table))
        if not cur.fetchone():
            cur.execute(f'''ALTER TABLE {table} ADD CONSTRAINT {constraint}
                FOREIGN KEY(tenant_id,access_responsible_id)
                REFERENCES saas_tenant_members(tenant_id,telegram_id)''')
        cur.execute(f'''CREATE INDEX IF NOT EXISTS {table}_access_owner_idx
            ON {table}(tenant_id,access_responsible_id)''')
        key = 'historical-access-owner-v1:' + table
        cur.execute('SELECT 1 FROM saas_access_schema_migrations WHERE migration_key=%s', (key,))
        if cur.fetchone():
            continue
        # Lock writes while taking the historical snapshot. Running this once
        # before serving new code prevents old/new semantics from overlapping.
        cur.execute(f'LOCK TABLE {table} IN SHARE ROW EXCLUSIVE MODE')
        cur.execute(f'''WITH owners AS (
            SELECT t.id AS tenant_id, t.owner_telegram_id
            FROM saas_tenants t JOIN saas_tenant_members m ON m.tenant_id=t.id
            WHERE m.role='owner'
            GROUP BY t.id,t.owner_telegram_id
            HAVING count(*)=1 AND bool_and(m.active)
              AND min(m.telegram_id)=t.owner_telegram_id
        ) UPDATE {table} r SET access_responsible_id=o.owner_telegram_id
          FROM owners o WHERE r.tenant_id=o.tenant_id AND r.access_responsible_id IS NULL''')
        cur.execute(f'''INSERT INTO saas_access_migration_issues
            (tenant_id,resource_table,resource_id,reason)
            SELECT tenant_id,%s,id::text,'sole_active_company_owner_required'
            FROM {table} WHERE access_responsible_id IS NULL
            ON CONFLICT DO NOTHING''', (table,))
        cur.execute('INSERT INTO saas_access_schema_migrations(migration_key) VALUES(%s)', (key,))
