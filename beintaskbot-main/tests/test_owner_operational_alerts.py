import asyncio
import logging
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import tenant_owner_operational_alerts as alerts


class SignalTests(unittest.TestCase):
    def test_quiet_then_two_confirmed_samples_trigger_only_once(self):
        state, item = alerts.transition({}, 2, 0, 1)
        self.assertIsNone(item)
        state, item = alerts.transition(state, 3, 30, 1)
        self.assertIsNone(item)
        state, item = alerts.transition(state, 4, 90, 1, lambda:'incident')
        self.assertEqual(item, dict(incident='incident',recipient_id=1,kind='down',count=4,send=True))
        state, item = alerts.transition(state, 4, 150, 1)
        self.assertIsNone(item)

    def test_recovery_only_after_down_ack_and_same_owner(self):
        for acknowledged, owner in [(False,1),(True,2),(True,1)]:
            with self.subTest(acknowledged=acknowledged, owner=owner):
                first = dict(incident='incident',recipient=1,acknowledged=acknowledged)
                state, item = alerts.transition(first, 0, 0, owner)
                self.assertIsNone(item)
                state, item = alerts.transition(state, 0, 60, owner)
                self.assertNotIn('incident', state)
                if acknowledged and owner == 1:
                    self.assertEqual(item['kind'], 'up')
                    self.assertIn('не подтверждает', alerts._message('send',item))
                else:
                    self.assertIsNone(item)
                state, item = alerts.transition(state, 0, 120, owner)
                self.assertIsNone(item)

    def test_concurrent_or_delayed_sample_does_not_create_false_incident(self):
        s, i = alerts.transition({}, 10, 1, 1)
        for t, n in [(10,10), (5,0), (300,10), (359,0)]:
            s, i = alerts.transition(s, n, t, 1)
            self.assertIsNone(i)

    def test_count_only_returns_aggregate_from_each_scoped_queue(self):
        class Cursor:
            statements=[]
            results=iter([{'ready':True},{'n':4},{'ready':True},{'n':17}])
            def execute(self, sql, args):
                self.statements.append((sql,args))
            def fetchone(self):
                return next(self.results)
        cur = Cursor()
        self.assertEqual(alerts._count(cur,'tenant','queue'),21)
        select = [stmt for stmt in cur.statements if ' LIMIT ' in stmt[0]]
        self.assertEqual([stmt[1] for stmt in select],[('tenant',21),('tenant',17)])
        self.assertTrue(all('count(*)' in stmt[0] and 'tenant_id=%s::uuid' in stmt[0] for stmt in select))
        for item in alerts.SIGNS:
            rendered = alerts._message(item, {'kind':'down','count':21,'recipient_id':123456789})
            self.assertIn('20+',rendered)
            self.assertNotIn('123456789',rendered)


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_entrypoint_one_signal_failure_does_not_interrupt_others(self):
        log = logging.getLogger(__name__)
        received=[]
        async def send(**kwargs):
            received.append(kwargs)
            return SimpleNamespace(message_id=123)
        bot = SimpleNamespace(send_message=send)
        def step(tenant,signal,now):
            if signal == 'queue':
                raise RuntimeError('sensitive payload')
            return dict(incident='current',recipient_id=1,kind='down',count=4,send=True)
        # Stub import so test needs neither database nor live integration.
        from unittest.mock import Mock
        import sys
        fake_operator=SimpleNamespace(configured_workspace_id=lambda:'tenant')
        with patch.dict(sys.modules,{'tenant_beinsystems_operator_access':fake_operator}), patch.object(
            alerts,'_step',side_effect=step
        ), patch.object(alerts,'_still_owner',side_effect=[False,True]), patch.object(
            alerts,'_confirm',new_callable=Mock
        ) as confirm, self.assertLogs(log,level='WARNING') as logs:
            await alerts.deliver(bot,log)
        self.assertEqual(len(received),1)
        self.assertIn('ошибки синхронизации',received[0]['text'])
        self.assertNotIn('sensitive payload', ''.join(logs.output))
        confirm.assert_called_once()


class PostgresSamples(unittest.TestCase):
    def test_exact_tenant_only_stale_work_and_recent_uncertainty(self):
        import os
        from uuid import uuid4
        dsn = os.environ.get('TEST_POSTGRES_DSN')
        if not dsn:
            self.skipTest('Disposable Postgres fixture only')
        import psycopg
        from psycopg.rows import dict_row
        tenant, other = uuid4(), uuid4()
        with psycopg.connect(dsn, row_factory=dict_row) as conn, conn.cursor() as cur:
            for table in ('saas_chat_send_receipts','saas_crm_sync_outbox',
                          'saas_crm_scalar_outbox','saas_crm_relation_outbox'):
                cur.execute(f"""CREATE TEMP TABLE {table}(
                    tenant_id UUID, state TEXT, created_at TIMESTAMPTZ DEFAULT now(),
                    updated_at TIMESTAMPTZ DEFAULT now(), next_attempt_at TIMESTAMPTZ DEFAULT now(),
                    lease_until TIMESTAMPTZ DEFAULT now()
                ) ON COMMIT DROP""")
            cur.execute("""INSERT INTO saas_chat_send_receipts(tenant_id,state,updated_at)
                VALUES (%s,'unknown',now()),(%s,'accepted',now()),(%s,'sending',now()),
                       (%s,'unknown',now()-interval '31 minutes'),(%s,'unknown',now())""",
                (tenant,tenant,tenant,tenant,other))
            cur.execute("""INSERT INTO saas_crm_sync_outbox(tenant_id,state,created_at,next_attempt_at)
                VALUES (%s,'pending',now()-interval '25 minutes',now()-interval '10 minutes'),
                       (%s,'pending',now()-interval '25 minutes',now()+interval '10 minutes'),
                       (%s,'unknown',now(),now()),(%s,'pending',now()-interval '25 minutes',now()-interval '10 minutes')""",
                (tenant,tenant,tenant,other))
            cur.execute("""INSERT INTO saas_crm_relation_outbox(tenant_id,state,updated_at,next_attempt_at)
                VALUES (%s,'processing',now()-interval '30 minutes',now()-interval '20 minutes'),
                       (%s,'pending',now()-interval '30 minutes',now()+interval '20 minutes')""",
                (tenant,tenant))
            self.assertEqual(alerts._count(cur,str(tenant),'send'),1)
            self.assertEqual(alerts._count(cur,str(tenant),'queue'),2)
            self.assertEqual(alerts._count(cur,str(tenant),'sync'),1)


if __name__ == '__main__':
    unittest.main()
