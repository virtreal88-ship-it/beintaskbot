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
    if params.get('host') not in {'localhost','127.0.0.1','::1'} or params.get('service'):
        raise RuntimeError('Customer memory tests require loopback PostgreSQL')


@unittest.skipUnless(DSN,'CUSTOMER_MEMORY_TEST_DSN is not set')
class CustomerMemoryPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_db=os.environ.get('DATABASE_URL');cls.original_flag=os.environ.get('CUSTOMER_MEMORY_ENABLED')
        os.environ['DATABASE_URL']=DSN;os.environ['CUSTOMER_MEMORY_ENABLED']='1'
        from tenant_platform import _connect,_ensure_schema
        from tenant_connect_transport import _schema
        with _connect() as conn:
            _ensure_schema(conn);_schema(conn);conn.commit()

    @classmethod
    def tearDownClass(cls):
        for key,value in (('DATABASE_URL',cls.original_db),('CUSTOMER_MEMORY_ENABLED',cls.original_flag)):
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
                cur.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,body,direction,message_type) VALUES(%s,%s,%s,%s,%s,'Synthetic customer statement','incoming','text')",(self.message,self.tenant,lead,str(uuid.uuid4()),'connect:'+self.channel))
            conn.commit()
        import tenant_customer_memory as memory
        self.memory=memory

    def link(self):
        return self.memory.confirm_link(self.profile,self.contact,self.channel,self.jid,True)['id']

    def fact(self,link,**changes):
        data={'request_id':str(uuid.uuid4()),'link_id':link,'source_message_id':self.message,
              'category':'feature','value':'Uses Android','source_kind':'customer_statement','reliability':'needs_review'}
        data.update(changes)
        return self.memory.add_fact(self.profile,self.contact,data)

    def test_manual_edit_conflict_and_delete_suppression(self):
        from tenant_linear_policy import TenantLinearError
        from tenant_platform import _connect
        link=self.link(); fact=self.fact(link); item=fact['id']
        self.assertEqual(self.memory.read(self.profile,self.contact)['facts'][0]['value'],'Uses Android')
        self.assertEqual(self.memory.change_fact(self.profile,self.contact,item,1,'edit',value='Uses iOS')['version'],2)
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
        self.assertEqual(self.memory.read(self.profile,self.contact),{'links':[],'facts':[]})
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
            try:return self.memory.change_fact(self.profile,self.contact,fact['id'],1,'edit',value=value)['version']
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
        self.assertEqual(self.memory.read(self.profile,self.contact),{'links':[],'facts':[]})

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
        self.assertEqual(self.memory.read(self.profile,self.contact),{'links':[],'facts':[]})

    def test_owner_can_delete_hidden_fact_after_revocation_or_source_loss(self):
        from tenant_platform import _connect
        link=self.link();fact=self.fact(link)
        self.memory.revoke_link(self.profile,self.contact,link,1)
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('DELETE FROM saas_crm_messages WHERE id=%s',(self.message,))
                cur.execute('UPDATE saas_crm_native_contacts SET archived_at=now() WHERE id=%s',(self.contact,))
            conn.commit()
        self.assertEqual(self.memory.change_fact(self.profile,self.contact,fact['id'],1,'delete'),{'deleted':True})
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT count(*) AS n FROM saas_customer_memory_fact_versions WHERE fact_id=%s',(fact['id'],))
                self.assertEqual(cur.fetchone()['n'],0)

    def test_missing_channel_is_not_a_readable_source(self):
        from tenant_platform import _connect
        link=self.link();self.fact(link)
        with _connect() as conn:
            with conn.cursor() as cur:cur.execute('DELETE FROM saas_connect_channels WHERE tenant_id=%s AND connect_channel_id=%s',(self.tenant,self.channel))
            conn.commit()
        self.assertEqual(self.memory.read(self.profile,self.contact),{'links':[],'facts':[]})
