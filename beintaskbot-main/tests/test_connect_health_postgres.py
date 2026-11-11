"""Disposable native PostgreSQL transaction/race and signed cross-stack checks."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from cryptography.fernet import Fernet
import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
import pytest
import tenant_connect_health as health
import tenant_connect_transport as transport

FIXTURE=json.loads((Path(__file__).parent/'fixtures/connect-health-signed.json').read_text())
CHANNEL=FIXTURE['expectedData']['channelId']

@pytest.fixture
def db(monkeypatch):
    dsn=os.environ.get('TEST_POSTGRES_DSN')
    if not dsn:pytest.skip('Disposable localhost PostgreSQL required')
    options=conninfo_to_dict(dsn)
    if options.get('host') not in {'127.0.0.1','localhost','::1'} or options.get('service'):
        raise RuntimeError('Disposable loopback PostgreSQL only')
    schema='connect_health_'+uuid4().hex
    with psycopg.connect(dsn) as conn:conn.execute('CREATE SCHEMA '+schema)
    def connect():return psycopg.connect(dsn,options='-c search_path='+schema,row_factory=dict_row)
    tenant=str(uuid4());fernet=Fernet(Fernet.generate_key())
    monkeypatch.setenv('CONNECT_HEALTH_ALERT_TENANT_IDS',tenant)
    monkeypatch.setenv('CONNECT_HEALTH_ALERTS_ENABLED','false')
    with connect() as conn:
        conn.execute('CREATE TABLE saas_tenants(id UUID PRIMARY KEY,owner_telegram_id BIGINT,name TEXT,status TEXT)')
        conn.execute('CREATE TABLE saas_tenant_members(tenant_id UUID,telegram_id BIGINT,role TEXT,active BOOLEAN)')
        conn.execute('CREATE TABLE saas_connect_channels(tenant_id UUID,connect_channel_id UUID,active BOOLEAN,name TEXT,PRIMARY KEY(tenant_id,connect_channel_id))')
        conn.execute('CREATE TABLE saas_connect_webhook_events(tenant_id UUID,event_id TEXT,PRIMARY KEY(tenant_id,event_id))')
        conn.execute('CREATE TABLE saas_tenant_integrations(tenant_id UUID,provider TEXT,status TEXT,secrets BYTEA)')
        conn.execute("INSERT INTO saas_tenants VALUES(%s,42,'Synthetic','active')",(tenant,))
        conn.execute("INSERT INTO saas_tenant_members VALUES(%s,42,'owner',TRUE),(%s,99,'admin',TRUE)",(tenant,tenant))
        conn.execute("INSERT INTO saas_connect_channels VALUES(%s,%s,TRUE,'Synthetic WA')",(tenant,CHANNEL))
        conn.execute("INSERT INTO saas_tenant_integrations VALUES(%s,'connect','connected',%s)",(tenant,fernet.encrypt(json.dumps({'webhook_secret':FIXTURE['secret'],'api_key':'test-only-api','webhook_id':'00000000-0000-4000-8000-000000000003'}).encode())))
        health.ensure_schema(conn)
    monkeypatch.setattr(transport,'_connect',connect);monkeypatch.setattr(transport,'_fernet',lambda:fernet)
    try:yield SimpleNamespace(connect=connect,tenant=tenant,channel=CHANNEL)
    finally:
        with psycopg.connect(dsn) as conn:conn.execute('DROP SCHEMA '+schema+' CASCADE')


def event(db,status='disconnected',when=None):
    value={'id':str(uuid4()),'type':health.EVENT,'data':{'channelId':db.channel,'status':status,'observedAt':(when or datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat()}}
    raw=json.dumps(value).encode();signature='sha256='+hmac.new(FIXTURE['secret'].encode(),raw,hashlib.sha256).hexdigest()
    return raw,signature,value


def race(fn,count=8):
    barrier=Barrier(count)
    def run(_):barrier.wait(timeout=10);return fn()
    with ThreadPoolExecutor(max_workers=count) as pool:return list(pool.map(run,range(count)))


def rows(db,table):
    with db.connect() as conn:return conn.execute('SELECT * FROM '+table).fetchall()


def test_concurrent_duplicate_receipts_one_incident(db):
    payload=event(db)
    results=race(lambda:transport.ingest(*payload))
    assert sum(results)==1
    assert len(rows(db,'saas_connect_webhook_events'))==1
    assert len(rows(db,'saas_connect_health'))==1


def test_concurrent_prepare_and_claim_send_once(db):
    transport.ingest(*event(db));race(health.prepare)
    assert len(rows(db,'saas_connect_health_notices'))==1
    claims=race(health.claim)
    assert len([x for x in claims if x and x['send']])==1
    assert rows(db,'saas_connect_health_notices')[0]['status']=='sending'


def test_receipt_failure_rolls_back_event_dedupe_and_state(db,monkeypatch):
    payload=event(db);real=health.record
    def broken(*args):real(*args);raise RuntimeError('injected before commit')
    with monkeypatch.context() as m:
        m.setattr(health,'record',broken)
        with pytest.raises(RuntimeError):transport.ingest(*payload)
    assert rows(db,'saas_connect_webhook_events')==[]
    assert rows(db,'saas_connect_health')==[]
    assert transport.ingest(*payload)


def test_outbox_failure_rolls_back_notice_and_queued_marker(db,monkeypatch):
    transport.ingest(*event(db));real=health._queue
    def broken(*args):real(*args);raise RuntimeError('injected after queue')
    with monkeypatch.context() as m:
        m.setattr(health,'_queue',broken)
        with pytest.raises(RuntimeError):health.prepare()
    assert rows(db,'saas_connect_health_notices')==[]
    assert not rows(db,'saas_connect_health')[0]['state'].get('alertQueued')
    health.prepare();assert len(rows(db,'saas_connect_health_notices'))==1


def test_concurrent_out_of_order_observations_keep_latest(db):
    base=datetime.now(timezone.utc)-timedelta(minutes=10)
    payloads=[event(db,'disconnected',base),event(db,'connected',base+timedelta(seconds=1))]*4
    with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(lambda p:transport.ingest(*p),payloads))
    assert rows(db,'saas_connect_health')[0]['state']['status']=='connected'
    health.prepare();assert rows(db,'saas_connect_health_notices')==[]


def test_signed_producer_to_crm_to_mock_telegram(db,monkeypatch):
    receipt=datetime.fromisoformat(FIXTURE['receiptTime'].replace('Z','+00:00'))
    class Clock(datetime):
        now_value=receipt
        @classmethod
        def now(cls,tz=None):return cls.now_value
    monkeypatch.setattr(health,'datetime',Clock)
    raw=FIXTURE['rawBody'].encode();payload=json.loads(raw)
    assert transport.ingest(raw,FIXTURE['signature'],payload)
    assert not transport.ingest(FIXTURE['retry']['rawBody'].encode(),FIXTURE['retry']['headers']['x-connect-signature-256'],payload)
    health.prepare();assert rows(db,'saas_connect_health_notices')==[]
    Clock.now_value=receipt+timedelta(seconds=91)
    monkeypatch.setenv('CONNECT_HEALTH_ALERTS_ENABLED','true')
    bot=AsyncMock();bot.send_message.return_value=SimpleNamespace(message_id=123)
    asyncio.run(health.deliver(bot,Mock()))
    assert bot.send_message.await_count==1
    assert bot.send_message.call_args.kwargs['chat_id']==42
    assert rows(db,'saas_connect_health_notices')[0]['status']=='delivered'
    asyncio.run(health.deliver(bot,Mock()))
    assert bot.send_message.await_count==1


def test_missing_schema_fails_closed_without_serving_ddl(db):
    with db.connect() as conn:conn.execute('DROP TABLE saas_connect_health_notices')
    with pytest.raises(RuntimeError,match='startup schema'):transport.ingest(*event(db))
    with db.connect() as conn:
        assert conn.execute("SELECT to_regclass('saas_connect_health_notices') AS name").fetchone()['name'] is None
    assert rows(db,'saas_connect_webhook_events')==[]


def test_schema_migration_failure_rolls_back_and_readiness_does_not_cache(db):
    with db.connect() as conn:
        conn.execute('DROP TABLE saas_connect_health_notices');conn.execute('DROP TABLE saas_connect_health')
    with pytest.raises(RuntimeError):
        with db.connect() as conn:
            health.ensure_schema(conn);health.require_schema(conn)
            raise RuntimeError('migration aborted')
    with db.connect() as conn:
        with pytest.raises(RuntimeError,match='startup schema'):health.require_schema(conn)
    with db.connect() as conn:health.ensure_schema(conn)
    with db.connect() as conn:health.require_schema(conn)


def test_wrong_tenant_never_prepared(db,monkeypatch):
    transport.ingest(*event(db))
    monkeypatch.setenv('CONNECT_HEALTH_ALERT_TENANT_IDS',str(uuid4()))
    health.prepare()
    assert rows(db,'saas_connect_health_notices')==[]


def test_removed_allowlist_rejects_already_pending_notice(db,monkeypatch):
    transport.ingest(*event(db));health.prepare()
    monkeypatch.setenv('CONNECT_HEALTH_ALERT_TENANT_IDS','')
    item=health.claim()
    assert item and item['send'] is False
    assert rows(db,'saas_connect_health_notices')[0]['status']=='skipped'


def test_malformed_allowlist_fails_closed_for_entire_list(db,monkeypatch):
    transport.ingest(*event(db));health.prepare()
    monkeypatch.setenv('CONNECT_HEALTH_ALERT_TENANT_IDS',db.tenant+',not-a-uuid')
    assert health.allowed_tenants()==frozenset()
    assert health.claim()['send'] is False


CALLBACK='https://crm.example.test/api/platform/connect/webhook'


def rollout_mock(monkeypatch,db,*,status='connected',before_status=None):
    calls=[];events=['message.inbound.received','message.outbound.observed']
    def request(key,method,path,body=None):
        assert key=='test-only-api'
        calls.append((method,path,body))
        if method=='GET' and path=='/v1/webhooks':
            return {'data':[{'id':'00000000-0000-4000-8000-000000000003','url':CALLBACK,'active':True,'events':list(events)}]}
        if method=='PATCH' and path=='/v1/webhooks/00000000-0000-4000-8000-000000000003':
            assert set(body)=={'events'};events[:]=body['events'];return {}
        if method=='GET' and path=='/v1/channels/'+db.channel+'/status':
            if before_status:before_status()
            return {'id':db.channel,'status':status}
        raise AssertionError('Unapproved test request '+method+' '+path)
    monkeypatch.setattr(transport,'_request',request)
    return calls


def test_scoped_reconcile_audit_is_read_only_and_apply_idempotent(db,monkeypatch):
    import tenant_connect_health_rollout as rollout
    calls=rollout_mock(monkeypatch,db)
    audit=rollout.reconcile(db.tenant,CALLBACK)
    assert not audit['health_subscription_verified'] and not audit['seeded_channels']
    assert calls==[('GET','/v1/webhooks',None)]
    assert rows(db,'saas_connect_health')==[]
    first=rollout.reconcile(db.tenant,CALLBACK,apply=True)
    second=rollout.reconcile(db.tenant,CALLBACK,apply=True)
    assert first['seeded_channels']==second['seeded_channels']==1
    assert sum(method=='PATCH' for method,_,_ in calls)==1
    assert rows(db,'saas_connect_health')[0]['state']['status']=='connected'
    assert rows(db,'saas_connect_health_notices')==[]
    assert not first['message_flow_verified']


def test_seed_ambiguous_provider_error_is_unknown_never_outage(db,monkeypatch):
    import tenant_connect_health_rollout as rollout
    rollout_mock(monkeypatch,db,status='error')
    report=rollout.reconcile(db.tenant,CALLBACK,apply=True)
    assert report['provider_reported_statuses']==['unknown']
    health.prepare();assert rows(db,'saas_connect_health_notices')==[]


def test_seed_preserves_newer_signed_event_during_provider_poll(db,monkeypatch):
    import tenant_connect_health_rollout as rollout
    def new_event():transport.ingest(*event(db,'disconnected',datetime.now(timezone.utc)))
    rollout_mock(monkeypatch,db,status='connected',before_status=new_event)
    rollout.reconcile(db.tenant,CALLBACK,apply=True)
    assert rows(db,'saas_connect_health')[0]['state']['status']=='disconnected'


def test_rollout_rejects_wrong_tenant_or_live_delivery_before_external_calls(db,monkeypatch):
    import tenant_connect_health_rollout as rollout
    calls=rollout_mock(monkeypatch,db)
    with pytest.raises(RuntimeError,match='allowlisted'):rollout.reconcile(str(uuid4()),CALLBACK,apply=True)
    monkeypatch.setenv('CONNECT_HEALTH_ALERTS_ENABLED','true')
    with pytest.raises(RuntimeError,match='disabled'):rollout.reconcile(db.tenant,CALLBACK,apply=True)
    assert calls==[]


def test_rollout_requires_exact_active_owner_binding(db,monkeypatch):
    import tenant_connect_health_rollout as rollout
    calls=rollout_mock(monkeypatch,db)
    with db.connect() as conn:conn.execute('UPDATE saas_tenant_members SET active=FALSE WHERE telegram_id=42')
    with pytest.raises(RuntimeError,match='owner'):rollout.reconcile(db.tenant,CALLBACK,apply=True)
    assert calls==[]


def test_rollout_callback_mismatch_never_patches_or_seeds(db,monkeypatch):
    import tenant_connect_health_rollout as rollout
    calls=rollout_mock(monkeypatch,db)
    with pytest.raises(RuntimeError,match='callback'):rollout.reconcile(db.tenant,'https://other.example.test/api/platform/connect/webhook',apply=True)
    assert all(method=='GET' for method,_,_ in calls)
    assert rows(db,'saas_connect_health')==[]


def test_removed_scope_cannot_prepare_recovery_from_ingestion(db,monkeypatch):
    transport.ingest(*event(db));health.prepare();item=health.claim()
    health.finish(item,SimpleNamespace(status='delivered',message_id=123))
    monkeypatch.setenv('CONNECT_HEALTH_ALERT_TENANT_IDS',str(uuid4()))
    transport.ingest(*event(db,'connected',datetime.now(timezone.utc)))
    assert len(rows(db,'saas_connect_health_notices'))==1


def clock_at(monkeypatch,when):
    class Clock(datetime):
        now_value=when
        @classmethod
        def now(cls,tz=None):return cls.now_value
    monkeypatch.setattr(health,'datetime',Clock)
    return Clock


@pytest.mark.parametrize('skip_before_new_bad',[False,True])
def test_unknown_then_bad_rearms_unsent_alert_with_fresh_debounce(db,monkeypatch,skip_before_new_bad):
    base=datetime.now(timezone.utc);clock=clock_at(monkeypatch,base)
    transport.ingest(*event(db,'disconnected',base))
    clock.now_value=base+timedelta(seconds=91);health.prepare()
    first=rows(db,'saas_connect_health_notices')[0]
    clock.now_value=base+timedelta(seconds=92)
    transport.ingest(*event(db,'unknown',clock.now_value))
    if skip_before_new_bad:assert health.claim()['send'] is False
    clock.now_value=base+timedelta(seconds=93)
    transport.ingest(*event(db,'disconnected',clock.now_value))
    state=rows(db,'saas_connect_health')[0]['state']
    assert state['incidentId']!=str(first['incident_id']) and state['incidentGeneration']==2
    if not skip_before_new_bad:assert health.claim()['send'] is False
    health.prepare();assert len(rows(db,'saas_connect_health_notices'))==1
    clock.now_value=base+timedelta(seconds=182);health.prepare()
    assert len(rows(db,'saas_connect_health_notices'))==1
    clock.now_value=base+timedelta(seconds=183);health.prepare()
    item=health.claim();assert item['send'] and str(item['incident_id'])==state['incidentId']
    health.finish(item,SimpleNamespace(status='delivered',message_id=123))
    health.prepare();assert health.claim() is None


@pytest.mark.parametrize('outcome',['sending','unknown','delivered'])
def test_unknown_preserves_attempted_incident_and_recovery_semantics(db,monkeypatch,outcome):
    base=datetime.now(timezone.utc);clock=clock_at(monkeypatch,base)
    transport.ingest(*event(db,'error',base-timedelta(minutes=10)));health.prepare();item=health.claim()
    assert item['send']
    if outcome!='sending':health.finish(item,SimpleNamespace(status=outcome,message_id=123 if outcome=='delivered' else None))
    transport.ingest(*event(db,'unknown',base))
    clock.now_value=base+timedelta(seconds=1)
    transport.ingest(*event(db,'error',clock.now_value))
    state=rows(db,'saas_connect_health')[0]['state']
    assert state['incidentId']==str(item['incident_id']) and state['alertAttempted'] and state['alertQueued']
    clock.now_value=base+timedelta(minutes=10);health.prepare()
    assert len(rows(db,'saas_connect_health_notices'))==1
    transport.ingest(*event(db,'connected',clock.now_value))
    if outcome=='sending':
        assert health.claim() is None
        health.finish(item,SimpleNamespace(status='delivered',message_id=123))
    recovery=health.claim()
    assert recovery['kind']=='recovered' and recovery['send']==(outcome!='unknown')


def test_claim_rechecks_debounce_and_rotates_pending_without_starvation(db,monkeypatch):
    base=datetime.now(timezone.utc);clock=clock_at(monkeypatch,base)
    transport.ingest(*event(db,'error',base-timedelta(minutes=10)));health.prepare()
    with db.connect() as conn:
        conn.execute("UPDATE saas_connect_health SET state=jsonb_set(state,'{badSince}',to_jsonb(%s::text))",(base.isoformat(),))
        channel=str(uuid4())
        conn.execute("INSERT INTO saas_connect_channels VALUES(%s,%s,TRUE,'Second')",(db.tenant,channel))
    transport.ingest(*event(SimpleNamespace(channel=channel),'error',base-timedelta(minutes=10)));health.prepare()
    deferred=health.claim();assert deferred['deferred'] and not deferred['send']
    other=health.claim();assert other['send'] and str(other['channel_id'])==channel
    clock.now_value=base+timedelta(seconds=90)
    original=health.claim();assert original['send'] and str(original['channel_id'])==db.channel


def test_distinct_equal_time_events_are_ambiguous_not_duplicate_identity(db,monkeypatch):
    base=datetime.now(timezone.utc);clock=clock_at(monkeypatch,base)
    first=event(db,'disconnected',base);second=event(db,'connected',base)
    assert transport.ingest(*first);assert transport.ingest(*second)
    assert not transport.ingest(*second)
    assert len(rows(db,'saas_connect_webhook_events'))==2
    state=rows(db,'saas_connect_health')[0]['state']
    assert state['status']=='unknown' and state['ambiguousTimestamp']
    clock.now_value=base+timedelta(minutes=10);health.prepare()
    assert rows(db,'saas_connect_health_notices')==[]
    transport.ingest(*event(db,'connected',clock.now_value))
    assert rows(db,'saas_connect_health')[0]['state']['status']=='connected'
