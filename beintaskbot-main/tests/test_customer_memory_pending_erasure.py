"""Owner erasure requests on disposable PostgreSQL; transport is not enabled."""
import importlib
import os
import threading
import time
import unittest
import uuid
from unittest.mock import patch

fixture=importlib.import_module('test_customer_memory_postgres')


@unittest.skipUnless(fixture.DSN,'CUSTOMER_MEMORY_TEST_DSN is not set')
class PendingErasureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.addClassCleanup(fixture.CustomerMemoryPostgresTests.tearDownClass.__func__,cls)
        fixture.CustomerMemoryPostgresTests.setUpClass.__func__(cls)

    link=fixture.CustomerMemoryPostgresTests.link
    fact=fixture.CustomerMemoryPostgresTests.fact
    edit=fixture.CustomerMemoryPostgresTests.edit

    def setUp(self):fixture.CustomerMemoryPostgresTests.setUp(self)

    def assert_backend_blocked(self,waiter,holder,done):
        from tenant_platform import _connect
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            if done.wait(.01):break
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute('SELECT %s=ANY(pg_blocking_pids(%s)) AS blocked',(holder,waiter))
                    if cur.fetchone()['blocked']:return
        self.fail('erasure did not wait on the expected source reader')

    def test_atomic_pending_retry_and_status_when_flags_off(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        link=self.link();fact=self.fact(link,value='Synthetic erased value')
        self.edit(fact['id'],1,'Synthetic revised value')
        result=self.memory.change_fact(self.profile,self.contact,fact['id'],2,'delete')
        self.assertEqual(result['status'],'pending');self.assertTrue(result['request_id'])
        self.assertEqual(self.memory.read(self.profile,self.contact)['facts'],[])
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT count(*) AS n FROM saas_customer_memory_facts WHERE id=%s',(fact['id'],));self.assertEqual(cur.fetchone()['n'],0)
                cur.execute('SELECT count(*) AS n FROM saas_customer_memory_fact_versions WHERE fact_id=%s',(fact['id'],));self.assertEqual(cur.fetchone()['n'],0)
                cur.execute('''SELECT state,completed_at FROM saas_customer_memory_erasure_requests WHERE id=%s''',(result['request_id'],))
                self.assertEqual(cur.fetchone(),{'state':'pending','completed_at':None})
                cur.execute("UPDATE saas_tenant_workflow_policies SET value='{}' WHERE tenant_id=%s AND policy_key='customer_memory'",(self.tenant,))
            conn.commit()
        with patch.dict(os.environ,{'CUSTOMER_MEMORY_ENABLED':'0'}):
            with patch.object(self.memory,'_ensure_schema',side_effect=AssertionError('recovery must not run migrations')):
                self.assertEqual(self.memory.change_fact(self.profile,self.contact,fact['id'],2,'delete'),result)
                status=self.memory.erasure_status(self.profile,self.contact,result['request_id'])
                self.assertEqual(status['status'],'pending');self.assertIsNone(status['completed_at'])
                self.assertEqual(self.memory.erasure_statuses(self.profile,self.contact)['requests'][0]['request_id'],result['request_id'])
            with self.assertRaises(TenantLinearError):self.memory.read(self.profile,self.contact)
        # Status metadata contains no customer values or source quote.
        self.assertNotIn('Synthetic',repr(status))

    def test_status_allows_current_owner_but_not_other_tenant_or_inactive_owner(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        link=self.link();fact=self.fact(link);result=self.memory.change_fact(self.profile,self.contact,fact['id'],1,'delete')
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,display_name,role,active,permissions) VALUES(%s,73,'Other','owner',TRUE,'[]')",(self.tenant,))
            conn.commit()
        successor={'tenant_id':self.tenant,'telegram_id':73}
        self.assertEqual(self.memory.erasure_status(successor,self.contact,result['request_id'])['status'],'pending')
        other={'tenant_id':self.other,'telegram_id':72}
        with self.assertRaises(TenantLinearError):self.memory.erasure_status(other,self.contact,result['request_id'])
        self.assertEqual(self.memory.erasure_statuses(other,self.contact)['requests'],[])
        with _connect() as conn:
            with conn.cursor() as cur:cur.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s AND telegram_id=71',(self.tenant,))
            conn.commit()
        with self.assertRaises(TenantLinearError):self.memory.erasure_status(self.profile,self.contact,result['request_id'])

    def test_pending_survives_contact_and_tenant_cascade_without_old_owner_access(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        link=self.link();fact=self.fact(link);result=self.memory.change_fact(self.profile,self.contact,fact['id'],1,'delete')
        with _connect() as conn:
            with conn.cursor() as cur:cur.execute('DELETE FROM saas_crm_native_contacts WHERE id=%s',(self.contact,))
            conn.commit()
        self.assertEqual(self.memory.erasure_status(self.profile,self.contact,result['request_id'])['status'],'pending')
        with _connect() as conn:
            with conn.cursor() as cur:cur.execute('DELETE FROM saas_tenants WHERE id=%s',(self.tenant,))
            conn.commit()
        with self.assertRaises(TenantLinearError):self.memory.erasure_status(self.profile,self.contact,result['request_id'])
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT state FROM saas_customer_memory_erasure_requests WHERE id=%s',(result['request_id'],))
                self.assertEqual(cur.fetchone()['state'],'pending')

    def test_erased_ids_cannot_be_reused_by_new_source_or_contact(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        link=self.link();request_id=str(uuid.uuid4());fact=self.fact(link,request_id=request_id)
        self.memory.change_fact(self.profile,self.contact,fact['id'],1,'delete')
        other_jid='994709876543@s.whatsapp.net';other_message=str(uuid.uuid4())
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,conversation_kind) VALUES(%s,%s,%s,'contact') RETURNING lead_id",(self.tenant,self.channel,other_jid))
                lead=cur.fetchone()['lead_id']
                cur.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,body,direction,message_type,happened_at) VALUES(%s,%s,%s,%s,%s,'New synthetic evidence','incoming','text',now())",(other_message,self.tenant,lead,'connect:'+str(uuid.uuid4()),'connect:'+self.channel))
            conn.commit()
        other=self.memory.confirm_link(self.profile,self.contact2,self.channel,other_jid,True)['id']
        data={'request_id':request_id,'link_id':other,'source_message_id':other_message,'category':'profile',
              'value':'Another synthetic fact','source_kind':'customer_statement','reliability':'needs_review',
              'evidence_quote':'New synthetic evidence','evidence_confirmed':True}
        with self.assertRaises(TenantLinearError) as caught:self.memory.add_fact(self.profile,self.contact2,data)
        self.assertEqual(caught.exception.status,409)
        self.assertEqual(self.memory.change_fact(self.profile,self.contact,fact['id'],1,'delete')['status'],'pending')
        self.assertEqual(self.memory.read(self.profile,self.contact2)['facts'],[])

    def test_linked_group_reserves_each_erased_fact_identifier(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        link=self.link();first_request=str(uuid.uuid4());second_request=str(uuid.uuid4())
        first=self.fact(link,request_id=first_request,value='Same synthetic fact')
        other=str(uuid.uuid4())
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT kommo_lead_id,channel FROM saas_crm_messages WHERE id=%s',(self.message,));src=cur.fetchone()
                cur.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,body,direction,message_type) VALUES(%s,%s,%s,%s,%s,'Second synthetic statement','incoming','text')",(other,self.tenant,src['kommo_lead_id'],'connect:'+str(uuid.uuid4()),src['channel']))
            conn.commit()
        second=self.fact(link,source_message_id=other,request_id=second_request,value='Same synthetic fact',group_fact_id=first['id'],group_version=1)
        self.memory.change_fact(self.profile,self.contact,first['id'],1,'delete')
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT fact_id FROM saas_customer_memory_erased_fact_ids WHERE tenant_id=%s AND fact_id=ANY(%s::uuid[])',(self.tenant,[first['id'],second['id']]))
                self.assertEqual({str(r['fact_id']) for r in cur.fetchall()},{first['id'],second['id']})
        with self.assertRaises(TenantLinearError):self.fact(link,request_id=second_request)

    def test_failure_before_commit_rolls_back_purge_and_pending(self):
        from tenant_platform import _connect
        link=self.link();fact=self.fact(link)
        with patch.object(self.memory,'_audit',side_effect=RuntimeError('synthetic rollback')):
            with self.assertRaises(RuntimeError):self.memory.change_fact(self.profile,self.contact,fact['id'],1,'delete')
        self.assertEqual(self.memory.read(self.profile,self.contact)['facts'][0]['id'],fact['id'])
        self.assertEqual(self.memory.erasure_statuses(self.profile,self.contact)['requests'],[])
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT 1 FROM saas_customer_memory_erased_fact_ids WHERE tenant_id=%s AND fact_id=%s',(self.tenant,fact['id']))
                self.assertIsNone(cur.fetchone())
        self.assertEqual(self.memory.change_fact(self.profile,self.contact,fact['id'],1,'delete')['status'],'pending')

    def test_status_history_is_paginated_without_values(self):
        from tenant_platform import _connect
        ids=[]
        with _connect() as conn:
            with conn.cursor() as cur:
                for _ in range(27):
                    request=str(uuid.uuid4());ids.append(request)
                    cur.execute('''INSERT INTO saas_customer_memory_erasure_requests
                      (id,tenant_id,contact_id,fact_id,fact_version,requested_by,state)
                      VALUES(%s,%s,%s,%s,1,71,'pending')''',(request,self.tenant,self.contact,str(uuid.uuid4())))
            conn.commit()
        first=self.memory.erasure_statuses(self.profile,self.contact)
        self.assertEqual(len(first['requests']),25);self.assertTrue(first['next_before'])
        second=self.memory.erasure_statuses(self.profile,self.contact,first['next_before'])
        self.assertEqual(len(second['requests']),2);self.assertIsNone(second['next_before'])
        self.assertEqual({r['request_id'] for r in first['requests']+second['requests']},set(ids))
        self.assertTrue(all(set(row)=={'request_id','status','requested_at','completed_at'} for row in first['requests']+second['requests']))

    def test_deleted_source_replay_cannot_be_rebound_to_another_contact(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        link=self.link();self.fact(link)
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT kommo_lead_id,external_id,channel FROM saas_crm_messages WHERE id=%s',(self.message,));src=cur.fetchone()
                cur.execute('DELETE FROM saas_crm_messages WHERE id=%s',(self.message,))
            conn.commit()
        self.memory.revoke_link(self.profile,self.contact,link,1)
        new_link=self.memory.confirm_link(self.profile,self.contact2,self.channel,self.jid,True)['id']
        replay=str(uuid.uuid4())
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,body,direction,message_type) VALUES(%s,%s,%s,%s,%s,'Synthetic customer statement','incoming','text')",
                  (replay,self.tenant,src['kommo_lead_id'],src['external_id'],src['channel']))
            conn.commit()
        data={'request_id':str(uuid.uuid4()),'link_id':new_link,'source_message_id':replay,
              'category':'feature','value':'Synthetic replay','source_kind':'customer_statement',
              'reliability':'needs_review','evidence_quote':'Synthetic customer statement','evidence_confirmed':True}
        with self.assertRaises(TenantLinearError):self.memory.add_fact(self.profile,self.contact2,data)
        self.assertEqual(self.memory.read(self.profile,self.contact2)['facts'],[])

    def test_replayed_external_id_on_another_channel_is_suppressed(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        link=self.link();self.fact(link)
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT external_id FROM saas_crm_messages WHERE id=%s',(self.message,));external=cur.fetchone()['external_id']
                cur.execute('DELETE FROM saas_crm_messages WHERE id=%s',(self.message,))
            conn.commit()
        channel=str(uuid.uuid4());jid='994709876543@s.whatsapp.net';source=str(uuid.uuid4())
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s,%s,'Synthetic other channel')",(self.tenant,channel))
                cur.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,conversation_kind) VALUES(%s,%s,%s,'contact') RETURNING lead_id",(self.tenant,channel,jid));lead=cur.fetchone()['lead_id']
                cur.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,body,direction,message_type) VALUES(%s,%s,%s,%s,%s,'Synthetic customer statement','incoming','text')",(source,self.tenant,lead,external,'connect:'+channel))
            conn.commit()
        other=self.memory.confirm_link(self.profile,self.contact2,channel,jid,True)['id']
        data={'request_id':str(uuid.uuid4()),'link_id':other,'source_message_id':source,'category':'profile',
              'value':'Synthetic replay','source_kind':'customer_statement','reliability':'needs_review',
              'evidence_quote':'Synthetic customer statement','evidence_confirmed':True}
        with self.assertRaises(TenantLinearError) as caught:self.memory.add_fact(self.profile,self.contact2,data)
        self.assertEqual(caught.exception.status,409)

    def test_contact_erasure_keeps_external_source_fence_across_channel(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        link=self.link();self.fact(link)
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT external_id FROM saas_crm_messages WHERE id=%s',(self.message,));external=cur.fetchone()['external_id']
                cur.execute('DELETE FROM saas_crm_native_contacts WHERE id=%s',(self.contact,))
                cur.execute('DELETE FROM saas_crm_messages WHERE id=%s',(self.message,))
            conn.commit()
        channel=str(uuid.uuid4());jid='994709876543@s.whatsapp.net';source=str(uuid.uuid4())
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s,%s,'Other channel')",(self.tenant,channel))
                cur.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,conversation_kind) VALUES(%s,%s,%s,'contact') RETURNING lead_id",(self.tenant,channel,jid));lead=cur.fetchone()['lead_id']
                cur.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,body,direction,message_type) VALUES(%s,%s,%s,%s,%s,'Synthetic customer statement','incoming','text')",(source,self.tenant,lead,external,'connect:'+channel))
            conn.commit()
        other=self.memory.confirm_link(self.profile,self.contact2,channel,jid,True)['id']
        data={'request_id':str(uuid.uuid4()),'link_id':other,'source_message_id':source,'category':'profile',
              'value':'Synthetic replay','source_kind':'customer_statement','reliability':'needs_review',
              'evidence_quote':'Synthetic customer statement','evidence_confirmed':True}
        with self.assertRaises(TenantLinearError):self.memory.add_fact(self.profile,self.contact2,data)

    def test_manual_erasure_then_contact_erasure_preserves_unrelated_tenant_memory(self):
        from tenant_platform import _connect
        link=self.link();fact=self.fact(link);self.memory.change_fact(self.profile,self.contact,fact['id'],1,'delete')
        channel=str(uuid.uuid4());jid='994709876543@s.whatsapp.net';source=str(uuid.uuid4())
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s,%s,'Other')",(self.tenant,channel))
                cur.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,conversation_kind) VALUES(%s,%s,%s,'contact') RETURNING lead_id",(self.tenant,channel,jid));lead=cur.fetchone()['lead_id']
                cur.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,body,direction,message_type) VALUES(%s,%s,%s,%s,%s,'Other synthetic','incoming','text')",(source,self.tenant,lead,'connect:'+str(uuid.uuid4()),'connect:'+channel))
            conn.commit()
        other=self.memory.confirm_link(self.profile,self.contact2,channel,jid,True)['id']
        data={'request_id':str(uuid.uuid4()),'link_id':other,'source_message_id':source,'category':'profile',
              'value':'Other synthetic value','source_kind':'customer_statement','reliability':'needs_review',
              'evidence_quote':'Other synthetic','evidence_confirmed':True}
        self.memory.add_fact(self.profile,self.contact2,data)
        with _connect() as conn:
            with conn.cursor() as cur:cur.execute('DELETE FROM saas_crm_native_contacts WHERE id=%s',(self.contact,))
            conn.commit()
        self.assertEqual([row['value'] for row in self.memory.read(self.profile,self.contact2)['facts']],['Other synthetic value'])

    @unittest.skipUnless(os.environ.get('CUSTOMER_MEMORY_ENFORCE_ERASURE_GATE')=='1','known contact/source in-flight gap: release gate disabled')
    def test_contact_erasure_fences_inflight_same_source_on_other_channel(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        import types
        first=self.link();self.fact(first)
        channel=str(uuid.uuid4());jid='994709876543@s.whatsapp.net'
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s,%s,'Other')",(self.tenant,channel))
                cur.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,conversation_kind) VALUES(%s,%s,%s,'contact') RETURNING lead_id",(self.tenant,channel,jid));lead=cur.fetchone()['lead_id']
                cur.execute('UPDATE saas_crm_messages SET kommo_lead_id=%s,channel=%s WHERE id=%s',(lead,'connect:'+channel,self.message))
            conn.commit()
        other=self.memory.confirm_link(self.profile,self.contact2,channel,jid,True)['id']
        data={'request_id':str(uuid.uuid4()),'link_id':other,'source_message_id':self.message,'category':'profile',
              'value':'Synthetic pre-existing context','source_kind':'customer_statement','reliability':'needs_review',
              'evidence_quote':'Synthetic customer statement','evidence_confirmed':True}
        self.memory.add_fact(self.profile,self.contact2,data)
        self.assertEqual(len(self.memory.read(self.profile,self.contact2)['facts']),1)
        ready=threading.Event();resume=threading.Event();out=[];original=self.memory._source_read_guard
        def paused(cur,tenant,message):
            result=original(cur,tenant,message);ready.set()
            if not resume.wait(4):raise RuntimeError('reader not resumed')
            return result
        def reader():
            try:out.append(self.memory.read(self.profile,self.contact2))
            except TenantLinearError as exc:out.append(('memory_error',exc.status))
            except Exception as exc:out.append(('unexpected',type(exc).__name__))
        fixed=time.monotonic();clock=types.SimpleNamespace(monotonic=lambda:fixed if threading.current_thread().name=='contact-read' else time.monotonic())
        with patch.object(self.memory,'_source_read_guard',paused),patch.object(self.memory,'time',clock):
            thread=threading.Thread(target=reader,name='contact-read');thread.start();self.addCleanup(thread.join,4)
            try:
                self.assertTrue(ready.wait(3))
                with _connect() as conn:
                    with conn.cursor() as cur:cur.execute('DELETE FROM saas_crm_native_contacts WHERE id=%s',(self.contact,))
                    conn.commit()
            finally:resume.set()
            thread.join(4)
        self.assertFalse(thread.is_alive());self.assertEqual(len(out),1)
        if isinstance(out[0],dict):self.assertEqual(out[0]['facts'],[],'old source-derived context escaped after contact erasure')
        else:self.assertIn(out[0],[('memory_error',409),('memory_error',503)])

    def test_source_fence_and_source_gate_are_tenant_scoped(self):
        from tenant_platform import _connect
        link=self.link();self.fact(link)
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT external_id FROM saas_crm_messages WHERE id=%s',(self.message,));external=cur.fetchone()['external_id']
                cur.execute('DELETE FROM saas_crm_messages WHERE id=%s',(self.message,))
            conn.commit()
        contact=str(uuid.uuid4());channel=str(uuid.uuid4());source=str(uuid.uuid4());jid='994701111111@s.whatsapp.net'
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO saas_crm_native_contacts(id,tenant_id,display_name) VALUES(%s,%s,'Other tenant')",(contact,self.other))
                cur.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s,%s,'Other tenant')",(self.other,channel))
                cur.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,conversation_kind) VALUES(%s,%s,%s,'contact') RETURNING lead_id",(self.other,channel,jid));lead=cur.fetchone()['lead_id']
                cur.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,body,direction,message_type) VALUES(%s,%s,%s,%s,%s,'Other tenant evidence','incoming','text')",(source,self.other,lead,external,'connect:'+channel))
            conn.commit()
        profile={'tenant_id':self.other,'telegram_id':72}
        other=self.memory.confirm_link(profile,contact,channel,jid,True)['id']
        data={'request_id':str(uuid.uuid4()),'link_id':other,'source_message_id':source,'category':'profile',
              'value':'Other tenant fact','source_kind':'customer_statement','reliability':'needs_review',
              'evidence_quote':'Other tenant evidence','evidence_confirmed':True}
        with _connect() as held:
            with held.cursor() as cur:
                cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('customer-memory-source-tenant:'+self.tenant,))
                self.memory.add_fact(profile,contact,data)
                self.assertEqual(len(self.memory.read(profile,contact)['facts']),1)
            held.rollback()

    def test_old_cold_start_cannot_replace_versioned_erasure_triggers(self):
        from tenant_platform import _connect
        link=self.link();self.fact(link)
        with _connect() as conn:
            with conn.cursor() as cur:
                # Model the older binary's CREATE OR REPLACE FUNCTION followed
                # by its create-trigger-only-if-absent check.
                for function,trigger,table,event in (
                    ('customer_memory_source_deleted','customer_memory_source_deleted','saas_crm_messages','AFTER'),
                    ('customer_memory_contact_erased','customer_memory_contact_erased','saas_crm_native_contacts','BEFORE')):
                    cur.execute('CREATE OR REPLACE FUNCTION '+function+'() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN OLD; END $$')
                    cur.execute("""DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='"""+trigger+"""' AND tgrelid='"""+table+"""'::regclass AND NOT tgisinternal) THEN CREATE TRIGGER """+trigger+' '+event+' DELETE ON '+table+' FOR EACH ROW EXECUTE FUNCTION '+function+"""(); END IF; END $$""")
                cur.execute('''SELECT t.tgname,p.proname FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
                  WHERE t.tgname IN ('customer_memory_source_deleted','customer_memory_contact_erased') AND NOT t.tgisinternal''')
                names={r['tgname']:r['proname'] for r in cur.fetchall()}
                self.assertEqual(names,{'customer_memory_source_deleted':'customer_memory_source_deleted_v2',
                                        'customer_memory_contact_erased':'customer_memory_contact_erased_v2'})
                cur.execute('DELETE FROM saas_crm_messages WHERE id=%s',(self.message,))
                cur.execute('SELECT count(*) AS n FROM saas_customer_memory_source_erasure_events WHERE tenant_id=%s',(self.tenant,))
                self.assertGreater(cur.fetchone()['n'],0)
                cur.execute('DELETE FROM saas_crm_native_contacts WHERE id=%s',(self.contact,))
                cur.execute('SELECT count(*) AS n FROM saas_customer_memory_erased_subjects WHERE tenant_id=%s',(self.tenant,))
                self.assertGreater(cur.fetchone()['n'],0)
            conn.rollback()

    def test_versioned_trigger_upgrades_old_binding_and_skips_current_ddl(self):
        from tenant_platform import _connect
        from tenant_customer_memory_schema import _ensure_versioned_trigger
        for kind,table,trigger,old,new,event in (
            ('source','saas_crm_messages','customer_memory_source_deleted','customer_memory_source_deleted','customer_memory_source_deleted_v2','AFTER'),
            ('contact','saas_crm_native_contacts','customer_memory_contact_erased','customer_memory_contact_erased','customer_memory_contact_erased_v2','BEFORE')):
            with self.subTest(kind=kind):
                with _connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute('CREATE OR REPLACE FUNCTION '+old+'() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN OLD; END $$')
                        cur.execute('CREATE OR REPLACE TRIGGER '+trigger+' '+event+' DELETE ON '+table+' FOR EACH ROW EXECUTE FUNCTION '+old+'()')
                        _ensure_versioned_trigger(cur,kind)
                        cur.execute('''SELECT p.proname FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
                          WHERE t.tgrelid=%s::regclass AND t.tgname=%s''',(table,trigger))
                        self.assertEqual(cur.fetchone()['proname'],new)
                    conn.rollback()
                started=threading.Event();done=threading.Event();out=[]
                def check():
                    try:
                        with _connect() as db:
                            with db.cursor() as cur:
                                cur.execute("SET LOCAL lock_timeout='200ms'");started.set()
                                _ensure_versioned_trigger(cur,kind)
                            db.commit()
                        out.append('no-ddl')
                    except Exception as exc:out.append(getattr(exc,'sqlstate',type(exc).__name__))
                    finally:done.set()
                with _connect() as held:
                    with held.cursor() as cur:
                        cur.execute('LOCK TABLE '+table+' IN ROW EXCLUSIVE MODE')
                        thread=threading.Thread(target=check);thread.start();self.addCleanup(thread.join,3)
                        self.assertTrue(started.wait(2));self.assertTrue(done.wait(2))
                    held.rollback()
                thread.join(3);self.assertEqual(out,['no-ddl'])

    def test_legacy_source_fence_without_external_token_fails_closed(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        link=self.link();self.fact(link)
        with _connect() as conn:
            with conn.cursor() as cur:
                key,_=self.memory._source_token(self.tenant,'synthetic','other')
                cur.execute('''INSERT INTO saas_customer_memory_erased_sources(tenant_id,source_token,token_key_id)
                  VALUES(%s,%s,%s)''',(self.tenant,'synthetic-legacy-token',key))
            conn.commit()
        self.assertEqual(self.memory.read(self.profile,self.contact)['facts'],[])
        with self.assertRaises(TenantLinearError):self.fact(link,distinct_confirmation=True)

    def test_rr_source_deletion_quarantines_replay_after_old_snapshot(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        link=self.link()
        with _connect() as deletion:
            with deletion.cursor() as cur:
                cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                cur.execute('SELECT kommo_lead_id,external_id,channel FROM saas_crm_messages WHERE id=%s',(self.message,));src=cur.fetchone()
                self.fact(link,value='Synthetic added after snapshot')
                cur.execute('DELETE FROM saas_crm_messages WHERE id=%s',(self.message,))
            deletion.commit()
        replay=str(uuid.uuid4())
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('''SELECT 1 FROM saas_customer_memory_source_erasure_events WHERE tenant_id=%s
                  AND source_token='' AND token_key_id='' ''',(self.tenant,));self.assertIsNotNone(cur.fetchone())
                cur.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,body,direction,message_type) VALUES(%s,%s,%s,%s,%s,'Synthetic replay','incoming','text')",(replay,self.tenant,src['kommo_lead_id'],src['external_id'],src['channel']))
            conn.commit()
        with self.assertRaises(TenantLinearError):self.fact(link,source_message_id=replay,value='Synthetic replay fact')
        self.assertEqual(self.memory.read(self.profile,self.contact)['facts'],[])

    @unittest.skipUnless(os.environ.get('CUSTOMER_MEMORY_ENFORCE_ERASURE_GATE')=='1','known pre-fact source-replay gap: release gate disabled')
    def test_deleted_source_without_fact_cannot_reappear_on_replay(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        link=self.link()
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT kommo_lead_id,external_id,channel FROM saas_crm_messages WHERE id=%s',(self.message,));src=cur.fetchone()
                cur.execute('DELETE FROM saas_crm_messages WHERE id=%s',(self.message,))
            conn.commit()
        replay=str(uuid.uuid4())
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,body,direction,message_type) VALUES(%s,%s,%s,%s,%s,'Synthetic replay','incoming','text')",(replay,self.tenant,src['kommo_lead_id'],src['external_id'],src['channel']))
            conn.commit()
        with self.assertRaises(TenantLinearError):self.fact(link,source_message_id=replay,value='Synthetic replay')

    def test_busy_source_gate_rolls_back_request_and_allows_retry(self):
        from tenant_platform import _connect
        import psycopg,time
        link=self.link();fact=self.fact(link);gate='customer-memory-source-tenant:'+self.tenant
        with _connect() as held:
            with held.cursor() as cur:
                cur.execute('SELECT pg_advisory_xact_lock_shared(hashtextextended(%s,0))',(gate,))
                started=time.monotonic()
                with self.assertRaises(psycopg.errors.LockNotAvailable):self.memory.change_fact(self.profile,self.contact,fact['id'],1,'delete')
                self.assertLess(time.monotonic()-started,4)
            held.rollback()
        self.assertEqual(self.memory.erasure_statuses(self.profile,self.contact)['requests'],[])
        self.assertEqual(self.memory.read(self.profile,self.contact)['facts'][0]['id'],fact['id'])
        self.assertEqual(self.memory.change_fact(self.profile,self.contact,fact['id'],1,'delete')['status'],'pending')

    @unittest.skipUnless(os.environ.get('CUSTOMER_MEMORY_ENFORCE_ERASURE_GATE')=='1','known source-delete race: release gate disabled')
    def test_rr_source_quarantine_fences_already_started_other_subject_read(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        import time,types
        link=self.link();self.fact(link)
        route='994709876543@s.whatsapp.net';source=str(uuid.uuid4())
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,conversation_kind) VALUES(%s,%s,%s,'contact') RETURNING lead_id",(self.tenant,self.channel,route));lead=cur.fetchone()['lead_id']
                cur.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,body,direction,message_type) VALUES(%s,%s,%s,%s,%s,'Independent synthetic source','incoming','text')",(source,self.tenant,lead,'connect:'+str(uuid.uuid4()),'connect:'+self.channel))
            conn.commit()
        other=self.memory.confirm_link(self.profile,self.contact2,self.channel,route,True)['id']
        data={'request_id':str(uuid.uuid4()),'link_id':other,'source_message_id':source,'category':'profile',
              'value':'Synthetic pre-existing context','source_kind':'customer_statement','reliability':'needs_review',
              'evidence_quote':'Independent synthetic source','evidence_confirmed':True}
        self.memory.add_fact(self.profile,self.contact2,data)
        self.assertEqual(len(self.memory.read(self.profile,self.contact2)['facts']),1)
        ready=threading.Event();resume=threading.Event();out=[];original=self.memory._source_read_guard
        def paused(cur,tenant,message):
            result=original(cur,tenant,message);ready.set()
            if not resume.wait(4):raise RuntimeError('reader not resumed')
            return result
        def reader():
            try:out.append(self.memory.read(self.profile,self.contact2))
            except TenantLinearError as exc:out.append(('memory_error',exc.status))
            except Exception as exc:out.append(('unexpected',type(exc).__name__))
        fixed=time.monotonic();clock=types.SimpleNamespace(monotonic=lambda:fixed if threading.current_thread().name=='rr-read' else time.monotonic())
        with patch.object(self.memory,'_source_read_guard',paused),patch.object(self.memory,'time',clock):
            thread=threading.Thread(target=reader,name='rr-read');thread.start();self.addCleanup(thread.join,4)
            try:
                self.assertTrue(ready.wait(3))
                with _connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                        cur.execute('DELETE FROM saas_crm_messages WHERE id=%s',(self.message,))
                    conn.commit()
            finally:resume.set()
            thread.join(4)
        self.assertFalse(thread.is_alive());self.assertEqual(len(out),1)
        if isinstance(out[0],dict):self.assertEqual(out[0]['facts'],[],'old context escaped after erasure commit')
        else:self.assertIn(out[0],[('memory_error',409),('memory_error',503)])

    def test_existing_other_subject_fact_is_hidden_after_source_erasure(self):
        link=self.link();first=self.fact(link,value='Synthetic original')
        self.memory.revoke_link(self.profile,self.contact,link,1)
        other=self.memory.confirm_link(self.profile,self.contact2,self.channel,self.jid,True)['id']
        data={'request_id':str(uuid.uuid4()),'link_id':other,'source_message_id':self.message,
              'category':'feature','value':'Other synthetic','source_kind':'customer_statement',
              'reliability':'needs_review','evidence_quote':'Synthetic customer statement','evidence_confirmed':True}
        self.memory.add_fact(self.profile,self.contact2,data)
        self.assertEqual(len(self.memory.read(self.profile,self.contact2)['facts']),1)
        self.assertEqual(self.memory.change_fact(self.profile,self.contact,first['id'],1,'delete')['status'],'pending')
        self.assertEqual(self.memory.read(self.profile,self.contact2)['facts'],[])

    def test_inflight_add_of_erased_source_cannot_remain_visible(self):
        from tenant_linear_policy import TenantLinearError
        link=self.link();first=self.fact(link,value='Synthetic original')
        self.memory.revoke_link(self.profile,self.contact,link,1)
        other=self.memory.confirm_link(self.profile,self.contact2,self.channel,self.jid,True)['id']
        data={'request_id':str(uuid.uuid4()),'link_id':other,'source_message_id':self.message,
              'category':'feature','value':'Other synthetic','source_kind':'customer_statement',
              'reliability':'needs_review','evidence_quote':'Synthetic customer statement','evidence_confirmed':True}
        guarded=threading.Event();release=threading.Event();erasing=threading.Event();done=threading.Event();out=[];reader_pids=[];delete_pids=[]
        original_read=self.memory._source_read_guard;original_erase=self.memory._lock_sources_for_erasure
        def read_guard(cur,tenant,source):
            result=original_read(cur,tenant,source)
            if threading.current_thread().name=='other-add':
                cur.execute('SELECT pg_backend_pid() AS pid');reader_pids.append(cur.fetchone()['pid'])
                guarded.set()
                if not release.wait(3):raise RuntimeError('release missing')
            return result
        def erase_guard(cur,tenant,pairs):
            cur.execute('SELECT pg_backend_pid() AS pid');delete_pids.append(cur.fetchone()['pid'])
            erasing.set();return original_erase(cur,tenant,pairs)
        def add():
            try:self.memory.add_fact(self.profile,self.contact2,data);out.append(('add','committed'))
            except TenantLinearError as exc:out.append(('add',exc.status))
            except Exception as exc:out.append(('add_error',type(exc).__name__))
        def erase():
            try:out.append(('erase',self.memory.change_fact(self.profile,self.contact,first['id'],1,'delete')['status']))
            except Exception as exc:out.append(('erase_error',type(exc).__name__))
            finally:done.set()
        with patch.object(self.memory,'_source_read_guard',read_guard),patch.object(self.memory,'_lock_sources_for_erasure',erase_guard):
            adding=threading.Thread(target=add,name='other-add');adding.start();self.addCleanup(adding.join,4)
            try:
                self.assertTrue(guarded.wait(3))
                deleting=threading.Thread(target=erase,name='first-delete');deleting.start();self.addCleanup(deleting.join,4)
                self.assertTrue(erasing.wait(3));self.assert_backend_blocked(delete_pids[0],reader_pids[0],done)
            finally:release.set()
            adding.join(4);deleting.join(4)
        self.assertCountEqual(out,[('add','committed'),('erase','pending')])
        from tenant_platform import _connect
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT count(*) AS n FROM saas_customer_memory_facts WHERE tenant_id=%s AND contact_id=%s',(self.tenant,self.contact2))
                self.assertEqual(cur.fetchone()['n'],1)
        self.assertEqual(self.memory.read(self.profile,self.contact2)['facts'],[])

    def test_mixed_key_processes_share_tenant_fence(self):
        import base64,multiprocessing
        ctx=multiprocessing.get_context('fork')
        link=self.link();first=self.fact(link,value='Synthetic original')
        self.memory.revoke_link(self.profile,self.contact,link,1)
        data={'request_id':str(uuid.uuid4()),'source_message_id':self.message,
              'category':'feature','value':'Other synthetic','source_kind':'customer_statement',
              'reliability':'needs_review','evidence_quote':'Synthetic customer statement','evidence_confirmed':True}
        guarded=ctx.Event();release=ctx.Event();erased=ctx.Event();results=ctx.Queue();reader_pids=ctx.Queue()
        def child():
            os.environ['TENANT_ENCRYPTION_KEY']=base64.urlsafe_b64encode(b'\x02'*32).decode()
            original=self.memory._source_read_guard;first_call=[True]
            def paused(cur,tenant,source):
                result=original(cur,tenant,source)
                if first_call[0]:
                    first_call[0]=False
                    cur.execute('SELECT pg_backend_pid() AS pid');reader_pids.put(cur.fetchone()['pid']);guarded.set()
                    if not release.wait(3):raise RuntimeError('release missing')
                return result
            try:
                data['link_id']=self.memory.confirm_link(self.profile,self.contact2,self.channel,self.jid,True)['id']
                with patch.object(self.memory,'_source_read_guard',paused):
                    self.memory.add_fact(self.profile,self.contact2,data)
                results.put(('add','committed'))
                if not erased.wait(4):raise RuntimeError('erasure missing')
                results.put(('visible',len(self.memory.read(self.profile,self.contact2)['facts'])))
            except Exception as exc:results.put(('error',type(exc).__name__))
        process=ctx.Process(target=child);process.start();self.addCleanup(process.join,5)
        erasing=threading.Event();done=threading.Event();out=[];delete_pids=[];original_erase=self.memory._lock_sources_for_erasure
        def erase_guard(cur,tenant,pairs):
            cur.execute('SELECT pg_backend_pid() AS pid');delete_pids.append(cur.fetchone()['pid'])
            erasing.set();return original_erase(cur,tenant,pairs)
        def erase():
            try:out.append(self.memory.change_fact(self.profile,self.contact,first['id'],1,'delete')['status'])
            except Exception as exc:out.append(type(exc).__name__)
            finally:done.set()
        try:
            self.assertTrue(guarded.wait(3));reader_pid=reader_pids.get(timeout=2)
            with patch.object(self.memory,'_lock_sources_for_erasure',erase_guard):
                thread=threading.Thread(target=erase);thread.start();self.addCleanup(thread.join,4)
                self.assertTrue(erasing.wait(3));self.assert_backend_blocked(delete_pids[0],reader_pid,done);release.set()
                thread.join(4)
            self.assertEqual(out,['pending']);erased.set()
            self.assertCountEqual([results.get(timeout=4),results.get(timeout=4)],[('add','committed'),('visible',0)])
            from tenant_platform import _connect
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute('SELECT count(*) AS n FROM saas_customer_memory_facts WHERE tenant_id=%s AND contact_id=%s',(self.tenant,self.contact2))
                    self.assertEqual(cur.fetchone()['n'],1)
        finally:
            release.set();erased.set();process.join(5)
            if process.is_alive():process.terminate();process.join(2)
        self.assertEqual(process.exitcode,0)

    def test_http_off_allows_only_owner_erasure_recovery(self):
        import asyncio,logging
        from aiohttp import web
        from aiohttp.test_utils import TestClient,TestServer
        from tenant_customer_memory_api import handler
        link=self.link();fact=self.fact(link)
        async def exercise():
            app=web.Application();app.router.add_route('*','/memory/{contact_id}',handler(lambda _:self.profile,'https://example.test',logging.getLogger('fixture')))
            client=TestClient(TestServer(app));await client.start_server()
            path='/memory/'+self.contact+'?expected_tenant_id='+self.tenant+'&expected_user_id=71'
            origin={'Origin':'https://example.test'}
            try:
                with patch.dict(os.environ,{'CUSTOMER_MEMORY_ENABLED':'0'}):
                    with patch.object(self.memory,'_connect',side_effect=AssertionError('disabled operation reached database')):
                        self.assertEqual((await client.get(path)).status,404)
                        self.assertEqual((await client.post(path,json={'action':'confirm_link'},headers=origin)).status,404)
                    command={'action':'delete','fact_id':fact['id'],'expected_version':1}
                    response=await client.post(path,json=command,headers=origin);self.assertEqual(response.status,200)
                    result=(await response.json())['data'];self.assertEqual(result['status'],'pending')
                    response=await client.get(path+'&erasure_request_id='+result['request_id']);self.assertEqual(response.status,200)
                    self.assertEqual((await response.json())['data']['status'],'pending')
                    response=await client.get(path+'&erasure_status=1');self.assertEqual(response.status,200)
                    self.assertEqual((await response.json())['data']['requests'][0]['request_id'],result['request_id'])
                    response=await client.post(path,json=command,headers=origin);self.assertEqual((await response.json())['data'],result)
            finally:await client.close()
        asyncio.run(exercise())

    def test_partial_recovery_schema_fails_closed_without_migration(self):
        import asyncio,logging
        from aiohttp import web
        from aiohttp.test_utils import TestClient,TestServer
        from tenant_platform import _connect
        from tenant_customer_memory_api import handler
        conn=_connect()
        with conn.cursor() as cur:
            cur.execute('CREATE TEMP TABLE saas_customer_memory_erasure_requests(id UUID)')
        conn.commit()
        async def exercise():
            app=web.Application();app.router.add_route('*','/memory/{contact_id}',handler(lambda _:self.profile,'https://example.test',logging.getLogger('fixture')))
            client=TestClient(TestServer(app));await client.start_server()
            path='/memory/'+self.contact+'?expected_tenant_id='+self.tenant+'&expected_user_id=71&erasure_status=1'
            try:
                with patch.dict(os.environ,{'CUSTOMER_MEMORY_ENABLED':'0'}), \
                     patch.object(self.memory,'_connect',return_value=conn), \
                     patch.object(self.memory,'_ensure_schema',side_effect=AssertionError('recovery must not migrate')):
                    response=await client.get(path)
                    self.assertEqual(response.status,503)
                    self.assertEqual((await response.json())['error'],'memory_unavailable')
            finally:await client.close()
        try:asyncio.run(exercise())
        finally:
            if not conn.closed:conn.close()

    def test_concurrent_reuse_waits_for_erasure_reservation(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        request_id=str(uuid.uuid4());link=self.link();fact=self.fact(link,request_id=request_id)
        route='994709876543@s.whatsapp.net';message=str(uuid.uuid4())
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,conversation_kind) VALUES(%s,%s,%s,'contact') RETURNING lead_id",(self.tenant,self.channel,route));lead=cur.fetchone()['lead_id']
                cur.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,body,direction,message_type) VALUES(%s,%s,%s,%s,%s,'Other synthetic evidence','incoming','text')",(message,self.tenant,lead,'connect:'+str(uuid.uuid4()),'connect:'+self.channel))
            conn.commit()
        other=self.memory.confirm_link(self.profile,self.contact2,self.channel,route,True)['id']
        data={'request_id':request_id,'link_id':other,'source_message_id':message,'category':'profile',
              'value':'Other synthetic','source_kind':'customer_statement','reliability':'needs_review',
              'evidence_quote':'Other synthetic evidence','evidence_confirmed':True}
        locked=threading.Event();release=threading.Event();attempted=threading.Event();done=threading.Event();out=[]
        original=self.memory._lock_fact_ids
        def instrument(cur,tenant,ids):
            if threading.current_thread().name=='pending-delete':
                original(cur,tenant,ids);locked.set()
                if not release.wait(3):raise RuntimeError('release missing')
            else:
                attempted.set();original(cur,tenant,ids)
        def erase():
            try:out.append(('erase',self.memory.change_fact(self.profile,self.contact,fact['id'],1,'delete')['status']))
            except Exception as exc:out.append(('erase_error',type(exc).__name__))
        def add():
            try:self.memory.add_fact(self.profile,self.contact2,data);out.append(('add','accepted'))
            except TenantLinearError as exc:out.append(('add',exc.status))
            except Exception as exc:out.append(('add_error',type(exc).__name__))
            finally:done.set()
        with patch.object(self.memory,'_lock_fact_ids',instrument):
            first=threading.Thread(target=erase,name='pending-delete');first.start();self.addCleanup(first.join,4)
            try:
                self.assertTrue(locked.wait(3))
                second=threading.Thread(target=add,name='pending-add');second.start();self.addCleanup(second.join,4)
                self.assertTrue(attempted.wait(3));self.assertFalse(done.wait(.05))
            finally:release.set()
            first.join(4);second.join(4)
        self.assertCountEqual(out,[('erase','pending'),('add',409)])
