"""Native PostgreSQL first-observation dedupe and per-tenant quota races."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from threading import Barrier, Event, current_thread
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
    for name,value in (('_push_cursor',None),('_push_age_cursor',None),('_push_age_cutoff',None),('_push_selections_since_age',0)):
        monkeypatch.setattr(delivery,name,value)
    dsn = os.environ.get('TEST_POSTGRES_DSN') or os.environ.get('BEINTASKBOT_TEST_POSTGRES_DSN')
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


def test_concurrent_baseline_backfill_enqueues_one_owner_alert(risk_postgres, monkeypatch):
    dsn, schema, tenant = risk_postgres
    monkeypatch.setattr(delivery, '_channels', lambda _profile, _tenant: [])
    _observe(dsn, schema, tenant, 8)  # persisted before risk opt-in
    monkeypatch.setattr(delivery, '_channels', lambda _profile, _tenant: ['internal'])
    with _profiled_connection(dsn, schema) as conn:
        conn.execute('''INSERT INTO saas_proactive_risk_alert_backfills(tenant_id,owner_id)
          VALUES(%s,101)''', (tenant,))
    monkeypatch.setattr(delivery, 'current_finding', lambda _cur, event, _profile: event)

    gate = Barrier(2)
    monkeypatch.setattr(delivery, '_connect', lambda: _profiled_connection(dsn, schema))
    monkeypatch.setattr(delivery, 'ensure_schema', lambda _conn: None)
    def seed(_n):
        gate.wait(timeout=10)
        return delivery.seed_baseline_alerts(limit=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        counts = list(pool.map(seed, range(2)))
    with _profiled_connection(dsn, schema) as conn:
        rows = conn.execute('''SELECT recipient_id,source_id,state FROM saas_proactive_risk_alert_events
          WHERE tenant_id=%s::uuid''', (tenant,)).fetchall()
    assert sorted(counts) == [0, 1]
    assert rows == [{'recipient_id':101,'source_id':'8','state':'pending'}]


def test_parallel_backfill_sources_release_quota_lock_between_rows(risk_postgres, monkeypatch):
    dsn, schema, tenant = risk_postgres
    monkeypatch.setattr(delivery, '_channels', lambda _profile, _tenant: [])
    _observe(dsn, schema, tenant, 21)
    _observe(dsn, schema, tenant, 22)
    monkeypatch.setattr(delivery, '_channels', lambda _profile, _tenant: ['internal'])
    with _profiled_connection(dsn, schema) as conn:
        conn.execute('''INSERT INTO saas_proactive_risk_alert_backfills(tenant_id,owner_id)
          VALUES(%s,101)''', (tenant,))
    monkeypatch.setattr(delivery, 'current_finding', lambda _cur, event, _profile: event)
    monkeypatch.setattr(delivery, '_connect', lambda: _profiled_connection(dsn, schema))
    monkeypatch.setattr(delivery, 'ensure_schema', lambda _conn: None)
    assert delivery.seed_baseline_alerts(limit=2) == 2
    with _profiled_connection(dsn, schema) as conn:
        rows = conn.execute('''SELECT source_id FROM saas_proactive_risk_alert_events
          WHERE tenant_id=%s::uuid ORDER BY source_id''', (tenant,)).fetchall()
    assert rows == [{'source_id':'21'},{'source_id':'22'}]


def test_observer_can_take_next_source_between_backfill_transactions(risk_postgres, monkeypatch):
    dsn, schema, tenant = risk_postgres
    monkeypatch.setattr(delivery, '_channels', lambda _profile, _tenant: [])
    _observe(dsn, schema, tenant, 31)
    _observe(dsn, schema, tenant, 32)
    monkeypatch.setattr(delivery, '_channels', lambda _profile, _tenant: ['internal'])
    with _profiled_connection(dsn, schema) as conn:
        conn.execute('''INSERT INTO saas_proactive_risk_alert_backfills(tenant_id,owner_id)
          VALUES(%s,101)''', (tenant,))
    monkeypatch.setattr(delivery, 'current_finding', lambda _cur, event, _profile: event)
    monkeypatch.setattr(delivery, '_connect', lambda: _profiled_connection(dsn, schema))
    monkeypatch.setattr(delivery, 'ensure_schema', lambda _conn: None)
    first_committed, observer_done = Event(), Event()

    def backfill():
        first = delivery.seed_baseline_alerts(limit=1)
        first_committed.set()
        assert observer_done.wait(10), 'observer must proceed after backfill commits its first source'
        return first + delivery.seed_baseline_alerts(limit=1)

    def observe_next():
        assert first_committed.wait(10), 'first backfill source did not commit'
        _observe(dsn, schema, tenant, 32)
        observer_done.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(backfill);b=pool.submit(observe_next)
        assert a.result(timeout=20)==1
        b.result(timeout=20)
    with _profiled_connection(dsn, schema) as conn:
        counts=conn.execute('''SELECT source_id,count(*) AS n FROM saas_proactive_risk_alert_events
          WHERE tenant_id=%s::uuid GROUP BY source_id ORDER BY source_id''',(tenant,)).fetchall()
    assert counts==[{'source_id':'31','n':1},{'source_id':'32','n':1}]


def test_crm_observer_and_backfill_use_one_quota_then_source_lock_order(risk_postgres, monkeypatch):
    dsn, schema, tenant = risk_postgres
    monkeypatch.setattr(delivery, '_channels', lambda _profile, _tenant: [])
    _observe(dsn, schema, tenant, 41)
    _observe(dsn, schema, tenant, 42)
    monkeypatch.setattr(delivery, '_channels', lambda _profile, _tenant: ['internal'])
    with _profiled_connection(dsn, schema) as conn:
        conn.execute('''INSERT INTO saas_proactive_risk_alert_backfills
          (tenant_id,owner_id,cursor_source_kind,cursor_source_id,cursor_rule_id)
          VALUES(%s,101,'crm_task','41','zzzz')''', (tenant,))
    monkeypatch.setattr(delivery, 'current_finding', lambda _cur, event, _profile: event)
    monkeypatch.setattr(delivery, 'ensure_schema', lambda _conn: None)

    quota_attempt, observer_first_source = Event(), Event()
    original_quota_lock = delivery.lock_daily_quota_transition
    def observed_quota_lock(cur, lock_tenant):
        if current_thread().name.endswith('_1'):
            quota_attempt.set()
        return original_quota_lock(cur, lock_tenant)
    monkeypatch.setattr(delivery, 'lock_daily_quota_transition', observed_quota_lock)

    def observer():
        with _profiled_connection(dsn, schema) as conn:
            conn.execute("SET lock_timeout='8s'")
            with conn.cursor() as cur:
                for source_id in ('41', '42'):
                    replace_source_findings(cur, tenant, 'crm_task', source_id, 'v2',
                        'Observed source', _finding(source_id), source_scope='scope-v1')
                    if source_id == '41':
                        observer_first_source.set()
                        assert quota_attempt.wait(8), 'backfill did not reach quota lock while observer held it'
            conn.commit()

    def backfill():
        assert observer_first_source.wait(8)
        return delivery.seed_baseline_alerts(limit=1)

    def connect_with_lock_timeout():
        conn=_profiled_connection(dsn, schema)
        conn.execute("SET lock_timeout='8s'")
        return conn
    monkeypatch.setattr(delivery, '_connect', connect_with_lock_timeout)
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix='risk-backfill') as pool:
        observer_future=pool.submit(observer)
        backfill_future=pool.submit(backfill)
        observer_future.result(timeout=12)
        assert backfill_future.result(timeout=12)==0
    with _profiled_connection(dsn, schema) as conn:
        counts=conn.execute('''SELECT source_id,count(*) AS n FROM saas_proactive_risk_alert_events
          WHERE tenant_id=%s::uuid GROUP BY source_id ORDER BY source_id''',(tenant,)).fetchall()
    assert counts==[{'source_id':'41','n':1},{'source_id':'42','n':1}]


def test_parallel_sources_cannot_exceed_daily_tenant_limit(risk_postgres, monkeypatch):
    dsn, schema, tenant = risk_postgres
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(lambda source: _observe(dsn, schema, tenant, source), range(100, 124)))
    with _profiled_connection(dsn, schema) as conn:
        counts = conn.execute('''SELECT state,count(*) AS n FROM saas_proactive_risk_alert_events
          WHERE tenant_id=%s::uuid GROUP BY state''', (tenant,)).fetchall()
    states = {row['state']: row['n'] for row in counts}
    assert states.get('pending', 0) == delivery.MAX_RISK_ALERTS_PER_DAY
    assert states.get('deferred', 0) == 14
    assert states.get('suppressed', 0) == 0
    assert sum(states.values()) == 24
    with _profiled_connection(dsn, schema) as conn:
        conn.execute("UPDATE saas_proactive_risk_alert_events SET created_at=now()-interval '2 days',quota_reserved_at=now()-interval '2 days' WHERE tenant_id=%s::uuid AND source_id='100'", (tenant,))
        conn.execute("UPDATE saas_proactive_risk_alert_events SET retry_after=now()-interval '1 minute' WHERE tenant_id=%s::uuid AND source_id='123'",(tenant,))
    monkeypatch.setattr(delivery, '_connect', lambda: _profiled_connection(dsn, schema))
    monkeypatch.setattr(delivery, 'ensure_schema', lambda _conn: None)
    assert delivery.promote_deferred_events(limit=1)==1
    with _profiled_connection(dsn, schema) as conn:
        recent = conn.execute('''SELECT count(*) AS n FROM saas_proactive_risk_alert_events
          WHERE tenant_id=%s::uuid AND quota_reserved_at>=now()-interval '1 day'
            AND state NOT IN ('suppressed','deferred') ''', (tenant,)).fetchone()['n']
    assert recent == delivery.MAX_RISK_ALERTS_PER_DAY


def test_parallel_deferred_promotions_reserve_quota_atomically(risk_postgres, monkeypatch):
    dsn, schema, tenant = risk_postgres
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(lambda source: _observe(dsn, schema, tenant, source), range(200, 209)))
    deferred_ids=[str(uuid4()),str(uuid4())]
    with _profiled_connection(dsn,schema) as conn:
        for event_id,source_id in zip(deferred_ids,('old-backlog-a','old-backlog-b')):
            conn.execute('''INSERT INTO saas_proactive_risk_alert_events
              (id,tenant_id,recipient_id,source_kind,source_id,rule_id,source_scope,source_version,
               alert_fingerprint,state,retry_after,created_at)
              VALUES(%s,%s,101,'crm_task',%s,'service_unavailable','scope','v1','fp','deferred',
                now()-interval '1 minute',now()-interval '2 days')''',(event_id,tenant,source_id))
    monkeypatch.setattr(delivery,'_connect',lambda:_profiled_connection(dsn,schema))
    monkeypatch.setattr(delivery,'ensure_schema',lambda _conn:None)
    gate=Barrier(2)
    def promote(_n):
        gate.wait(timeout=10)
        return delivery.promote_deferred_events(limit=1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        promoted=list(pool.map(promote,range(2)))
    assert sum(promoted)==1
    with _profiled_connection(dsn,schema) as conn:
        states=conn.execute('''SELECT state,count(*) AS n FROM saas_proactive_risk_alert_events
          WHERE tenant_id=%s::uuid GROUP BY state''',(tenant,)).fetchall()
        reserved=conn.execute('''SELECT count(*) AS n FROM saas_proactive_risk_alert_events
          WHERE tenant_id=%s::uuid AND quota_reserved_at>=now()-interval '1 day'
            AND state NOT IN ('deferred','suppressed')''',(tenant,)).fetchone()['n']
    assert {row['state']:row['n'] for row in states}=={'deferred':1,'pending':10}
    assert reserved==delivery.MAX_RISK_ALERTS_PER_DAY


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
    def authorize(cur,item,**_kwargs):
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


def test_concurrent_material_update_enqueues_once(risk_postgres):
    dsn, schema, tenant = risk_postgres
    issue_id = str(uuid4())
    original = str(uuid4())
    with _profiled_connection(dsn, schema) as conn:
        conn.execute('''INSERT INTO saas_proactive_risk_alert_events
          (id,tenant_id,recipient_id,source_kind,source_id,rule_id,source_scope,source_version,alert_fingerprint,state)
          VALUES(%s,%s,101,'linear',%s,'access_control_claim','scope','2026-11-08T10:00:00Z','risk','expanded')''',
          (original,tenant,issue_id))
        conn.execute('''INSERT INTO saas_proactive_risk_alert_deliveries
          (tenant_id,event_id,recipient_id,channel,status) VALUES(%s,%s,101,'internal','delivered')''', (tenant,original))
    before={'id':issue_id,'state':{'id':'todo','name':'Todo'},'assignee':{'id':'a','name':'Ali'}}
    after={'id':issue_id,'state':{'id':'done','name':'Done'},'assignee':{'id':'b','name':'Sara'}}
    def observe(_):
        with _profiled_connection(dsn,schema) as conn:
            with conn.cursor() as cur:
                delivery.record_linear_material_update(cur,tenant,'scope','2026-11-08T10:01:00Z',before,after)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(observe,range(16)))
    with _profiled_connection(dsn,schema) as conn:
        rows=conn.execute("SELECT state FROM saas_proactive_risk_alert_events WHERE event_type='linear_update'").fetchall()
    assert rows == [{'state':'pending'}]


def test_busy_source_backlog_does_not_starve_other_source(risk_postgres, monkeypatch):
    dsn, schema, tenant = risk_postgres
    with _profiled_connection(dsn, schema) as conn:
        for n in range(26):
            source='busy' if n < 25 else 'free'
            event_id=str(uuid4())
            conn.execute('''INSERT INTO saas_proactive_risk_alert_events
              (id,tenant_id,recipient_id,source_kind,source_id,rule_id,source_scope,source_version,alert_fingerprint,state)
              VALUES(%s,%s,101,'crm_task',%s,'risk','scope','v1','fp','expanded')''', (event_id,tenant,source))
            conn.execute('''INSERT INTO saas_proactive_risk_alert_deliveries
              (tenant_id,event_id,recipient_id,channel,endpoint_hash,status,updated_at)
              VALUES(%s,%s,101,'push','device-hash','pending',now()+%s*interval '1 second')''',
              (tenant,event_id,n))
    monkeypatch.setattr(delivery,'_connect',lambda:_profiled_connection(dsn,schema))
    monkeypatch.setattr(delivery,'ensure_schema',lambda _conn:None)
    monkeypatch.setattr(delivery,'_channels',lambda _profile,_tenant:['push'])
    monkeypatch.setattr(delivery,'_authorize_event',lambda cur,item,**_kwargs:
                        (delivery._owner_profile(cur,item['tenant_id'],int(item['recipient_id'])),True))
    with _profiled_connection(dsn,schema) as blocker:
        with blocker.cursor() as cur:
            delivery.lock_source_transition(cur,tenant,'crm_task','busy')
        with ThreadPoolExecutor(max_workers=1) as pool:
            result=pool.submit(delivery.claim_push).result(timeout=10)
        assert result and result['send'] is True and result['source_id']=='free'


def test_rotation_reaches_free_source_after_twenty_five_busy_sources(risk_postgres, monkeypatch):
    dsn, schema, tenant = risk_postgres
    with _profiled_connection(dsn, schema) as conn:
        for n in range(26):
            source=f'busy-{n:02}' if n < 25 else 'free'
            event_id=str(uuid4())
            conn.execute('''INSERT INTO saas_proactive_risk_alert_events
              (id,tenant_id,recipient_id,source_kind,source_id,rule_id,source_scope,source_version,alert_fingerprint,state)
              VALUES(%s,%s,101,'crm_task',%s,'risk','scope','v1','fp','expanded')''', (event_id,tenant,source))
            conn.execute('''INSERT INTO saas_proactive_risk_alert_deliveries
              (tenant_id,event_id,recipient_id,channel,endpoint_hash,status,updated_at)
              VALUES(%s,%s,101,'push','device-hash','pending',now()+%s*interval '1 second')''',
              (tenant,event_id,n))
    monkeypatch.setattr(delivery,'_push_cursor',None)
    monkeypatch.setattr(delivery,'_connect',lambda:_profiled_connection(dsn,schema))
    monkeypatch.setattr(delivery,'ensure_schema',lambda _conn:None)
    monkeypatch.setattr(delivery,'_channels',lambda _profile,_tenant:['push'])
    monkeypatch.setattr(delivery,'_authorize_event',lambda cur,item,**_kwargs:
                        (delivery._owner_profile(cur,item['tenant_id'],int(item['recipient_id'])),True))
    original=delivery.try_lock_source_transition
    checks=[]
    def count(cur, tenant_id, kind, source):
        checks.append(source)
        return original(cur,tenant_id,kind,source)
    monkeypatch.setattr(delivery,'try_lock_source_transition',count)
    with _profiled_connection(dsn,schema) as blocker:
        with blocker.cursor() as cur:
            for n in range(25):
                delivery.lock_source_transition(cur,tenant,'crm_task',f'busy-{n:02}')
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(delivery.claim_push).result(timeout=10) is None
            assert len(checks)==25
            previous=len(checks)
            result=pool.submit(delivery.claim_push).result(timeout=10)
        assert result and result['send'] is True and result['source_id']=='free'
        assert 1 <= len(checks)-previous <= delivery.MAX_PUSH_SOURCE_CHECKS_PER_CLAIM
        assert checks[-1]=='free'


def test_continuous_arrivals_do_not_starve_unprocessed_sources(risk_postgres, monkeypatch):
    dsn, schema, tenant = risk_postgres
    def insert(conn, source, seconds):
        event_id=str(uuid4())
        conn.execute('''INSERT INTO saas_proactive_risk_alert_events
          (id,tenant_id,recipient_id,source_kind,source_id,rule_id,source_scope,source_version,alert_fingerprint,state)
          VALUES(%s,%s,101,'crm_task',%s,'risk','scope','v1','fp','expanded')''', (event_id,tenant,source))
        conn.execute('''INSERT INTO saas_proactive_risk_alert_deliveries
          (tenant_id,event_id,recipient_id,channel,endpoint_hash,status,updated_at)
          VALUES(%s,%s,101,'push','device-hash','pending',now()+%s*interval '1 second')''',
          (tenant,event_id,seconds))
    with _profiled_connection(dsn,schema) as conn:
        for n in range(3): insert(conn,f'old-{n}',n)
    monkeypatch.setattr(delivery,'_push_cursor',None)
    monkeypatch.setattr(delivery,'_connect',lambda:_profiled_connection(dsn,schema))
    monkeypatch.setattr(delivery,'ensure_schema',lambda _conn:None)
    monkeypatch.setattr(delivery,'_channels',lambda _profile,_tenant:['push'])
    monkeypatch.setattr(delivery,'_authorize_event',lambda cur,item,**_kwargs:
                        (delivery._owner_profile(cur,item['tenant_id'],int(item['recipient_id'])),True))
    claimed=[]
    for n in range(3):
        with _profiled_connection(dsn,schema) as conn:
            insert(conn,f'new-{n}',100+n)
        row=delivery.claim_push(); assert row and row['send']
        claimed.append(row['source_id'])
    assert claimed == ['old-0','old-1','old-2']


def test_same_event_devices_not_skipped_by_new_arrivals(risk_postgres, monkeypatch):
    dsn, schema, tenant = risk_postgres
    with _profiled_connection(dsn,schema) as conn:
        conn.execute("INSERT INTO saas_push_devices VALUES('device-second',101,%s)",(b'encrypted',))
        conn.execute("INSERT INTO saas_member_push_devices VALUES(%s,101,'device-second',TRUE)",(tenant,))
        event_id=str(uuid4())
        conn.execute('''INSERT INTO saas_proactive_risk_alert_events
          (id,tenant_id,recipient_id,source_kind,source_id,rule_id,source_scope,source_version,alert_fingerprint,state)
          VALUES(%s,%s,101,'crm_task','old','risk','scope','v1','fp','expanded')''',(event_id,tenant))
        for endpoint in ('device-hash','device-second'):
            conn.execute('''INSERT INTO saas_proactive_risk_alert_deliveries
              (tenant_id,event_id,recipient_id,channel,endpoint_hash,status,updated_at)
              VALUES(%s,%s,101,'push',%s,'pending',now()-interval '1 minute')''',(tenant,event_id,endpoint))
    for name,value in (('_push_cursor',None),('_push_age_cursor',None),('_push_age_cutoff',None),('_push_selections_since_age',0)):
        monkeypatch.setattr(delivery,name,value)
    monkeypatch.setattr(delivery,'_connect',lambda:_profiled_connection(dsn,schema))
    monkeypatch.setattr(delivery,'ensure_schema',lambda _conn:None)
    monkeypatch.setattr(delivery,'_channels',lambda _profile,_tenant:['push'])
    monkeypatch.setattr(delivery,'_authorize_event',lambda cur,item,**_kwargs:
                        (delivery._owner_profile(cur,item['tenant_id'],int(item['recipient_id'])),True))
    first=delivery.claim_push(); assert first and first['source_id']=='old'
    with _profiled_connection(dsn,schema) as conn:
        fresh=str(uuid4())
        conn.execute('''INSERT INTO saas_proactive_risk_alert_events
          (id,tenant_id,recipient_id,source_kind,source_id,rule_id,source_scope,source_version,alert_fingerprint,state)
          VALUES(%s,%s,101,'crm_task','new','risk','scope','v1','fp','expanded')''',(fresh,tenant))
        conn.execute('''INSERT INTO saas_proactive_risk_alert_deliveries
          (tenant_id,event_id,recipient_id,channel,endpoint_hash,status,updated_at)
          VALUES(%s,%s,101,'push','device-hash','pending',now())''',(tenant,fresh))
    second=delivery.claim_push()
    assert second and second['source_id']=='old' and second['endpoint_hash']!=first['endpoint_hash']


def test_temporarily_busy_old_source_revisited_during_continuous_arrivals(risk_postgres, monkeypatch):
    dsn, schema, tenant = risk_postgres
    def insert(conn, source, seconds):
        event_id=str(uuid4())
        conn.execute('''INSERT INTO saas_proactive_risk_alert_events
          (id,tenant_id,recipient_id,source_kind,source_id,rule_id,source_scope,source_version,alert_fingerprint,state)
          VALUES(%s,%s,101,'crm_task',%s,'risk','scope','v1','fp','expanded')''',(event_id,tenant,source))
        conn.execute('''INSERT INTO saas_proactive_risk_alert_deliveries
          (tenant_id,event_id,recipient_id,channel,endpoint_hash,status,updated_at)
          VALUES(%s,%s,101,'push','device-hash','pending',now()+%s*interval '1 second')''',(tenant,event_id,seconds))
    with _profiled_connection(dsn,schema) as conn:
        insert(conn,'old-busy',-60); insert(conn,'first-free',-30)
    for name,value in (('_push_cursor',None),('_push_age_cursor',None),('_push_age_cutoff',None),('_push_selections_since_age',0)):
        monkeypatch.setattr(delivery,name,value)
    monkeypatch.setattr(delivery,'_connect',lambda:_profiled_connection(dsn,schema))
    monkeypatch.setattr(delivery,'ensure_schema',lambda _conn:None)
    monkeypatch.setattr(delivery,'_channels',lambda _profile,_tenant:['push'])
    monkeypatch.setattr(delivery,'_authorize_event',lambda cur,item,**_kwargs:
                        (delivery._owner_profile(cur,item['tenant_id'],int(item['recipient_id'])),True))
    with _profiled_connection(dsn,schema) as blocker:
        with blocker.cursor() as cur: delivery.lock_source_transition(cur,tenant,'crm_task','old-busy')
        with ThreadPoolExecutor(max_workers=1) as pool:
            first=pool.submit(delivery.claim_push).result(timeout=10)
        assert first and first['source_id']=='first-free'
    claimed=[]
    for n in range(delivery.MAX_PUSH_SOURCE_CHECKS_PER_CLAIM+2):
        with _profiled_connection(dsn,schema) as conn: insert(conn,f'new-{n}',100+n)
        item=delivery.claim_push()
        if item: claimed.append(item['source_id'])
        if 'old-busy' in claimed: break
    assert 'old-busy' in claimed
