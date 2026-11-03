"""Synthetic PostgreSQL counterexamples; no network provider or production DB."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
import tenant_ai_spend_budget as spend
import tenant_voice_test_grant as grant
from tenant_linear_policy import TenantLinearError
from ledger import local_connect,reject_libpq_env

NAMESPACE=uuid.UUID('001e5679-4310-442e-b90e-bbc117076d36')
KIND='realtime_voice_companion_experiment'


def _identity(scope):
    return str(uuid.uuid5(NAMESPACE,scope['tenant_id']+':'+scope['request_id'])), 'voice-experiment-companion:'+scope['request_id']


def reserve_zero_contribution_conflict(cur,scope):
    """Test-only thought experiment; deliberately not production code.

    Locks global then tenant, permits only conflicting rows with current zero
    generic contribution; this does not fence future writers or prove scope.
    """
    s=grant._scope(scope)
    cur.execute('SELECT frozen FROM saas_voice_test_grant WHERE grant_key=%s FOR UPDATE',(grant.GRANT_KEY,))
    if not cur.fetchone():raise ValueError('grant_missing')
    cur.execute('''SELECT * FROM saas_voice_test_hold WHERE request_id=%s AND tenant_id=%s
      AND fingerprint=%s AND state='quarantined' FOR UPDATE''',(s['request_id'],s['tenant_id'],grant._fingerprint(s)))
    voice=cur.fetchone()
    if not voice or voice['tenant_repair_state']!='conflict':raise ValueError('not_exact_quarantine')
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('tenant-ai-spend:'+s['tenant_id'],))
    cur.execute('''SELECT id,call_key,state,actual_microusd,reserved_microusd FROM saas_ai_spend_reservations
      WHERE tenant_id=%s AND (id=%s OR call_key=%s) ORDER BY id FOR UPDATE''',
      (s['tenant_id'],voice['tenant_reservation_id'],'voice-test:'+s['request_id']))
    rows=cur.fetchall()
    if any(r['state']!='released' or r['actual_microusd']!=0 for r in rows):
        raise ValueError('ambiguous_preexisting_contribution')
    amount=max(voice['reserved_microusd'],voice['actual_microusd'] or 0)
    expected_id,key=_identity(s)
    cur.execute('''SELECT id,call_key,kind,model,state,actual_microusd FROM saas_ai_spend_reservations
      WHERE tenant_id=%s AND (id=%s OR call_key=%s) ORDER BY id FOR UPDATE''',(s['tenant_id'],expected_id,key))
    previous=cur.fetchall()
    if previous:
        if (len(previous)!=1 or str(previous[0]['id'])!=expected_id or previous[0]['call_key']!=key or
            previous[0]['kind']!=KIND or previous[0]['model']!=voice['model'] or
            previous[0]['state']!='unknown' or previous[0]['actual_microusd'] is not None):
            raise ValueError('companion_scope_conflict')
        cur.execute('''UPDATE saas_ai_spend_reservations SET reserved_microusd=GREATEST(reserved_microusd,%s)
          WHERE tenant_id=%s AND id=%s RETURNING *''',(amount,s['tenant_id'],expected_id))
    else:
        cur.execute('''INSERT INTO saas_ai_spend_reservations(tenant_id,id,call_key,kind,model,reserved_microusd,state)
          VALUES(%s,%s,%s,%s,%s,%s,'unknown') ON CONFLICT DO NOTHING RETURNING *''',
          (s['tenant_id'],expected_id,key,KIND,voice['model'],amount))
    row=cur.fetchone()
    if not row:raise ValueError('companion_scope_conflict')
    return row

ROOT=Path(__file__).resolve().parents[1]


def new_scope():
    uid=lambda:str(uuid.uuid4())
    return {'request_id':uid(),'tenant_id':uid(),'session_id':uid(),'actor_id':1,'channel_id':uid(),
            'conversation_id':'synthetic-dialog','response_number':1,'executor':'synthetic'}


class CompanionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reject_libpq_env()
        dsn=os.environ.get('VOICE_BUDGET_POC_ADMIN_URL','');p=urlsplit(dsn);parsed=conninfo_to_dict(dsn)
        if (p.scheme not in ('postgres','postgresql') or p.query or p.fragment or p.path!='/postgres' or
            p.hostname not in ('localhost','127.0.0.1','::1') or parsed.get('host') not in ('localhost','127.0.0.1','::1')):
            raise RuntimeError('localhost_admin_only')
        cls.name='test_voice_budget_'+uuid.uuid4().hex
        cls.admin=psycopg.connect(dsn,hostaddr='::1' if p.hostname=='::1' else '127.0.0.1',autocommit=True)
        cls.admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(cls.name)))
        cls.dsn=p._replace(path='/'+cls.name).geturl()
        with local_connect(cls.dsn) as conn:
            spend.ensure_schema(conn)
            conn.execute((ROOT/'voice_budget_poc'/'voice_test_grant.sql').read_text())

    @classmethod
    def tearDownClass(cls):
        cls.admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(cls.name)))
        cls.admin.close()

    def setUp(self):
        with local_connect(self.dsn) as conn:
            conn.execute('TRUNCATE saas_voice_test_hold,saas_voice_test_session,saas_ai_spend_reservations')
            conn.execute('UPDATE saas_voice_test_grant SET enabled=true,frozen=false')

    def quarantined(self,actual=3_000_000):
        s=new_scope()
        with patch.object(grant,'ENFORCEMENT_VERIFIED',True),patch.object(grant,'RECONCILIATION_VERIFIED',True),patch.object(spend,'limit_microusd',return_value=0):
            with local_connect(self.dsn) as conn,conn.cursor() as cur:grant.reserve(cur,s)
            with local_connect(self.dsn) as conn,conn.cursor() as cur:grant.claim(cur,s)
            with local_connect(self.dsn) as conn:
                conn.execute("UPDATE saas_ai_spend_reservations SET kind='other',state='released',actual_microusd=0 WHERE tenant_id=%s",(s['tenant_id'],))
            with local_connect(self.dsn) as conn,conn.cursor() as cur:
                assert grant.note_actual(cur,s,actual)['state']=='quarantined'
        return s

    def test_only_zero_contributing_conflict_gets_one_monotonic_companion(self):
        s=self.quarantined();expected_id,key=_identity(s)
        def admit(_):
            with local_connect(self.dsn) as conn,conn.cursor() as cur:return reserve_zero_contribution_conflict(cur,s)
        with ThreadPoolExecutor(max_workers=12) as workers: rows=list(workers.map(admit,range(20)))
        self.assertEqual({str(r['id']) for r in rows},{expected_id})
        with local_connect(self.dsn) as conn:
            amounts=conn.execute("""SELECT COALESCE(sum(CASE WHEN state IN ('reserved','unknown') THEN reserved_microusd
              ELSE COALESCE(actual_microusd,reserved_microusd) END),0) AS used FROM saas_ai_spend_reservations WHERE tenant_id=%s""",(s['tenant_id'],)).fetchone()
            self.assertEqual(amounts['used'],3_000_000)
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM saas_ai_spend_reservations WHERE tenant_id=%s AND call_key=%s',(s['tenant_id'],key)).fetchone()['n'],1)
            conn.execute('UPDATE saas_voice_test_hold SET actual_microusd=3500000 WHERE request_id=%s',(s['request_id'],))
        with local_connect(self.dsn) as conn,conn.cursor() as cur:
            self.assertEqual(reserve_zero_contribution_conflict(cur,s)['reserved_microusd'],3_500_000)
        # Once existing row begins contributing, no new update is admitted.
        with local_connect(self.dsn) as conn:
            conn.execute("UPDATE saas_ai_spend_reservations SET state='unknown' WHERE tenant_id=%s AND kind='other'",(s['tenant_id'],))
        with self.assertRaisesRegex(ValueError,'ambiguous_preexisting_contribution'):
            with local_connect(self.dsn) as conn,conn.cursor() as cur:reserve_zero_contribution_conflict(cur,s)
        # Crucially, the old companion already remains: future mutation can
        # create duplicate accounting despite the initial zero check.
        with local_connect(self.dsn) as conn:
            total=conn.execute("SELECT sum(reserved_microusd) AS used FROM saas_ai_spend_reservations WHERE tenant_id=%s AND state='unknown'",(s['tenant_id'],)).fetchone()['used']
        self.assertEqual(total,3_500_000+grant.HOLD)

    def test_positive_collision_is_ambiguous_in_two_indistinguishable_histories(self):
        s=self.quarantined()
        with local_connect(self.dsn) as conn:
            conn.execute("UPDATE saas_ai_spend_reservations SET state='unknown' WHERE tenant_id=%s",(s['tenant_id'],))
            observed=conn.execute('SELECT state,kind,reserved_microusd FROM saas_ai_spend_reservations WHERE tenant_id=%s',(s['tenant_id'],)).fetchone()
        with self.assertRaisesRegex(ValueError,'ambiguous_preexisting_contribution'):
            with local_connect(self.dsn) as conn,conn.cursor() as cur:reserve_zero_contribution_conflict(cur,s)
        self.assertEqual(observed,{'state':'unknown','kind':'other','reserved_microusd':grant.HOLD})
        # The identical current DB observation admits two histories: the row's
        # HOLD is either the voice itself or an independent operation. An
        # external oracle gives different correct totals; DB fields do not.
        voice_exposure=3_000_000;existing=observed['reserved_microusd']
        correct_if_same=voice_exposure
        correct_if_unrelated=voice_exposure+existing
        self.assertNotEqual(correct_if_same,correct_if_unrelated)
        self.assertEqual(existing+voice_exposure-correct_if_same,existing)  # full companion may double count
        self.assertEqual(correct_if_unrelated-max(existing,voice_exposure),existing)  # max may undercount

    def test_existing_companion_key_collision_is_untouched(self):
        s=self.quarantined();expected_id,key=_identity(s)
        with local_connect(self.dsn) as conn:
            conn.execute('''INSERT INTO saas_ai_spend_reservations(tenant_id,id,call_key,kind,model,reserved_microusd,state)
              VALUES(%s,%s,%s,'unrelated','model',100,'unknown')''',(s['tenant_id'],str(uuid.uuid4()),key))
        with self.assertRaisesRegex(ValueError,'companion_scope_conflict'):
            with local_connect(self.dsn) as conn,conn.cursor() as cur:reserve_zero_contribution_conflict(cur,s)
        with local_connect(self.dsn) as conn:
            row=conn.execute('SELECT id,kind,reserved_microusd FROM saas_ai_spend_reservations WHERE tenant_id=%s AND call_key=%s',(s['tenant_id'],key)).fetchone()
        self.assertNotEqual(str(row['id']),expected_id)
        self.assertEqual((row['kind'],row['reserved_microusd']),('unrelated',100))

    def test_existing_tenant_cap_consumes_companion_without_false_actual(self):
        s=self.quarantined(actual=3_000_000)
        with local_connect(self.dsn) as conn,conn.cursor() as cur:reserve_zero_contribution_conflict(cur,s)
        with patch.object(spend,'limit_microusd',return_value=3_000_000):
            with self.assertRaises(TenantLinearError):
                with local_connect(self.dsn) as conn,conn.cursor() as cur:
                    spend.reserve(cur,s['tenant_id'],'other-ai','text','synthetic',1,0,track_uncapped=True,_bounded_prices=(1_000_000,1_000_000))
        with local_connect(self.dsn) as conn:
            row=conn.execute("SELECT actual_microusd FROM saas_ai_spend_reservations WHERE tenant_id=%s AND kind='realtime_voice_companion_experiment'",(s['tenant_id'],)).fetchone()
        self.assertIsNone(row['actual_microusd'])


if __name__=='__main__':unittest.main()
