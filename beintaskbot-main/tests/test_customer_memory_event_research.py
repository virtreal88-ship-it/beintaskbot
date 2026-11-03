"""Research existing event and cost primitives on disposable PostgreSQL only.

These tests do not run the support extraction worker, AI, or any customer send.
They do not implement customer-memory automation; they show which existing
mechanisms can be reused and which need an adapter and extra guarantees.
"""
import importlib
import os
import unittest
import uuid
from unittest.mock import patch

fixture=importlib.import_module('test_customer_memory_postgres')


@unittest.skipUnless(fixture.DSN,'CUSTOMER_MEMORY_TEST_DSN is not set')
class EventResearchPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.addClassCleanup(fixture.CustomerMemoryPostgresTests.tearDownClass.__func__,cls)
        fixture.CustomerMemoryPostgresTests.setUpClass.__func__(cls)

    def setUp(self):
        fixture.CustomerMemoryPostgresTests.setUp(self)
        from tenant_platform import _connect
        import tenant_knowledge_live_queue as queue
        import tenant_media_evidence as evidence
        import tenant_ai_spend_budget as spend
        self.env=patch.dict(os.environ,{'SUPPORT_CASES_LIVE_EXTRACTION_ENABLED':'1',
          **{name:'' for name in queue.SCOPE_ENV.values()},
          'AI_TENANT_SPEND_LIMITS_MICROUSD':'{}'})
        self.env.start();self.addCleanup(self.env.stop)
        with _connect() as conn:
            queue.create_schema(conn);evidence.create_schema(conn);spend.ensure_schema(conn)
            with conn.cursor() as cur:
                cur.execute("UPDATE saas_crm_messages SET happened_at=now() WHERE id=%s",(self.message,))
                cur.execute("SELECT kommo_lead_id,channel FROM saas_crm_messages WHERE id=%s",(self.message,))
                row=cur.fetchone();self.lead=row['kommo_lead_id'];self.channel_text=row['channel']
                cur.execute("""INSERT INTO saas_ai_agent_settings(tenant_id,support_case_extraction_enabled,
                  support_case_extraction_mode,support_case_extraction_channels,support_case_extraction_started_at)
                  VALUES(%s,TRUE,'automatic',jsonb_build_array(%s::text),now()-interval '1 day')
                  ON CONFLICT(tenant_id) DO UPDATE SET support_case_extraction_enabled=TRUE,
                    support_case_extraction_mode='automatic',support_case_extraction_channels=EXCLUDED.support_case_extraction_channels,
                    support_case_extraction_started_at=EXCLUDED.support_case_extraction_started_at""",(self.tenant,self.channel))
            conn.commit()

    def enqueue(self,cur,message):
        from tenant_knowledge_live_queue import enqueue
        enqueue(cur,self.tenant,self.channel,self.jid,self.lead,message)

    def state(self,cur):
        cur.execute('''SELECT generation,state,last_message_id FROM saas_knowledge_live_queue
          WHERE tenant_id=%s AND channel_id=%s AND conversation_id=%s''',(self.tenant,self.channel,self.jid))
        return cur.fetchone()

    def create_message(self,cur):
        other=str(uuid.uuid4())
        cur.execute("""INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,body,direction,message_type,happened_at)
          VALUES(%s,%s,%s,%s,%s,'Synthetic','incoming','text',now())""",(other,self.tenant,self.lead,'connect:'+str(uuid.uuid4()),self.channel_text))
        return other

    def test_off_is_noop_and_never_invokes_extractor(self):
        from tenant_platform import _connect
        with patch.dict(os.environ,{'SUPPORT_CASES_LIVE_EXTRACTION_ENABLED':'0'}), \
             patch('tenant_knowledge_live_queue.analyze',side_effect=AssertionError('unexpected provider path')):
            with _connect() as conn:
                with conn.cursor() as cur:
                    self.enqueue(cur,self.message);self.assertIsNone(self.state(cur))
                conn.commit()

    def test_source_and_enqueue_rollback_together(self):
        from tenant_platform import _connect
        with patch('tenant_knowledge_live_queue.analyze',side_effect=AssertionError('unexpected provider path')):
            with _connect() as conn:
                with conn.cursor() as cur:
                    other=self.create_message(cur);self.enqueue(cur,other);self.assertEqual(self.state(cur)['generation'],1)
                conn.rollback()
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute('SELECT 1 FROM saas_crm_messages WHERE id=%s',(other,));self.assertIsNone(cur.fetchone())
                    self.assertIsNone(self.state(cur))

    def test_live_queue_relies_on_upstream_dedup_for_nonadjacent_replay(self):
        from tenant_platform import _connect
        with _connect() as conn:
            with conn.cursor() as cur:
                other=self.create_message(cur)
                self.enqueue(cur,self.message);self.enqueue(cur,self.message)
                self.assertEqual(self.state(cur)['generation'],1)
                self.enqueue(cur,other);self.enqueue(cur,self.message)
                row=self.state(cur)
                self.assertEqual(row['generation'],3)
                self.assertEqual(str(row['last_message_id']),self.message)
            conn.commit()

    def test_new_message_during_running_generation_stays_pending(self):
        from tenant_platform import _connect
        from tenant_knowledge_live_queue import finish
        with _connect() as conn:
            with conn.cursor() as cur:
                other=self.create_message(cur);self.enqueue(cur,self.message)
                cur.execute("UPDATE saas_knowledge_live_queue SET state='running' WHERE tenant_id=%s",(self.tenant,))
                self.enqueue(cur,other);self.assertEqual(self.state(cur)['generation'],2)
            conn.commit()
        finish({'tenant_id':self.tenant,'channel_id':self.channel,'conversation_id':self.jid,'generation':1})
        with _connect() as conn:
            with conn.cursor() as cur:self.assertEqual(self.state(cur)['state'],'pending')

    def test_late_source_can_move_existing_queue_pointer_to_older_event_time(self):
        from tenant_platform import _connect
        with _connect() as conn:
            with conn.cursor() as cur:
                late=self.create_message(cur)
                cur.execute("UPDATE saas_crm_messages SET happened_at=now()-interval '1 hour' WHERE id=%s",(late,))
                self.enqueue(cur,self.message);self.enqueue(cur,late)
                state=self.state(cur)
                self.assertEqual(str(state['last_message_id']),late)
                cur.execute('''SELECT older.happened_at < newer.happened_at AS is_older
                  FROM saas_crm_messages older,saas_crm_messages newer WHERE older.id=%s AND newer.id=%s''',(late,self.message))
                self.assertTrue(cur.fetchone()['is_older'])
            conn.commit()

    def test_media_receipt_changes_fingerprint_but_not_existing_queue_generation(self):
        from tenant_platform import _connect
        from tenant_media_evidence import publication_fingerprint
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE saas_crm_messages SET message_type='audio' WHERE id=%s",(self.message,))
                self.enqueue(cur,self.message)
                cur.execute('SELECT * FROM saas_crm_messages WHERE id=%s',(self.message,));row=cur.fetchone()
                before=publication_fingerprint(cur,self.tenant,row)
                cur.execute("""INSERT INTO saas_media_evidence_receipts(tenant_id,channel_id,external_id,source_hash,media_kind,direction,status,result)
                  VALUES(%s,%s,%s,%s,'audio','incoming','processed','{"transcript":"Synthetic"}'::jsonb)""",
                  (self.tenant,self.channel,row['external_id'],before['source_hash']))
                after=publication_fingerprint(cur,self.tenant,row)
                self.assertEqual(before['source_hash'],after['source_hash'])
                self.assertNotEqual(before['receipt_hash'],after['receipt_hash'])
                self.enqueue(cur,self.message)
                self.assertEqual(self.state(cur)['generation'],1)
            conn.commit()

    def test_spend_reservation_idempotency_is_not_a_dispatch_guard(self):
        from tenant_platform import _connect
        import tenant_ai_spend_budget as spend
        key='synthetic-memory:'+str(uuid.uuid4())
        with _connect() as conn:
            with conn.cursor() as cur:
                self.assertIsNone(spend.reserve(cur,self.tenant,'synthetic-uncapped:'+str(uuid.uuid4()),
                    'synthetic-memory','synthetic-model',100,10,_bounded_prices=(100,200)))
                first=spend.reserve(cur,self.tenant,key,'synthetic-memory','synthetic-model',100,10,track_uncapped=True,_bounded_prices=(100,200))
                repeated=spend.reserve(cur,self.tenant,key,'synthetic-memory','synthetic-model',100,10,track_uncapped=True,_bounded_prices=(100,200))
                self.assertEqual(first,repeated)
            conn.commit()
        spend.settle(self.tenant,key,None,unknown=True,track_uncapped=True)
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT state FROM saas_ai_spend_reservations WHERE tenant_id=%s AND call_key=%s',(self.tenant,key))
                self.assertEqual(cur.fetchone()['state'],'unknown')
                # An unknown reservation reuses the id; the future worker must
                # additionally fence dispatch and must not call a provider.
                again=spend.reserve(cur,self.tenant,key,'synthetic-memory','synthetic-model',100,10,track_uncapped=True,_bounded_prices=(100,200))
                self.assertEqual(again,first)
            conn.rollback()
