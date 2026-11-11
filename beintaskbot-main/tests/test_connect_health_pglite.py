"""Real SQL on isolated PostgreSQL/WASM; no production/network effects."""
import json
import os
import hmac
import hashlib
import re
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
import tenant_connect_health as h

ROOT=Path(__file__).resolve().parents[1]
class DB:
    def __init__(self):
        self.p=subprocess.Popen(['node',str(ROOT/'tests/support_pglite.cjs')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,cwd=ROOT)
    def cursor(self):return Cursor(self)
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def commit(self):pass
    def query(self,sql,params=()):
        numbers=iter(range(1,1000));sql=re.sub('%s',lambda _: '$'+str(next(numbers)),sql)
        self.p.stdin.write(json.dumps({'sql':sql,'params':params},default=str)+'\n');self.p.stdin.flush()
        line=self.p.stdout.readline()
        if not line:raise RuntimeError(self.p.stderr.read())
        result=json.loads(line)
        if 'error' in result:raise RuntimeError(result['error'])
        return result['rows']
    def close(self):self.p.stdin.close();self.p.wait(timeout=10)
class Cursor:
    def __init__(self,db):self.db=db
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def execute(self,sql,params=()):self.rows=self.db.query(sql,params)
    def fetchone(self):return self.rows[0] if self.rows else None
    def fetchall(self):
        for row in self.rows:
            for key,value in row.items():
                if isinstance(value,dict) and '__bytes' in value:row[key]=bytes.fromhex(value['__bytes'])
        return self.rows

@unittest.skipUnless((ROOT/'.support-test-deps/node_modules/@electric-sql/pglite').exists(),'PGlite not installed')
class SQLTests(unittest.TestCase):
    def setUp(self):
        self.db=DB();self.tenant=str(uuid4());self.channel=str(uuid4())
        self.env=patch.dict(os.environ,{'CONNECT_HEALTH_ALERT_TENANT_IDS':self.tenant});self.env.start()
        for sql in ["CREATE TABLE saas_tenants(id UUID PRIMARY KEY,owner_telegram_id BIGINT,name TEXT,status TEXT)",
                    "CREATE TABLE saas_tenant_members(tenant_id UUID,telegram_id BIGINT,role TEXT,active BOOLEAN)",
                    "CREATE TABLE saas_connect_webhook_events(tenant_id UUID,event_id TEXT,PRIMARY KEY(tenant_id,event_id))",
                    "CREATE TABLE saas_tenant_integrations(tenant_id UUID,provider TEXT,status TEXT,secrets BYTEA)",
                    "CREATE TABLE saas_connect_channels(tenant_id UUID,connect_channel_id UUID,active BOOLEAN,name TEXT,PRIMARY KEY(tenant_id,connect_channel_id))"]:self.db.query(sql)
        self.db.query("INSERT INTO saas_tenants VALUES(%s,42,'Test','active')",(self.tenant,))
        self.db.query("INSERT INTO saas_tenant_members VALUES(%s,42,'owner',TRUE),(%s,99,'admin',TRUE)",(self.tenant,self.tenant))
        self.db.query("INSERT INTO saas_connect_channels VALUES(%s,%s,TRUE,'WA')",(self.tenant,self.channel))
        h.ensure_schema(self.db)
        self.db.query("INSERT INTO saas_tenant_integrations VALUES(%s,'connect','connected',decode(%s,'hex'))",(self.tenant,json.dumps({'webhook_secret':'test-only'}).encode().hex()))
        class WebhookError(Exception):
            def __init__(self,code,message,status):self.code=code;self.status=status
        self.WebhookError=WebhookError
        self.patcher=patch.dict(sys.modules,{'tenant_connect_transport':SimpleNamespace(_connect=lambda:self.db,_schema=lambda conn:None, _fernet=lambda:SimpleNamespace(decrypt=lambda value:value), _verify_signature=lambda secret,raw,signature: hmac.compare_digest('sha256='+hmac.new(secret.encode(),raw,hashlib.sha256).hexdigest(),signature), ConnectWebhookError=WebhookError)})
        self.patcher.start()
    def tearDown(self):self.patcher.stop();self.env.stop();self.db.close()
    def observe(self,status,when=None):
        when=when or datetime.now(timezone.utc)-timedelta(minutes=10)
        with self.db.cursor() as cur:h.record(cur,self.tenant,self.channel,{'status':status,'observedAt':when.isoformat()},datetime.now(timezone.utc))
    def count(self):return self.db.query('SELECT * FROM saas_connect_health_notices')
    def test_queue_exact_owner_dedupe_and_recovery(self):
        self.observe('disconnected');h.prepare();h.prepare()
        rows=self.count();self.assertEqual(len(rows),1);self.assertEqual(rows[0]['recipient_id'],42)
        item=h.claim();self.assertTrue(item['send']);self.assertIsNone(h.claim())
        h.finish(item,SimpleNamespace(status='delivered',message_id=123))
        self.observe('connected',datetime.now(timezone.utc));recovered=h.claim()
        self.assertTrue(recovered['send']);self.assertEqual(recovered['kind'],'recovered')
        h.finish(recovered,SimpleNamespace(status='delivered',message_id=124))
        self.assertIsNone(h.claim())
    def test_recover_before_attempt_suppresses_both_notices(self):
        self.observe('disconnected');h.prepare();self.observe('connected',datetime.now(timezone.utc))
        self.assertFalse(h.claim()['send']);self.assertFalse(h.claim()['send'])
    def test_owner_change_does_not_leak_to_old_owner(self):
        self.observe('disconnected');h.prepare()
        self.db.query('UPDATE saas_tenants SET owner_telegram_id=99')
        self.assertFalse(h.claim()['send'])
    def test_inactive_channel_suppresses_delivery(self):
        self.observe('error');h.prepare();self.db.query('UPDATE saas_connect_channels SET active=FALSE')
        self.assertFalse(h.claim()['send'])
    def test_uncertain_send_not_replayed_or_false_recovered(self):
        self.observe('error');h.prepare();h.claim()
        self.db.query("UPDATE saas_connect_health_notices SET updated_at=now()-interval '6 minutes'")
        self.assertIsNone(h.claim());self.assertEqual(self.count()[0]['status'],'unknown')
        self.observe('connected',datetime.now(timezone.utc));self.assertFalse(h.claim()['send'])
    def test_other_tenant_isolated(self):
        self.observe('disconnected');other=str(uuid4())
        self.db.query("INSERT INTO saas_tenants VALUES(%s,777,'Other','active')",(other,))
        self.db.query("INSERT INTO saas_tenant_members VALUES(%s,777,'owner',TRUE)",(other,))
        h.prepare();self.assertEqual(h.claim()['recipient_id'],42)
    def test_recovery_waits_for_inflight_attempt(self):
        self.observe('error');h.prepare();item=h.claim()
        self.observe('connected',datetime.now(timezone.utc))
        self.assertIsNone(h.claim())
        h.finish(item,SimpleNamespace(status='delivered',message_id=10))
        self.assertTrue(h.claim()['send'])
    def event(self,channel=None):
        return {'id':'test-event','type':h.EVENT,'data':{'channelId':channel or self.channel,'status':'disconnected','observedAt':datetime.now(timezone.utc).isoformat()}}
    def signed_ingest(self,event):
        raw=json.dumps(event).encode();signature='sha256='+hmac.new(b'test-only',raw,hashlib.sha256).hexdigest()
        return h.ingest(raw,signature,event)
    def test_signed_ingestion_idempotent_even_with_later_receipt(self):
        event=self.event();self.assertTrue(self.signed_ingest(event))
        event['data']['observedAt']=datetime.now(timezone.utc).isoformat()
        self.assertFalse(self.signed_ingest(event))
        self.assertEqual(len(self.db.query('SELECT * FROM saas_connect_webhook_events')),1)
    def test_wrong_signature_rejected(self):
        event=self.event()
        with self.assertRaises(self.WebhookError) as error:h.ingest(json.dumps(event).encode(),'sha256=bad',event)
        self.assertEqual(error.exception.status,401)
        self.assertEqual(self.db.query('SELECT * FROM saas_connect_health'),[])
    def test_signed_foreign_channel_rejected(self):
        self.assertFalse(self.signed_ingest(self.event(str(uuid4()))))
        self.assertEqual(self.db.query('SELECT * FROM saas_connect_health'),[])
    def test_only_latest_incident_recovery_delivered(self):
        base=datetime.now(timezone.utc)-timedelta(minutes=20)
        self.observe('error',base);h.prepare();first=h.claim()
        h.finish(first,SimpleNamespace(status='delivered',message_id=10))
        self.observe('connected',base+timedelta(minutes=1))
        self.observe('error',base+timedelta(minutes=2));h.prepare()
        # A's pending recovery is obsolete even before B has recovered.
        obsolete=h.claim();self.assertFalse(obsolete['send'])
        second=h.claim();self.assertTrue(second['send'])
        h.finish(second,SimpleNamespace(status='delivered',message_id=11))
        self.observe('connected',base+timedelta(minutes=3))
        # Recreate A's pending state to directly exercise the reviewed sequence:
        # both recoveries pending while latest current state is connected.
        self.db.query("UPDATE saas_connect_health_notices SET status='pending' WHERE incident_id=%s AND kind='recovered'",(first['incident_id'],))
        one=h.claim();two=h.claim()
        outcomes={str(item['incident_id']):item['send'] for item in (one,two)}
        self.assertFalse(outcomes[str(first['incident_id'])])
        self.assertTrue(outcomes[str(second['incident_id'])])
        self.assertIsNone(h.claim())
    def test_bad_signature_never_prepares_schema(self):
        event=self.event()
        with patch.object(h,'ensure_schema') as ensure, patch.object(sys.modules['tenant_connect_transport'],'_schema') as schema:
            with self.assertRaises(self.WebhookError):h.ingest(json.dumps(event).encode(),'sha256=bad',event)
        ensure.assert_not_called();schema.assert_not_called()
    def test_no_verified_owner_no_notice(self):
        self.db.query("UPDATE saas_tenant_members SET active=FALSE WHERE telegram_id=42")
        self.observe('disconnected');h.prepare();self.assertEqual(self.count(),[])

if __name__=='__main__':unittest.main()
