"""Disposable loopback PostgreSQL only; never uses the configured application DB."""
import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

DSN=os.environ.get('CUSTOMER_MEMORY_TEST_DSN','')
if DSN:
    from psycopg.conninfo import conninfo_to_dict
    params=conninfo_to_dict(DSN)
    loopback={'localhost','127.0.0.1','::1'}
    if (params.get('host') not in loopback or params.get('hostaddr') and params['hostaddr'] not in loopback
            or params.get('service') or params.get('dbname') != 'customer_memory_test'
            or os.environ.get('CUSTOMER_MEMORY_TEST_ISOLATED') != '1'):
        raise RuntimeError('Customer memory tests require an explicit disposable loopback database')


@unittest.skipUnless(DSN,'CUSTOMER_MEMORY_TEST_DSN is not set')
class CustomerMemoryPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_db=os.environ.get('DATABASE_URL');cls.original_flag=os.environ.get('CUSTOMER_MEMORY_ENABLED');cls.original_key=os.environ.get('TENANT_ENCRYPTION_KEY')
        cls.original_pg={k:os.environ.pop(k,None) for k in ('PGSERVICE','PGSERVICEFILE','PGHOSTADDR','PGOPTIONS')}
        os.environ['DATABASE_URL']=DSN;os.environ['CUSTOMER_MEMORY_ENABLED']='1'
        import base64
        os.environ['TENANT_ENCRYPTION_KEY']=base64.urlsafe_b64encode(b'\x01'*32).decode()
        from tenant_platform import _connect,_ensure_schema
        from tenant_connect_transport import _schema
        from tenant_ai_agents_schema import ensure_schema
        with _connect() as conn:
            _ensure_schema(conn);ensure_schema(conn);_schema(conn);conn.commit()

    @classmethod
    def tearDownClass(cls):
        for key,value in list(cls.original_pg.items())+[('DATABASE_URL',cls.original_db),('CUSTOMER_MEMORY_ENABLED',cls.original_flag),('TENANT_ENCRYPTION_KEY',cls.original_key)]:
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value

    def setUp(self):
        from tenant_platform import _connect
        self.tenant=str(uuid.uuid4()); self.other=str(uuid.uuid4()); self.channel=str(uuid.uuid4())
        self.contact=str(uuid.uuid4());self.contact2=str(uuid.uuid4());self.message=str(uuid.uuid4())
        self.jid='994701234567@s.whatsapp.net';self.profile={'tenant_id':self.tenant,'telegram_id':71}
        with _connect() as conn:
            with conn.cursor() as cur:
                for tenant,actor in ((self.tenant,71),(self.other,72)):
                    cur.execute("INSERT INTO saas_tenants(id,slug,name,status,owner_telegram_id) VALUES(%s,%s,'Fixture','active',%s)",(tenant,str(uuid.uuid4()),actor))
                    cur.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,display_name,role,active,permissions) VALUES(%s,%s,'Owner','owner',TRUE,'[]')",(tenant,actor))
                    cur.execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value) VALUES(%s,'customer_memory','{\"enabled\":true}')",(tenant,))
                cur.execute("INSERT INTO saas_crm_native_contacts(id,tenant_id,display_name) VALUES(%s,%s,'A'),(%s,%s,'B')",(self.contact,self.tenant,self.contact2,self.tenant))
                cur.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s,%s,'Fixture')",(self.tenant,self.channel))
                cur.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,conversation_kind) VALUES(%s,%s,%s,'contact') RETURNING lead_id",(self.tenant,self.channel,self.jid))
                lead=cur.fetchone()['lead_id']
                cur.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,body,direction,message_type) VALUES(%s,%s,%s,%s,%s,'Synthetic customer statement','incoming','text')",(self.message,self.tenant,lead,'connect:'+str(uuid.uuid4()),'connect:'+self.channel))
            conn.commit()
        import tenant_customer_memory as memory
        self.memory=memory

    def link(self):
        return self.memory.confirm_link(self.profile,self.contact,self.channel,self.jid,True)['id']

    def fact(self,link,**changes):
        data={'request_id':str(uuid.uuid4()),'link_id':link,'source_message_id':self.message,
              'category':'feature','value':'Uses Android','source_kind':'customer_statement','reliability':'needs_review'}
        data.update(changes)
        from tenant_platform import _connect
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT body FROM saas_crm_messages WHERE tenant_id=%s AND id=%s',(self.tenant,data['source_message_id']))
                source=cur.fetchone()
        data.update(evidence_quote=(source or {}).get('body',''),evidence_confirmed=True)
        return self.memory.add_fact(self.profile,self.contact,data)

    def edit(self,fact_id,version,value):
        from tenant_platform import _connect
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('''SELECT m.body FROM saas_customer_memory_facts f JOIN saas_crm_messages m
                  ON m.tenant_id=f.tenant_id AND m.id=f.source_message_id WHERE f.tenant_id=%s AND f.id=%s''',
                  (self.tenant,fact_id))
                source=cur.fetchone()
        return self.memory.change_fact(self.profile,self.contact,fact_id,version,'edit',value=value,
                    evidence_quote=(source or {}).get('body',''),evidence_confirmed=True)

    def test_manual_edit_conflict_and_delete_suppression(self):
        from tenant_linear_policy import TenantLinearError
        from tenant_platform import _connect
        link=self.link(); fact=self.fact(link); item=fact['id']
        self.assertEqual(self.memory.read(self.profile,self.contact)['facts'][0]['value'],'Uses Android')
        self.assertEqual(self.edit(item,1,'Uses iOS')['version'],2)
        with self.assertRaises(TenantLinearError): self.memory.change_fact(self.profile,self.contact,item,1,'confirm')
        self.memory.change_fact(self.profile,self.contact,item,2,'delete')
        self.assertEqual(self.memory.read(self.profile,self.contact)['facts'],[])
        with self.assertRaises(TenantLinearError): self.fact(link)
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT count(*) n FROM saas_customer_memory_fact_versions WHERE fact_id=%s',(item,))
                self.assertEqual(cur.fetchone()['n'],0)

    def test_identity_conflict_groups_tenant_and_revocation(self):
        from tenant_linear_policy import TenantLinearError
        link=self.link(); self.fact(link)
        with self.assertRaises(TenantLinearError): self.memory.confirm_link(self.profile,self.contact2,self.channel,self.jid,True)
        with self.assertRaises(TenantLinearError): self.memory.confirm_link(self.profile,self.contact,self.channel,'123456789@g.us',True)
        with self.assertRaises(TenantLinearError): self.memory.read({'tenant_id':self.other,'telegram_id':72},self.contact)
        self.memory.revoke_link(self.profile,self.contact,link,1)
        self.assertEqual(self.memory.read(self.profile,self.contact),{'links':[],'facts':[],'incomplete':True})
        new=self.memory.confirm_link(self.profile,self.contact2,self.channel,self.jid,True)['id']
        self.assertNotEqual(new,link)
        self.assertEqual(self.memory.read(self.profile,self.contact2)['facts'],[])

    def test_changed_source_and_permission_fail_closed(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        link=self.link();self.fact(link)
        with _connect() as conn:
            with conn.cursor() as cur:cur.execute("UPDATE saas_crm_messages SET body='Changed' WHERE id=%s",(self.message,))
            conn.commit()
        self.assertEqual(self.memory.read(self.profile,self.contact)['facts'],[])
        with _connect() as conn:
            with conn.cursor() as cur:cur.execute("UPDATE saas_crm_messages SET body='Synthetic customer statement' WHERE id=%s",(self.message,))
            conn.commit()
        self.assertEqual(self.memory.read(self.profile,self.contact)['facts'],[])
        with _connect() as conn:
            with conn.cursor() as cur:cur.execute("UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s AND telegram_id=71",(self.tenant,))
            conn.commit()
        with self.assertRaises(TenantLinearError):self.memory.read(self.profile,self.contact)

    def test_real_concurrent_edits_one_wins(self):
        from tenant_linear_policy import TenantLinearError
        link=self.link();fact=self.fact(link);gate=Barrier(2)
        def change(value):
            gate.wait()
            try:return self.edit(fact['id'],1,value)['version']
            except TenantLinearError as error:return error.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(change,['Uses iOS','Uses Linux']))
        self.assertCountEqual(results,[2,409])

    def test_secret_and_unprocessed_media_are_rejected(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        link=self.link()
        with self.assertRaises(TenantLinearError):self.fact(link,value='Пароль: test-fixture')
        with _connect() as conn:
            with conn.cursor() as cur:cur.execute("UPDATE saas_crm_messages SET message_type='image' WHERE id=%s",(self.message,))
            conn.commit()
        with self.assertRaises(TenantLinearError):self.fact(link)

    def test_http_boundary_requires_origin_and_live_membership(self):
        import asyncio
        import logging
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer
        from tenant_customer_memory_api import handler
        from tenant_platform import _connect
        async def exercise():
            app=web.Application()
            app.router.add_route('*','/memory/{contact_id}',handler(lambda _:self.profile,'https://example.test',logging.getLogger('fixture')))
            client=TestClient(TestServer(app));await client.start_server()
            try:
                path='/memory/'+self.contact+'?expected_tenant_id='+self.tenant+'&expected_user_id=71'
                response=await client.get('/memory/'+self.contact)
                self.assertEqual(response.status,400)
                response=await client.get(path)
                self.assertEqual(response.status,200)
                self.assertEqual(response.headers.get('Cache-Control'),'no-store')
                payload={'action':'confirm_link','channel_id':self.channel,'conversation_id':self.jid,'confirm':True}
                response=await client.post(path,json=payload)
                self.assertEqual(response.status,403)
                response=await client.post(path,data='x'*20001,headers={'Origin':'https://example.test','Content-Type':'application/json'})
                self.assertEqual(response.status,413)
                response=await client.post(path,json=payload,headers={'Origin':'https://example.test'})
                self.assertEqual(response.status,200)
                with _connect() as conn:
                    with conn.cursor() as cur:cur.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s AND telegram_id=71',(self.tenant,))
                    conn.commit()
                response=await client.get(path)
                self.assertEqual(response.status,403)
            finally: await client.close()
        asyncio.run(exercise())

    def test_duplicate_submission_and_flag_disabled(self):
        from tenant_linear_policy import TenantLinearError
        link=self.link();request_id=str(uuid.uuid4())
        first=self.fact(link,request_id=request_id)
        self.assertEqual(self.fact(link,request_id=request_id),first)
        self.assertEqual(len(self.memory.read(self.profile,self.contact)['facts']),1)
        previous=os.environ.pop('CUSTOMER_MEMORY_ENABLED')
        try:
            with self.assertRaises(TenantLinearError):self.memory.read(self.profile,self.contact)
        finally:os.environ['CUSTOMER_MEMORY_ENABLED']=previous

    def test_current_member_permissions_and_source_conflict(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        link=self.link();fact=self.fact(link)
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,display_name,role,active,permissions) VALUES(%s,73,'Reader','worker',TRUE,'[\"dialogs\",\"customers:view\"]')",(self.tenant,))
            conn.commit()
        reader={'tenant_id':self.tenant,'telegram_id':73}
        self.assertEqual(len(self.memory.read(reader,self.contact)['facts']),1)
        with self.assertRaises(TenantLinearError):self.memory.change_fact(reader,self.contact,fact['id'],1,'confirm')
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE saas_tenant_members SET permissions='[\"customers:view\"]' WHERE tenant_id=%s AND telegram_id=73",(self.tenant,))
            conn.commit()
        with self.assertRaises(TenantLinearError):self.memory.read(reader,self.contact)
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO saas_connect_identity_conflicts(tenant_id,connect_channel_id,conversation_id,event_id,evidence) VALUES(%s,%s,%s,'fixture','{}')",(self.tenant,self.channel,self.jid))
            conn.commit()
        self.assertEqual(self.memory.read(self.profile,self.contact),{'links':[],'facts':[],'incomplete':True})

    def test_link_revocation_waits_for_lock_and_fences_future_reads(self):
        from tenant_platform import _connect
        from threading import Event, Thread
        link=self.link();self.fact(link);entered=Event();done=Event();outcome=[]
        def revoke():
            entered.set()
            outcome.append(self.memory.revoke_link(self.profile,self.contact,link,1))
            done.set()
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT id FROM saas_customer_memory_links WHERE id=%s FOR UPDATE',(link,))
                thread=Thread(target=revoke);thread.start();self.assertTrue(entered.wait(2))
                self.assertFalse(done.wait(.15))
            conn.commit()
        thread.join(3)
        self.assertTrue(done.is_set());self.assertEqual(outcome,[{'revoked':True}])
        self.assertEqual(self.memory.read(self.profile,self.contact),{'links':[],'facts':[],'incomplete':True})

    def test_owner_can_delete_hidden_fact_after_revocation_or_source_loss(self):
        from tenant_platform import _connect
        link=self.link();fact=self.fact(link)
        self.memory.revoke_link(self.profile,self.contact,link,1)
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('DELETE FROM saas_crm_messages WHERE id=%s',(self.message,))
                cur.execute('UPDATE saas_crm_native_contacts SET archived_at=now() WHERE id=%s',(self.contact,))
            conn.commit()
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT count(*) AS n FROM saas_customer_memory_fact_versions WHERE fact_id=%s',(fact['id'],))
                self.assertEqual(cur.fetchone()['n'],0)
                cur.execute('SELECT count(*) AS n FROM saas_customer_memory_facts WHERE id=%s',(fact['id'],))
                self.assertEqual(cur.fetchone()['n'],0)
                cur.execute('SELECT count(*) AS n FROM saas_customer_memory_suppressions WHERE tenant_id=%s AND contact_id=%s AND source_message_id=%s',(self.tenant,self.contact,self.message))
                self.assertEqual(cur.fetchone()['n'],1)

    def test_missing_channel_is_not_a_readable_source(self):
        from tenant_platform import _connect
        link=self.link();self.fact(link)
        with _connect() as conn:
            with conn.cursor() as cur:cur.execute('DELETE FROM saas_connect_channels WHERE tenant_id=%s AND connect_channel_id=%s',(self.tenant,self.channel))
            conn.commit()
        self.assertEqual(self.memory.read(self.profile,self.contact),{'links':[],'facts':[],'incomplete':True})

    def test_multiple_facts_same_source_and_duplicate_erasure_across_routes(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        link=self.link();first=self.fact(link,value='Uses Android',reliability='confirmed')
        independent=self.fact(link,category='topic',value='Often asks about reports')
        other_jid='994709876543@s.whatsapp.net';other_message=str(uuid.uuid4())
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,conversation_kind) VALUES(%s,%s,%s,'contact') RETURNING lead_id",(self.tenant,self.channel,other_jid))
                lead=cur.fetchone()['lead_id']
                cur.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,body,direction,message_type) VALUES(%s,%s,%s,%s,%s,'Second synthetic statement','incoming','text')",(other_message,self.tenant,lead,'connect:'+str(uuid.uuid4()),'connect:'+self.channel))
            conn.commit()
        other_link=self.memory.confirm_link(self.profile,self.contact,self.channel,other_jid,True)['id']
        self.fact(other_link,source_message_id=other_message,value='Uses Android',reliability='confirmed',group_fact_id=first['id'],group_version=1)
        self.assertEqual(len(self.memory.read(self.profile,self.contact)['facts']),3)
        self.memory.change_fact(self.profile,self.contact,first['id'],1,'delete')
        left=self.memory.read(self.profile,self.contact)['facts']
        self.assertEqual(left,[])
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT id FROM saas_customer_memory_facts WHERE id=%s',(independent['id'],));self.assertIsNotNone(cur.fetchone())
        with self.assertRaises(TenantLinearError):
            self.edit(independent['id'],1,'Uses Android')
        with self.assertRaises(TenantLinearError):self.fact(other_link,source_message_id=other_message)
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT count(*) AS n FROM saas_customer_memory_suppressions WHERE tenant_id=%s AND contact_id=%s',(self.tenant,self.contact))
                self.assertEqual(cur.fetchone()['n'],2)
                cur.execute("SELECT count(*) AS n FROM saas_customer_memory_fact_versions WHERE lower(value)=lower('Uses Android') AND fact_id IN (SELECT id FROM saas_customer_memory_facts WHERE tenant_id=%s AND contact_id=%s)",(self.tenant,self.contact))
                self.assertEqual(cur.fetchone()['n'],0)

    def test_edit_clears_previous_confirmation_and_sensitive_inputs(self):
        from tenant_linear_policy import TenantLinearError
        link=self.link();fact=self.fact(link,reliability='confirmed')
        self.assertIsNotNone(self.memory.read(self.profile,self.contact)['facts'][0]['last_confirmed_at'])
        self.edit(fact['id'],1,'Uses iOS')
        entry=self.memory.read(self.profile,self.contact)['facts'][0]
        self.assertEqual(entry['reliability'],'needs_review');self.assertIsNone(entry['last_confirmed_at'])
        for value in ('PIN 4321','recovery phrase alpha beta gamma','GB82 WEST 1234 5698 7654 32'):
            with self.subTest(value=value),self.assertRaises(TenantLinearError):
                self.edit(fact['id'],2,value)

    def test_provider_conflict_fences_concurrent_read(self):
        from tenant_platform import _connect
        from tenant_connect_dialog_equivalence import lock
        from threading import Event, Thread
        link=self.link();self.fact(link);started=Event();done=Event();out=[]
        def reader():
            started.set();out.append(self.memory.read(self.profile,self.contact));done.set()
        with _connect() as conn:
            with conn.cursor() as cur:
                lock(cur,self.tenant,self.channel,[self.jid])
                thread=Thread(target=reader);thread.start();self.assertTrue(started.wait(2))
                self.assertFalse(done.wait(.15))
                cur.execute("INSERT INTO saas_connect_identity_conflicts(tenant_id,connect_channel_id,conversation_id,event_id,evidence) VALUES(%s,%s,%s,'fixture','{}')",(self.tenant,self.channel,self.jid))
            conn.commit()
        thread.join(3);self.assertTrue(done.is_set())
        self.assertEqual(out,[{'links':[],'facts':[],'incomplete':True}])

    def test_existing_identity_ingestion_and_memory_read_do_not_deadlock(self):
        from tenant_platform import _connect
        from tenant_connect_direct_identity import canonical_conversation
        from tenant_crm_native_contacts import upsert_whatsapp_contact
        from threading import Event, Thread
        link=self.link();self.fact(link);started=Event();done=Event();out=[]
        number=self.jid.split('@')[0]
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO saas_crm_native_contact_identities(tenant_id,identity_type,identity_value,contact_id) VALUES(%s,'whatsapp_phone',%s,%s)",(self.tenant,number,self.contact))
            conn.commit()
        def reader():
            started.set();out.append(self.memory.read(self.profile,self.contact));done.set()
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL lock_timeout='2s'")
                canonical,conflict=canonical_conversation(cur,self.tenant,self.channel,self.jid,{}, {'kind':'contact'})
                self.assertFalse(conflict)
                thread=Thread(target=reader);thread.start();self.assertTrue(started.wait(2));self.assertFalse(done.wait(.15))
                self.assertEqual(upsert_whatsapp_contact(cur,self.tenant,self.channel,canonical,number),self.contact)
            conn.commit()
        thread.join(3);self.assertTrue(done.is_set());self.assertEqual(len(out[0]['facts']),1)

    def test_source_delete_and_concurrent_edit_have_consistent_lock_order(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        from threading import Event, Thread
        link=self.link();fact=self.fact(link);started=Event();done=Event();out=[]
        def editor():
            started.set()
            try:out.append(self.edit(fact['id'],1,'Uses iOS'))
            except TenantLinearError as exc:out.append(exc.status)
            done.set()
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL lock_timeout='2s'")
                cur.execute('SELECT id FROM saas_crm_messages WHERE id=%s FOR UPDATE',(self.message,))
                thread=Thread(target=editor);thread.start();self.assertTrue(started.wait(2));self.assertFalse(done.wait(.15))
                cur.execute('DELETE FROM saas_crm_messages WHERE id=%s',(self.message,))
            conn.commit()
        thread.join(3);self.assertTrue(done.is_set());self.assertEqual(out,[404])
        self.assertEqual(self.memory.read(self.profile,self.contact)['facts'],[])

    def test_deleted_message_replay_with_new_row_id_stays_suppressed(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        link=self.link();self.fact(link)
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT kommo_lead_id,external_id,channel FROM saas_crm_messages WHERE id=%s',(self.message,))
                source=cur.fetchone()
                cur.execute('DELETE FROM saas_crm_messages WHERE id=%s',(self.message,))
            conn.commit()
        self.message=str(uuid.uuid4())
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,body,direction,message_type) VALUES(%s,%s,%s,%s,%s,'Synthetic customer statement','incoming','text')",(self.message,self.tenant,source['kommo_lead_id'],source['external_id'],source['channel']))
            conn.commit()
        with self.assertRaises(TenantLinearError):self.fact(link)
        self.assertEqual(self.memory.read(self.profile,self.contact)['facts'],[])

    def test_key_rotation_does_not_silently_bypass_suppression(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        import base64
        link=self.link();fact=self.fact(link)
        self.memory.change_fact(self.profile,self.contact,fact['id'],1,'delete')
        old=os.environ['TENANT_ENCRYPTION_KEY']
        try:
            os.environ['TENANT_ENCRYPTION_KEY']=base64.urlsafe_b64encode(b'\x02'*32).decode()
            with self.assertRaises(TenantLinearError):self.fact(link)
        finally:os.environ['TENANT_ENCRYPTION_KEY']=old
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT source_token,token_key_id FROM saas_customer_memory_suppressions WHERE tenant_id=%s AND contact_id=%s',(self.tenant,self.contact))
                tombstone=cur.fetchone()
                self.assertEqual(len(tombstone['source_token']),64)
                self.assertEqual(len(tombstone['token_key_id']),24)

    def test_unicode_claim_suppression_across_categories_and_edits(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        link=self.link();first=self.fact(link,value='Straße');self.fact(link,category='profile',value='STRASSE')
        other=str(uuid.uuid4())
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT kommo_lead_id,channel FROM saas_crm_messages WHERE id=%s',(self.message,));source=cur.fetchone()
                cur.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,body,direction,message_type) VALUES(%s,%s,%s,%s,%s,'Synthetic','incoming','text')",(other,self.tenant,source['kommo_lead_id'],'connect:'+str(uuid.uuid4()),source['channel']))
            conn.commit()
        unrelated=self.fact(link,source_message_id=other,value='A different fact')
        self.memory.change_fact(self.profile,self.contact,first['id'],1,'delete')
        self.assertEqual(self.memory.read(self.profile,self.contact)['facts'],[])
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT count(*) AS n FROM saas_customer_memory_facts WHERE tenant_id=%s AND contact_id=%s',(self.tenant,self.contact));self.assertEqual(cur.fetchone()['n'],2)
        with self.assertRaises(TenantLinearError):self.edit(unrelated['id'],1,'STRASSE')

    def test_mixed_old_keys_cannot_abort_source_deletion(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        import base64
        link=self.link();self.fact(link,value='First synthetic fact')
        old=os.environ['TENANT_ENCRYPTION_KEY']
        try:
            os.environ['TENANT_ENCRYPTION_KEY']=base64.urlsafe_b64encode(b'\x02'*32).decode()
            with self.assertRaises(TenantLinearError):self.fact(link,value='Second synthetic fact')
        finally:os.environ['TENANT_ENCRYPTION_KEY']=old
        second=self.fact(link,value='Second synthetic fact')
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE saas_customer_memory_facts SET source_token='synthetic-other-token',token_key_id='synthetic-key' WHERE id=%s",(second['id'],))
                cur.execute('DELETE FROM saas_crm_messages WHERE id=%s',(self.message,))
            conn.commit()
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT source_token,token_key_id FROM saas_customer_memory_suppressions WHERE tenant_id=%s AND contact_id=%s',(self.tenant,self.contact))
                row=cur.fetchone();self.assertEqual(row,{'source_token':'','token_key_id':''})

    def test_legacy_blank_claim_deletes_only_selected_fact(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        link=self.link();first=self.fact(link,value='Legacy one');second=self.fact(link,value='Legacy two')
        with _connect() as conn:
            with conn.cursor() as cur:cur.execute("UPDATE saas_customer_memory_facts SET claim_token='',token_key_id='',claim_group_id=NULL WHERE id=ANY(%s::uuid[])",([first['id'],second['id']],))
            conn.commit()
        self.memory.change_fact(self.profile,self.contact,first['id'],1,'delete')
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT id FROM saas_customer_memory_facts WHERE id=%s',(second['id'],));self.assertIsNotNone(cur.fetchone())
        with self.assertRaises(TenantLinearError):self.fact(link,value='New fact')

    def test_contact_erasure_and_read_finish_without_deadlock(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        from threading import Event, Thread
        link=self.link();self.fact(link);started=Event();done=Event();out=[]
        def reader():
            started.set()
            try:out.append(self.memory.read(self.profile,self.contact))
            except TenantLinearError as exc:out.append(exc.status)
            done.set()
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL lock_timeout='2s'")
                cur.execute('SELECT id FROM saas_crm_native_contacts WHERE id=%s FOR UPDATE',(self.contact,))
                thread=Thread(target=reader);thread.start();self.assertTrue(started.wait(2));self.assertFalse(done.wait(.05))
                cur.execute('DELETE FROM saas_crm_native_contacts WHERE id=%s',(self.contact,))
            conn.commit()
        thread.join(3);self.assertTrue(done.is_set())
        self.assertIn(out[0],(404,503))

    def test_bulk_source_deletion_never_waits_for_owner_delete(self):
        from tenant_platform import _connect
        from threading import Event, Thread
        link=self.link();first=self.fact(link,value='Same fact');other=str(uuid.uuid4())
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT kommo_lead_id,channel FROM saas_crm_messages WHERE id=%s',(self.message,));src=cur.fetchone()
                cur.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,body,direction,message_type) VALUES(%s,%s,%s,%s,%s,'Synthetic','incoming','text')",(other,self.tenant,src['kommo_lead_id'],'connect:'+str(uuid.uuid4()),src['channel']))
            conn.commit()
        self.fact(link,source_message_id=other,value='Same fact',group_fact_id=first['id'],group_version=1)
        started=Event();done=Event();out=[]
        def remove_fact():
            started.set()
            try:out.append(self.memory.change_fact(self.profile,self.contact,first['id'],1,'delete'))
            except Exception as exc:out.append(type(exc).__name__)
            done.set()
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL lock_timeout='2s'")
                cur.execute('SELECT id FROM saas_crm_messages WHERE id=%s FOR UPDATE',(other,))
                thread=Thread(target=remove_fact);thread.start();self.assertTrue(started.wait(2))
                self.assertTrue(done.wait(2),'NOWAIT must release source locks before bulk deletion')
                cur.execute('DELETE FROM saas_crm_messages WHERE id=ANY(%s::uuid[])',([self.message,other],))
            conn.commit()
        thread.join(2);self.assertTrue(done.is_set());self.assertEqual(out,['LockNotAvailable'])

    def test_source_delete_suppresses_prior_claim_versions_in_other_sources(self):
        from tenant_platform import _connect
        link=self.link();first=self.fact(link,value='Original synthetic fact')
        self.edit(first['id'],1,'Changed synthetic fact')
        other=str(uuid.uuid4())
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT kommo_lead_id,channel FROM saas_crm_messages WHERE id=%s',(self.message,));src=cur.fetchone()
                cur.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,body,direction,message_type) VALUES(%s,%s,%s,%s,%s,'Synthetic','incoming','text')",(other,self.tenant,src['kommo_lead_id'],'connect:'+str(uuid.uuid4()),src['channel']))
            conn.commit()
        self.fact(link,source_message_id=other,value='Original synthetic fact')
        with _connect() as conn:
            with conn.cursor() as cur:cur.execute('DELETE FROM saas_crm_messages WHERE id=%s',(self.message,))
            conn.commit()
        self.assertEqual(self.memory.read(self.profile,self.contact)['facts'],[])
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT count(*) AS n FROM saas_customer_memory_facts WHERE tenant_id=%s AND contact_id=%s',(self.tenant,self.contact));self.assertEqual(cur.fetchone()['n'],1)

    def test_new_alias_between_snapshot_and_lock_is_rejected(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        from tenant_connect_direct_identity import canonical_conversation
        import tenant_connect_dialog_equivalence as equivalence
        from unittest.mock import patch
        from threading import Event, Thread
        link=self.link();self.fact(link);snapshot=Event();continue_lock=Event();done=Event();out=[]
        original=equivalence.lock
        def delayed(cur,tenant,channel,identities):
            snapshot.set();self.assertTrue(continue_lock.wait(2));return original(cur,tenant,channel,identities)
        def reader():
            try:out.append(self.memory.read(self.profile,self.contact))
            except TenantLinearError as exc:out.append(exc.status)
            except Exception as exc:out.append(type(exc).__name__)
            done.set()
        with patch.object(equivalence,'lock',delayed):
            thread=Thread(target=reader);thread.start();self.assertTrue(snapshot.wait(2))
            with _connect() as conn:
                with conn.cursor() as cur:
                    pair={'directIdentity':{'jid':self.jid,'lid':'994701234567@lid'}}
                    route,conflict=canonical_conversation(cur,self.tenant,self.channel,self.jid,pair,{'kind':'contact'})
                    self.assertFalse(conflict);self.assertEqual(route,self.jid)
                conn.commit()
            continue_lock.set();thread.join(3)
        self.assertTrue(done.is_set());self.assertEqual(out,[409])

    def test_sorted_identity_locks_complete_against_reconciliation(self):
        from tenant_platform import _connect
        from tenant_connect_dialog_equivalence import lock
        from threading import Event, Thread
        first=self.link();self.fact(first);other_jid='994709876543@s.whatsapp.net'
        with _connect() as conn:
            with conn.cursor() as cur:cur.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,conversation_kind) VALUES(%s,%s,%s,'contact')",(self.tenant,self.channel,other_jid))
            conn.commit()
        self.memory.confirm_link(self.profile,self.contact,self.channel,other_jid,True)
        started=Event();done=Event();out=[]
        def reader():
            started.set()
            try:out.append(self.memory.read(self.profile,self.contact))
            except Exception as exc:out.append(type(exc).__name__)
            done.set()
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL lock_timeout='2s'")
                first_id,second_id=sorted([self.jid,other_jid])
                lock(cur,self.tenant,self.channel,[first_id])
                thread=Thread(target=reader);thread.start();self.assertTrue(started.wait(2));self.assertFalse(done.wait(.04))
                lock(cur,self.tenant,self.channel,[second_id])
            conn.commit()
        thread.join(3);self.assertTrue(done.is_set());self.assertIsInstance(out[0],dict)

    def test_source_cleanup_index_exists_for_legacy_bulk_deletion(self):
        from tenant_platform import _connect
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT indexdef FROM pg_indexes WHERE indexname='saas_customer_memory_source_lookup'")
                self.assertIn('(tenant_id, source_message_id)',cur.fetchone()['indexdef'])

    def test_conflicting_crm_mapping_and_reassignment_fence_memory(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO saas_crm_native_contact_conversations(tenant_id,provider,channel_id,conversation_id,contact_id) VALUES(%s,'connect',%s,%s,%s)",(self.tenant,self.channel,self.jid,self.contact2))
            conn.commit()
        with self.assertRaises(TenantLinearError):self.link()
        with _connect() as conn:
            with conn.cursor() as cur:cur.execute("UPDATE saas_crm_native_contact_conversations SET contact_id=%s WHERE tenant_id=%s AND channel_id=%s AND conversation_id=%s",(self.contact,self.tenant,self.channel,self.jid))
            conn.commit()
        link=self.link();self.fact(link)
        with _connect() as conn:
            with conn.cursor() as cur:cur.execute("UPDATE saas_crm_native_contact_conversations SET contact_id=%s WHERE tenant_id=%s AND channel_id=%s AND conversation_id=%s",(self.contact,self.tenant,self.channel,self.jid))
            conn.commit()
        self.assertEqual(len(self.memory.read(self.profile,self.contact)['facts']),1)
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE saas_crm_native_contact_conversations SET contact_id=%s WHERE tenant_id=%s AND channel_id=%s AND conversation_id=%s",(self.contact2,self.tenant,self.channel,self.jid))
                cur.execute("UPDATE saas_crm_native_contact_conversations SET contact_id=%s WHERE tenant_id=%s AND channel_id=%s AND conversation_id=%s",(self.contact,self.tenant,self.channel,self.jid))
            conn.commit()
        self.assertEqual(self.memory.read(self.profile,self.contact)['facts'],[])
        with self.assertRaises(TenantLinearError):self.link()
        self.memory.revoke_link(self.profile,self.contact,link,1)
        new=self.link();self.assertNotEqual(new,link)
        self.assertEqual(self.memory.read(self.profile,self.contact)['facts'],[])

    def test_same_text_for_distinct_subject_attributes_is_not_deleted(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        link=self.link();stores=self.fact(link,category='profile',value='3')
        with self.assertRaises(TenantLinearError):self.fact(link,category='profile',value='3')
        registers=self.fact(link,category='profile',value='3',distinct_confirmation=True)
        self.memory.change_fact(self.profile,self.contact,stores['id'],1,'delete')
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT id FROM saas_customer_memory_facts WHERE tenant_id=%s AND id=%s',(self.tenant,registers['id']))
                self.assertIsNotNone(cur.fetchone())

    def test_crm_route_key_move_and_back_invalidates_old_binding(self):
        from tenant_platform import _connect
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO saas_crm_native_contact_conversations(tenant_id,provider,channel_id,conversation_id,contact_id) VALUES(%s,'connect',%s,%s,%s)",(self.tenant,self.channel,self.jid,self.contact))
            conn.commit()
        link=self.link();self.fact(link)
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE saas_crm_native_contact_conversations SET conversation_id='994700000001@s.whatsapp.net' WHERE tenant_id=%s AND channel_id=%s AND conversation_id=%s",(self.tenant,self.channel,self.jid))
                cur.execute("UPDATE saas_crm_native_contact_conversations SET conversation_id=%s WHERE tenant_id=%s AND channel_id=%s AND conversation_id='994700000001@s.whatsapp.net'",(self.jid,self.tenant,self.channel))
            conn.commit()
        self.assertEqual(self.memory.read(self.profile,self.contact)['facts'],[])

    def test_read_does_not_return_after_source_deletion_commits(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        from unittest.mock import patch
        from threading import Event, Thread
        link=self.link();self.fact(link,value='First source')
        second=str(uuid.uuid4())
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT kommo_lead_id,channel FROM saas_crm_messages WHERE id=%s',(self.message,));src=cur.fetchone()
                cur.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,body,direction,message_type) VALUES(%s,%s,%s,%s,%s,'Synthetic','incoming','text')",(second,self.tenant,src['kommo_lead_id'],'connect:'+str(uuid.uuid4()),src['channel']))
            conn.commit()
        self.fact(link,source_message_id=second,value='Second source')
        paused=Event();resume=Event();done=Event();out=[];original=self.memory._source
        def delayed(cur,tenant,linked,source_id):
            if str(source_id)==second:
                paused.set();self.assertTrue(resume.wait(2))
            return original(cur,tenant,linked,source_id)
        def read():
            try:out.append(self.memory.read(self.profile,self.contact))
            except TenantLinearError as exc:out.append(exc.status)
            except Exception as exc:out.append(type(exc).__name__)
            done.set()
        with patch.object(self.memory,'_source',delayed):
            thread=Thread(target=read);thread.start();self.assertTrue(paused.wait(2))
            with _connect() as conn:
                with conn.cursor() as cur:cur.execute('DELETE FROM saas_crm_messages WHERE id=%s',(self.message,))
                conn.commit()
            resume.set();thread.join(3)
        self.assertTrue(done.is_set());self.assertIn(out[0],(409,503))

    def test_inflight_add_rolls_back_after_source_erasure(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        from unittest.mock import patch
        from threading import Event, Thread
        link=self.link();self.fact(link,value='First source')
        second=str(uuid.uuid4())
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT kommo_lead_id,channel FROM saas_crm_messages WHERE id=%s',(self.message,));src=cur.fetchone()
                cur.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,body,direction,message_type) VALUES(%s,%s,%s,%s,%s,'Synthetic','incoming','text')",(second,self.tenant,src['kommo_lead_id'],'connect:'+str(uuid.uuid4()),src['channel']))
            conn.commit()
        paused=Event();resume=Event();done=Event();out=[];original=self.memory._assert_claim_allowed
        def delayed(*args):
            original(*args);paused.set();self.assertTrue(resume.wait(2))
        def add():
            try:out.append(self.fact(link,source_message_id=second,value='In-flight fact'))
            except TenantLinearError as exc:out.append(exc.status)
            except Exception as exc:out.append(type(exc).__name__)
            done.set()
        with patch.object(self.memory,'_assert_claim_allowed',delayed):
            thread=Thread(target=add);thread.start();self.assertTrue(paused.wait(2))
            with _connect() as conn:
                with conn.cursor() as cur:cur.execute('DELETE FROM saas_crm_messages WHERE id=%s',(self.message,))
                conn.commit()
            resume.set();thread.join(3)
        self.assertTrue(done.is_set());self.assertIn(out[0],(409,503))
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT count(*) AS n FROM saas_customer_memory_facts WHERE tenant_id=%s AND contact_id=%s',(self.tenant,self.contact))
                self.assertEqual(cur.fetchone()['n'],0)

    def test_inflight_edit_rolls_back_after_source_erasure(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        from unittest.mock import patch
        from threading import Event, Thread
        link=self.link();self.fact(link,value='First source');second=str(uuid.uuid4())
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT kommo_lead_id,channel FROM saas_crm_messages WHERE id=%s',(self.message,));src=cur.fetchone()
                cur.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,body,direction,message_type) VALUES(%s,%s,%s,%s,%s,'Synthetic','incoming','text')",(second,self.tenant,src['kommo_lead_id'],'connect:'+str(uuid.uuid4()),src['channel']))
            conn.commit()
        target=self.fact(link,source_message_id=second,value='Before edit')
        paused=Event();resume=Event();done=Event();out=[];original=self.memory._assert_claim_allowed
        def delayed(*args):original(*args);paused.set();self.assertTrue(resume.wait(2))
        def edit():
            try:out.append(self.edit(target['id'],1,'In-flight edit'))
            except TenantLinearError as exc:out.append(exc.status)
            except Exception as exc:out.append(type(exc).__name__)
            done.set()
        with patch.object(self.memory,'_assert_claim_allowed',delayed):
            thread=Thread(target=edit);thread.start();self.assertTrue(paused.wait(2))
            with _connect() as conn:
                with conn.cursor() as cur:cur.execute('DELETE FROM saas_crm_messages WHERE id=%s',(self.message,))
                conn.commit()
            resume.set();thread.join(3)
        self.assertTrue(done.is_set());self.assertIn(out[0],(409,503))
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT value,version FROM saas_customer_memory_facts WHERE id=%s',(target['id'],))
                self.assertEqual(cur.fetchone(),{'value':'Before edit','version':1})

    def test_contact_erasure_and_source_delete_do_not_deadlock(self):
        from tenant_platform import _connect
        from threading import Event,Thread
        link=self.link();self.fact(link);started=Event();done=Event();out=[]
        def erase_source():
            started.set()
            try:
                with _connect() as c:
                    with c.cursor() as cur:
                        cur.execute("SET LOCAL lock_timeout='2s'")
                        cur.execute('DELETE FROM saas_crm_messages WHERE id=%s',(self.message,))
                    c.commit()
                out.append('done')
            except Exception as exc:out.append(type(exc).__name__)
            done.set()
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL lock_timeout='2s'")
                cur.execute('SELECT id FROM saas_crm_native_contacts WHERE id=%s FOR UPDATE',(self.contact,))
                thread=Thread(target=erase_source);thread.start();self.assertTrue(started.wait(2));self.assertFalse(done.wait(.1))
                cur.execute('DELETE FROM saas_crm_native_contacts WHERE id=%s',(self.contact,))
            conn.commit()
        thread.join(3);self.assertTrue(done.is_set());self.assertEqual(out,['done'])

    def test_post_erasure_new_evidence_recovers_without_old_or_late_history(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        link=self.link();old=self.fact(link,value='Old synthetic fact')
        self.memory.change_fact(self.profile,self.contact,old['id'],1,'delete')
        fresh=str(uuid.uuid4());late=str(uuid.uuid4())
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT kommo_lead_id,channel FROM saas_crm_messages WHERE id=%s',(self.message,));src=cur.fetchone()
                for mid,happened in ((fresh,'now()'),(late,"'2020-01-01T00:00:00Z'::timestamptz")):
                    cur.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,body,direction,message_type,happened_at) VALUES(%s,%s,%s,%s,%s,'New synthetic message','incoming','text',"+happened+")",(mid,self.tenant,src['kommo_lead_id'],'connect:'+str(uuid.uuid4()),src['channel']))
            conn.commit()
        with self.assertRaises(TenantLinearError):self.fact(link,source_message_id=late,value='Late historical fact')
        self.fact(link,source_message_id=fresh,value='New confirmed need')
        self.assertEqual([x['value'] for x in self.memory.read(self.profile,self.contact)['facts']],['New confirmed need'])

    def test_message_inserted_before_erasure_commit_is_not_new_evidence(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        link=self.link();self.fact(link,value='Old fact');second=str(uuid.uuid4())
        with _connect() as deletion:
            with deletion.cursor() as cur:
                cur.execute('SELECT kommo_lead_id,channel FROM saas_crm_messages WHERE id=%s',(self.message,));src=cur.fetchone()
                cur.execute('DELETE FROM saas_crm_messages WHERE id=%s',(self.message,))
                with _connect() as arrival:
                    with arrival.cursor() as a:
                        a.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,body,direction,message_type,happened_at) VALUES(%s,%s,%s,%s,%s,'Concurrent synthetic','incoming','text',clock_timestamp())",(second,self.tenant,src['kommo_lead_id'],'connect:'+str(uuid.uuid4()),src['channel']))
                    arrival.commit()
            deletion.commit()
        with self.assertRaises(TenantLinearError):self.fact(link,source_message_id=second,value='Not new evidence')

    def test_contact_erasure_fences_recreated_subject_and_old_source(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        link=self.link();self.fact(link)
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('DELETE FROM saas_crm_native_contacts WHERE tenant_id=%s AND id=%s',(self.tenant,self.contact))
                cur.execute("INSERT INTO saas_crm_native_contacts(id,tenant_id,display_name) VALUES(%s,%s,'Recreated fixture')",(self.contact,self.tenant))
            conn.commit()
        with self.assertRaises(TenantLinearError):self.link()
        with self.assertRaises(TenantLinearError):
            self.memory.confirm_link(self.profile,self.contact2,self.channel,self.jid,True)
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT subject_token,token_key_id FROM saas_customer_memory_erased_subjects WHERE tenant_id=%s',(self.tenant,))
                subject=cur.fetchone();self.assertEqual(len(subject['subject_token']),64)
                cur.execute('SELECT source_token,token_key_id FROM saas_customer_memory_erased_sources WHERE tenant_id=%s',(self.tenant,))
                source=cur.fetchone();self.assertEqual(len(source['source_token']),64)

    def test_tenant_erasure_does_not_leave_memory_fences(self):
        from tenant_platform import _connect
        link=self.link();self.fact(link)
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('DELETE FROM saas_tenants WHERE id=%s',(self.tenant,))
                cur.execute('SELECT count(*) AS n FROM saas_customer_memory_erased_sources WHERE tenant_id=%s',(self.tenant,))
                self.assertEqual(cur.fetchone()['n'],0)
            conn.commit()

    def test_exact_quote_and_employee_attestation_are_required(self):
        from tenant_linear_policy import TenantLinearError
        from tenant_platform import _connect
        link=self.link();request={'request_id':str(uuid.uuid4()),'link_id':link,'source_message_id':self.message,
          'category':'feature','value':'Synthetic fact','source_kind':'customer_statement','reliability':'needs_review',
          'evidence_quote':'Not in source','evidence_confirmed':True}
        with self.assertRaises(TenantLinearError):self.memory.add_fact(self.profile,self.contact,request)
        request['evidence_quote']='Synthetic customer statement';request['evidence_confirmed']=False
        with self.assertRaises(TenantLinearError):self.memory.add_fact(self.profile,self.contact,request)
        request['evidence_confirmed']=True;result=self.memory.add_fact(self.profile,self.contact,request)
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT evidence_quote_token FROM saas_customer_memory_facts WHERE id=%s',(result['id'],))
                token=cur.fetchone()['evidence_quote_token'];self.assertEqual(len(token),64)
                self.assertNotIn(request['evidence_quote'],token)

    def test_equivalent_phone_routes_cannot_be_attested_to_different_contacts(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        alternate=self.jid.replace('@s.whatsapp.net','@c.us')
        with _connect() as conn:
            with conn.cursor() as cur:cur.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,conversation_kind) VALUES(%s,%s,%s,'contact')",(self.tenant,self.channel,alternate))
            conn.commit()
        link=self.link()
        with self.assertRaises(TenantLinearError):self.memory.confirm_link(self.profile,self.contact2,self.channel,alternate,True)
        self.memory.revoke_link(self.profile,self.contact,link,1)
        other=self.memory.confirm_link(self.profile,self.contact2,self.channel,alternate,True)
        self.assertTrue(other['id'])

    def test_contact_erasure_fences_equivalent_phone_route(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        alternate=self.jid.replace('@s.whatsapp.net','@c.us');self.link()
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('DELETE FROM saas_crm_native_contacts WHERE tenant_id=%s AND id=%s',(self.tenant,self.contact))
                cur.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,conversation_kind) VALUES(%s,%s,%s,'contact')",(self.tenant,self.channel,alternate))
            conn.commit()
        with self.assertRaises(TenantLinearError):self.memory.confirm_link(self.profile,self.contact2,self.channel,alternate,True)

    def test_verified_lid_and_jid_cannot_bind_two_contacts_in_either_order(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        lid='994701234567@lid'
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,conversation_kind) VALUES(%s,%s,%s,'contact')",(self.tenant,self.channel,lid))
                cur.execute('INSERT INTO saas_connect_direct_identities(tenant_id,connect_channel_id,identity_key,canonical_conversation_id) VALUES(%s,%s,%s,%s)',(self.tenant,self.channel,lid,self.jid))
            conn.commit()
        first=self.link()
        with self.assertRaises(TenantLinearError):self.memory.confirm_link(self.profile,self.contact2,self.channel,lid,True)
        self.memory.revoke_link(self.profile,self.contact,first,1)
        second=self.memory.confirm_link(self.profile,self.contact2,self.channel,lid,True)['id']
        with self.assertRaises(TenantLinearError):self.link()
        self.assertTrue(second)

    def test_optional_generation_capture_does_not_block_message_ingest(self):
        from tenant_platform import _connect
        from threading import Event,Thread
        import time
        self.link();mid=str(uuid.uuid4());done=Event();out=[]
        with _connect() as hold:
            with hold.cursor() as cur:
                cur.execute('SELECT kommo_lead_id,channel FROM saas_crm_messages WHERE id=%s',(self.message,));src=cur.fetchone()
                cur.execute('SELECT id FROM saas_crm_native_contacts WHERE id=%s FOR UPDATE',(self.contact,))
                def insert():
                    start=time.monotonic()
                    try:
                        with _connect() as conn:
                            with conn.cursor() as c:
                                c.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,body,direction,message_type,happened_at) VALUES(%s,%s,%s,%s,%s,'Synthetic','incoming','text',now())",(mid,self.tenant,src['kommo_lead_id'],'connect:'+str(uuid.uuid4()),src['channel']))
                            conn.commit()
                        out.append(('done',time.monotonic()-start))
                    except Exception as exc:out.append((type(exc).__name__,time.monotonic()-start))
                    done.set()
                thread=Thread(target=insert);thread.start();self.assertTrue(done.wait(1),'optional memory must not block ingestion')
            hold.rollback()
        thread.join(2);self.assertEqual(out[0][0],'done');self.assertLess(out[0][1],.8)
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT 1 FROM saas_customer_memory_source_generations WHERE tenant_id=%s AND source_message_id=%s',(self.tenant,mid))
                self.assertIsNone(cur.fetchone())

    def test_erasure_fences_lid_after_provider_split(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        lid='994701234567@lid'
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,conversation_kind) VALUES(%s,%s,%s,'contact')",(self.tenant,self.channel,lid))
                cur.execute('INSERT INTO saas_connect_direct_identities(tenant_id,connect_channel_id,identity_key,canonical_conversation_id) VALUES(%s,%s,%s,%s)',(self.tenant,self.channel,lid,self.jid))
            conn.commit()
        self.link()
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('DELETE FROM saas_crm_native_contacts WHERE tenant_id=%s AND id=%s',(self.tenant,self.contact))
                cur.execute('UPDATE saas_connect_direct_identities SET canonical_conversation_id=%s WHERE tenant_id=%s AND connect_channel_id=%s AND identity_key=%s',(lid,self.tenant,self.channel,lid))
            conn.commit()
        with self.assertRaises(TenantLinearError):self.memory.confirm_link(self.profile,self.contact2,self.channel,lid,True)

    def test_new_provider_pair_fences_both_existing_subjects(self):
        from tenant_platform import _connect
        lid='994701234567@lid'
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,conversation_kind) VALUES(%s,%s,%s,'contact')",(self.tenant,self.channel,lid))
            conn.commit()
        first=self.link();self.fact(first)
        self.memory.confirm_link(self.profile,self.contact2,self.channel,lid,True)
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('INSERT INTO saas_connect_direct_identities(tenant_id,connect_channel_id,identity_key,canonical_conversation_id) VALUES(%s,%s,%s,%s)',(self.tenant,self.channel,lid,self.jid))
            conn.commit()
        for contact in (self.contact,self.contact2):
            view=self.memory.read(self.profile,contact)
            self.assertEqual(view['facts'],[])
            self.assertEqual(view['links'],[])

    def test_channel_erasure_fences_existing_other_subject(self):
        from tenant_platform import _connect
        other='994709876543@s.whatsapp.net'
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,conversation_kind) VALUES(%s,%s,%s,'contact')",(self.tenant,self.channel,other))
            conn.commit()
        self.link();self.memory.confirm_link(self.profile,self.contact2,self.channel,other,True)
        with _connect() as conn:
            with conn.cursor() as cur:cur.execute('DELETE FROM saas_crm_native_contacts WHERE id=%s',(self.contact,))
            conn.commit()
        view=self.memory.read(self.profile,self.contact2)
        self.assertEqual(view['links'],[]);self.assertEqual(view['facts'],[])

    def test_inflight_channel_read_prevents_erasure_commit(self):
        from tenant_platform import _connect
        import psycopg
        self.link()
        gate='customer-memory-channel:'+self.tenant+':'+self.channel
        with _connect() as held:
            with held.cursor() as cur:
                cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(gate,))
                with self.assertRaises(psycopg.errors.LockNotAvailable):
                    with _connect() as deleting:
                        with deleting.cursor() as other:other.execute('DELETE FROM saas_crm_native_contacts WHERE id=%s',(self.contact,))
                        deleting.commit()
            held.rollback()
        with _connect() as conn:
            with conn.cursor() as cur:cur.execute('DELETE FROM saas_crm_native_contacts WHERE id=%s',(self.contact,))
            conn.commit()

    def test_shared_channel_gate_allows_independent_readers(self):
        from tenant_platform import _connect
        self.link()
        gate='customer-memory-channel:'+self.tenant+':'+self.channel
        with _connect() as held:
            with held.cursor() as cur:
                cur.execute('SELECT pg_advisory_xact_lock_shared(hashtextextended(%s,0))',(gate,))
                self.assertEqual(len(self.memory.read(self.profile,self.contact)['links']),1)
            held.rollback()

    def test_busy_channel_is_temporary_error_not_empty_memory(self):
        from tenant_platform import _connect
        from tenant_linear_policy import TenantLinearError
        self.link();gate='customer-memory-channel:'+self.tenant+':'+self.channel
        with _connect() as held:
            with held.cursor() as cur:
                cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(gate,))
                with self.assertRaises(TenantLinearError) as result:self.memory.read(self.profile,self.contact)
                self.assertEqual(result.exception.status,503)
            held.rollback()
