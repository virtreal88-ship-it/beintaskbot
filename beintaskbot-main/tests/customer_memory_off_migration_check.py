"""Run first, in a fresh disposable PostgreSQL service, never on application data."""
import os
import time
import uuid
from types import SimpleNamespace

from psycopg.conninfo import conninfo_to_dict

dsn = os.environ.get('CUSTOMER_MEMORY_TEST_DSN', '')
params = conninfo_to_dict(dsn)
assert os.environ.get('CUSTOMER_MEMORY_TEST_ISOLATED') == '1'
assert params.get('host') in ('127.0.0.1', 'localhost', '::1')
assert not params.get('service') and not params.get('hostaddr')
assert params.get('dbname') == 'customer_memory_test'
os.environ['DATABASE_URL'] = dsn
os.environ['CUSTOMER_MEMORY_ENABLED'] = '0'
for key in ('PGSERVICE', 'PGSERVICEFILE', 'PGHOSTADDR', 'PGOPTIONS'):
    os.environ.pop(key, None)

from tenant_platform import _connect, _ensure_schema
import tenant_platform_schema
from tenant_browser_session import COOKIE_NAME, TenantBrowserSession
from tenant_account_store import session_is_active

tenant, session = str(uuid.uuid4()), str(uuid.uuid4())
with _connect() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.saas_customer_memory_erasure_requests') AS table_name")
        assert cur.fetchone()['table_name'] is None, 'requires a clean disposable database'
    _ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute("INSERT INTO saas_tenants(id,slug,name,status,owner_telegram_id) VALUES(%s,%s,'Synthetic','active',71)", (tenant, tenant))
        cur.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,display_name,role,active,permissions) VALUES(%s,71,'Owner','owner',TRUE,'[]')", (tenant,))
        cur.execute("INSERT INTO saas_tenant_browser_sessions(id,tenant_id,actor_id,expires_at) VALUES(%s,%s,71,to_timestamp(%s))", (session, tenant, int(time.time()) + 600))
    conn.commit()

# A fresh-process cold path is triggered inside the real sid session checker.
tenant_platform_schema._schema_ready = False
browser = TenantBrowserSession(secret=lambda: 'synthetic-only', member=lambda t, a: {'tenant_id': t, 'telegram_id': a, 'role': 'owner'}, identity_allowed=lambda *_: False, membership_error=ValueError, session_active=session_is_active)
payload = f'{tenant}.71.{int(time.time()) + 600}.{session}'
request = SimpleNamespace(cookies={COOKIE_NAME: f'{payload}.{browser._signature(payload)}'})
assert browser.profile(request)['tenant_id'] == tenant
with _connect() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.saas_customer_memory_erasure_requests') AS table_name")
        assert cur.fetchone()['table_name'] is None, 'OFF sid auth installed memory schema'
        cur.execute("SELECT count(*) AS n FROM pg_trigger WHERE NOT tgisinternal AND tgname LIKE 'customer_memory%'")
        assert cur.fetchone()['n'] == 0, 'OFF sid auth installed memory triggers'

# Simulate a new process on explicit enable; dynamic environment mutation in a
# running process is not a supported activation protocol.
os.environ['CUSTOMER_MEMORY_ENABLED'] = '1'
tenant_platform_schema._schema_ready = False
with _connect() as conn:
    _ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.saas_customer_memory_erasure_requests') AS table_name")
        assert cur.fetchone()['table_name'] is not None, 'enabled process did not install schema'
print('OFF real sid auth skipped memory migration; enabled cold start installed it')
