"""Disposable PostgreSQL integration checks. Refuses non-test database names."""
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import psycopg
from cryptography.fernet import Fernet


def test_url():
    value = os.environ.get('AI_AGENTS_TEST_DATABASE_URL', '').strip()
    parsed = urlsplit(value)
    name = parsed.path.lstrip('/')
    if (parsed.scheme not in {'postgres', 'postgresql'}
            or parsed.hostname not in {'localhost', '127.0.0.1', '::1'}
            or not name.startswith('ai_agents_test_')):
        raise SystemExit('Set AI_AGENTS_TEST_DATABASE_URL to a loopback disposable database named ai_agents_test_*; refusing to run.')
    return value


def reset_schema_caches():
    import tenant_platform_schema
    import tenant_ai_agents_schema
    tenant_platform_schema._schema_ready = False
    tenant_ai_agents_schema._ready = False


def main():
    database_url = test_url()
    os.environ['DATABASE_URL'] = database_url
    os.environ.setdefault('TENANT_ENCRYPTION_KEY', Fernet.generate_key().decode())
    from tenant_ai_agents_schema import ensure_schema

    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    agent_a, agent_b = uuid.uuid4(), uuid.uuid4()
    fingerprint = 'test-' + uuid.uuid4().hex
    duplicate_fingerprint = 'test-' + uuid.uuid4().hex
    try:
        with psycopg.connect(database_url) as conn:
            reset_schema_caches()
            ensure_schema(conn)
        with psycopg.connect(database_url) as conn:
            reset_schema_caches()
            ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('saas_ai_agent_jobs'),to_regclass('saas_ai_agent_runs'),to_regclass('saas_ai_knowledge_chunks')")
                assert all(cur.fetchone()), 'AI Agents schema tables are missing after replay'
                cur.execute("""INSERT INTO saas_tenants(id,slug,name,owner_telegram_id,status) VALUES
                    (%s,%s,'AI Agents Test A',101,'active'),(%s,%s,'AI Agents Test B',202,'active')""",
                    (tenant_a,'ai-agents-test-'+tenant_a.hex[:8],tenant_b,'ai-agents-test-'+tenant_b.hex[:8]))
                cur.execute("""INSERT INTO saas_tenant_members(tenant_id,telegram_id,role) VALUES
                    (%s,101,'owner'),(%s,202,'owner')""", (tenant_a,tenant_b))
                cur.execute('INSERT INTO saas_ai_agent_settings(tenant_id,enabled) VALUES (%s,TRUE),(%s,TRUE)', (tenant_a,tenant_b))
                cur.execute("""INSERT INTO saas_tenant_integrations(tenant_id,provider,status,account_domain)
                    VALUES (%s,'kommo','connected','test-a.kommo.com'),(%s,'kommo','connected','test-b.kommo.com')""",
                    (tenant_a,tenant_b))
                cur.execute("""INSERT INTO saas_ai_agent_test_mode(tenant_id,enabled,emergency_stop,account_domain,
                    company_phone,company_phone_attested,client_phone,channel_id,lead_id,contact_id,talk_id,chat_id,verified)
                    VALUES (%s,TRUE,FALSE,'test-a.kommo.com','+15555550102',TRUE,'+15555550101','wa-a',1,11,'talk-a','chat-a',TRUE),
                      (%s,TRUE,FALSE,'test-b.kommo.com','+15555550102',TRUE,'+15555550101','wa-b',2,22,'talk-b','chat-b',TRUE)""",
                    (tenant_a,tenant_b))
                cur.execute("""INSERT INTO saas_ai_agents(id,tenant_id,name,enabled,created_by) VALUES
                    (%s,%s,'Test Agent A',TRUE,101),(%s,%s,'Test Agent B',TRUE,202)""", (agent_a,tenant_a,agent_b,tenant_b))
                cur.execute("""INSERT INTO saas_tenant_webhook_events(tenant_id,fingerprint,external_id,event_type,payload)
                    VALUES (%s,%s,'same-message','message','{"contact_id":11}'),(%s,%s,'same-message','message','{"contact_id":22}')""",
                    (tenant_a,fingerprint,tenant_b,fingerprint))
                cur.execute('SAVEPOINT duplicate_event_probe')
                try:
                    cur.execute("""INSERT INTO saas_tenant_webhook_events(tenant_id,fingerprint,external_id,event_type,payload)
                        VALUES (%s,%s,'same-message','message','{}')""", (tenant_a,duplicate_fingerprint))
                except psycopg.errors.UniqueViolation:
                    cur.execute('ROLLBACK TO SAVEPOINT duplicate_event_probe')
                    cur.execute('RELEASE SAVEPOINT duplicate_event_probe')
                else:
                    raise AssertionError('Duplicate webhook external ID was accepted within a tenant')
                cur.execute("""INSERT INTO saas_ai_agent_jobs(tenant_id,agent_id,provider,conversation_id,lead_id,talk_id,chat_id,
                    channel_id,event_fingerprint,due_at) VALUES (%s,%s,'kommo','chat-a',1,'talk-a','chat-a','wa-a',%s,now()),
                    (%s,%s,'kommo','chat-b',2,'talk-b','chat-b','wa-b',%s,now())""",
                    (tenant_a,agent_a,fingerprint,tenant_b,agent_b,fingerprint))
                cur.execute("""INSERT INTO saas_ai_agent_runs(tenant_id,id,agent_id,event_fingerprint,revision,model,state)
                    VALUES (%s,%s,%s,%s,1,'test-model','completed')""", (tenant_a,uuid.uuid4(),agent_a,fingerprint))
                cur.execute('SAVEPOINT duplicate_run_probe')
                try:
                    cur.execute("""INSERT INTO saas_ai_agent_runs(tenant_id,id,agent_id,event_fingerprint,revision,model,state)
                        VALUES (%s,%s,%s,%s,1,'test-model','completed')""", (tenant_a,uuid.uuid4(),agent_a,fingerprint))
                except psycopg.errors.UniqueViolation:
                    cur.execute('ROLLBACK TO SAVEPOINT duplicate_run_probe')
                    cur.execute('RELEASE SAVEPOINT duplicate_run_probe')
                else:
                    raise AssertionError('Duplicate AI Agent run was accepted')
            conn.commit()
        print('PASS: initial schema and migration replay')
        print('PASS: tenant-scoped uniqueness and duplicate run rejection')

        # Tenant-filtered reads must not cross the tenant boundary.
        with psycopg.connect(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT count(*) FROM saas_ai_agent_jobs WHERE tenant_id=%s', (tenant_a,))
                assert cur.fetchone()[0] == 1
                cur.execute('SELECT count(*) FROM saas_ai_agent_jobs WHERE tenant_id=%s', (tenant_b,))
                assert cur.fetchone()[0] == 1
                cur.execute("UPDATE saas_ai_agent_jobs SET state='skipped' WHERE tenant_id=%s", (tenant_b,))
            conn.commit()
        print('PASS: tenant-filtered job isolation')

        # Exercise the worker's actual PostgreSQL claim path concurrently.
        from tenant_ai_agent_run_store import claim_next
        with psycopg.connect(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute('UPDATE saas_ai_agent_test_mode SET emergency_stop=TRUE WHERE tenant_id=%s',(tenant_a,))
            conn.commit()
        assert claim_next() is None, 'Emergency stop did not block an already queued job'
        with psycopg.connect(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute('UPDATE saas_ai_agent_test_mode SET emergency_stop=FALSE WHERE tenant_id=%s',(tenant_a,))
            conn.commit()
        print('PASS: Test Mode emergency stop blocks queued work')
        barrier = threading.Barrier(2)
        def competing_claim():
            barrier.wait(timeout=10)
            return claim_next()
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(lambda _index: competing_claim(), range(2)))
        claimed = [item for item in claims if item is not None]
        assert len(claimed) == 1, f'Expected one worker to claim the queued job, got {len(claimed)}'
        print('PASS: concurrent SKIP LOCKED worker claim')
    finally:
        with psycopg.connect(database_url) as conn:
            with conn.cursor() as cur:
                cur.execute('DELETE FROM saas_tenants WHERE id IN (%s,%s)', (tenant_a,tenant_b))


if __name__ == '__main__':
    main()

