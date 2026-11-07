"""Native PostgreSQL first-observation dedupe and per-tenant quota races."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from tenant_proactive_risk_policy import crm_scope, analyze_crm_task
from tenant_proactive_risk_store import replace_source_findings
import tenant_proactive_risk_delivery as delivery
import tenant_proactive_risk_schema as risk_schema


@pytest.fixture
def risk_postgres(monkeypatch):
    dsn = os.environ.get('TEST_POSTGRES_DSN')
    if not dsn:
        pytest.skip('Set TEST_POSTGRES_DSN to an isolated PostgreSQL database for native concurrency checks')
    schema = 'risk_delivery_' + uuid4().hex
    with psycopg.connect(dsn) as conn:
        conn.execute(f'CREATE SCHEMA {schema}')
    with psycopg.connect(dsn, options=f'-c search_path={schema}', row_factory=dict_row) as conn:
        conn.execute('''CREATE TABLE saas_tenants(
          id UUID PRIMARY KEY,name TEXT NOT NULL,owner_telegram_id BIGINT NOT NULL,
          status TEXT NOT NULL,modules JSONB NOT NULL DEFAULT '{}',
          notification_rules JSONB NOT NULL DEFAULT '{}',onboarding JSONB NOT NULL DEFAULT '{}')''')
        conn.execute('''CREATE TABLE saas_tenant_members(
          tenant_id UUID NOT NULL REFERENCES saas_tenants(id),telegram_id BIGINT NOT NULL,
          role TEXT NOT NULL,active BOOLEAN NOT NULL,PRIMARY KEY(tenant_id,telegram_id))''')
        tenant = str(uuid4())
        conn.execute('''INSERT INTO saas_tenants(id,name,owner_telegram_id,status)
          VALUES(%s,'Isolated',101,'active')''', (tenant,))
        conn.execute('INSERT INTO saas_tenant_members VALUES(%s,101,\'owner\',TRUE)', (tenant,))
        with monkeypatch.context() as patcher:
            patcher.setattr(risk_schema, '_ensure_schema', lambda _conn: None)
            patcher.setattr(risk_schema, '_ready', False)
            risk_schema.ensure_schema(conn)
        conn.execute('''CREATE TABLE saas_push_devices(
          endpoint_hash TEXT PRIMARY KEY,owner_telegram_id BIGINT NOT NULL,subscription BYTEA NOT NULL)''')
        conn.execute('''CREATE TABLE saas_member_push_devices(
          tenant_id UUID NOT NULL,telegram_id BIGINT NOT NULL,endpoint_hash TEXT NOT NULL,
          active BOOLEAN NOT NULL DEFAULT TRUE,PRIMARY KEY(tenant_id,telegram_id,endpoint_hash))''')
        conn.execute("INSERT INTO saas_push_devices VALUES('device-hash',101,%s)",(b'encrypted',))
        conn.execute("INSERT INTO saas_member_push_devices VALUES(%s,101,'device-hash',TRUE)",(tenant,))
        conn.commit()

    monkeypatch.setattr(delivery, '_read_tenant_workflow', lambda _cur, _tenant: {})
    monkeypatch.setattr(delivery, '_channels', lambda _profile, _tenant: ['internal'])
    try:
        yield dsn, schema, tenant
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(f'DROP SCHEMA {schema} CASCADE')


def _profiled_connection(dsn, schema):
    return psycopg.connect(dsn, options=f'-c search_path={schema}', row_factory=dict_row)


def _finding(source_id):
    return analyze_crm_task({'tenant_id':'unused','kommo_task_id':int(source_id),
        'text':'Change price without permission','due_at':datetime.now(timezone.utc)-timedelta(days=1),'responsible_id':0,
        'completed':False,'raw':{},'synced_at':None})


def _observe(dsn, schema, tenant, source_id):
    with _profiled_connection(dsn, schema) as conn:
        with conn.cursor() as cur:
            replace_source_findings(cur, tenant, 'crm_task', str(source_id), 'v1',
                'Risk source', _finding(source_id), source_scope='scope-v1')


def test_concurrent_first_observation_enqueues_one_alert(risk_postgres):
    dsn, schema, tenant = risk_postgres
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _i: _observe(dsn, schema, tenant, 7), range(16)))
    with _profiled_connection(dsn, schema) as conn:
        count = conn.execute('''SELECT count(*) FROM saas_proactive_risk_alert_events
          WHERE tenant_id=%s::uuid AND source_id='7' ''', (tenant,)).fetchone()['count']
    assert count == 1


def test_parallel_sources_cannot_exceed_daily_tenant_limit(risk_postgres):
    dsn, schema, tenant = risk_postgres
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(lambda source: _observe(dsn, schema, tenant, source), range(100, 124)))
    with _profiled_connection(dsn, schema) as conn:
        counts = conn.execute('''SELECT state,count(*) AS n FROM saas_proactive_risk_alert_events
          WHERE tenant_id=%s::uuid GROUP BY state''', (tenant,)).fetchall()
    states = {row['state']: row['n'] for row in counts}
    assert states.get('pending', 0) == delivery.MAX_RISK_ALERTS_PER_DAY
    assert states.get('suppressed', 0) == 24 - delivery.MAX_RISK_ALERTS_PER_DAY


def test_two_parallel_push_claims_do_not_upgrade_shared_tenant_lock(risk_postgres, monkeypatch):
    dsn, schema, tenant = risk_postgres
    event_ids=[str(uuid4()),str(uuid4())]
    with _profiled_connection(dsn,schema) as conn:
        for event_id,source_id in zip(event_ids,('claim-a','claim-b')):
            conn.execute('''INSERT INTO saas_proactive_risk_alert_events
              (id,tenant_id,recipient_id,source_kind,source_id,rule_id,source_scope,source_version,alert_fingerprint,state)
              VALUES(%s,%s,101,'crm_task',%s,'service_unavailable','scope','v1','fp','expanded')''',
              (event_id,tenant,source_id))
            conn.execute('''INSERT INTO saas_proactive_risk_alert_deliveries
              (tenant_id,event_id,recipient_id,channel,endpoint_hash,status)
              VALUES(%s,%s,101,'push','device-hash','pending')''',(tenant,event_id))

    gate=Barrier(2)
    def authorize(cur,item,*,channel=None):
        owner=delivery._owner_profile(cur,item['tenant_id'],int(item['recipient_id']))
        gate.wait(timeout=10)
        return owner,True
    monkeypatch.setattr(delivery,'_connect',lambda:_profiled_connection(dsn,schema))
    monkeypatch.setattr(delivery,'ensure_schema',lambda _conn:None)
    monkeypatch.setattr(delivery,'_read_tenant_workflow',lambda _cur,_tenant:{})
    monkeypatch.setattr(delivery,'_channels',lambda _profile,_tenant:['push'])
    monkeypatch.setattr(delivery,'_authorize_event',authorize)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _n:delivery.claim_push(),range(2)))
    assert all(row and row['send'] for row in results)
    assert {str(row['event_id']) for row in results}==set(event_ids)
    with _profiled_connection(dsn,schema) as conn:
        states=conn.execute("SELECT status,count(*) AS n FROM saas_proactive_risk_alert_deliveries GROUP BY status").fetchall()
    assert {row['status']:row['n'] for row in states}=={'sending':2}
