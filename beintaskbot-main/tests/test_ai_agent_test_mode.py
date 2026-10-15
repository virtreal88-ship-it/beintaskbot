import sys
import types
import unittest
from unittest.mock import MagicMock, patch

if 'psycopg' not in sys.modules:
    psycopg=types.ModuleType('psycopg'); psycopg.connect=MagicMock()
    rows=types.ModuleType('psycopg.rows'); rows.dict_row=object()
    sys.modules['psycopg']=psycopg; sys.modules['psycopg.rows']=rows

import tenant_ai_agent_test_mode as test_mode
import tenant_ai_agent_queue as queue
from tenant_linear_policy import TenantLinearError


TENANT='a9c11a16-01d5-469a-a6c1-9a726975ae42'
BINDING={'tenant_id':TENANT,'enabled':True,'emergency_stop':False,'verified':True,
 'company_phone_attested':True,'company_phone':'+15555550102','client_phone':'+15555550101',
 'account_domain':'test-account.kommo.com','channel_id':'whatsapp-origin','lead_id':7001,
 'contact_id':8001,'talk_id':'9001','chat_id':'chat-9001'}


class TestModeBindingTests(unittest.TestCase):
    def setUp(self):
        self.client_phone = patch.object(test_mode, 'CLIENT_PHONE', '+15555550101')
        self.company_phone = patch.object(test_mode, 'COMPANY_PHONE', '+15555550102')
        self.client_phone.start(); self.company_phone.start()
        self.addCleanup(self.company_phone.stop); self.addCleanup(self.client_phone.stop)

    def test_missing_server_allowlist_fails_closed(self):
        with patch.object(test_mode, 'CLIENT_PHONE', ''), patch.object(test_mode, 'COMPANY_PHONE', ''):
            self.assertFalse(test_mode.exact_binding(BINDING, tenant_id=TENANT, account_domain=BINDING['account_domain'],
                lead_id=7001, contact_id=8001, talk_id='9001', chat_id='chat-9001', channel_id='whatsapp-origin'))

    def test_complete_confirmed_binding_matches(self):
        self.assertTrue(test_mode.exact_binding(BINDING,tenant_id=TENANT,
            account_domain=BINDING['account_domain'],lead_id=7001,contact_id=8001,
            talk_id='9001',chat_id='chat-9001',channel_id='WHATSAPP-ORIGIN'))

    def test_fail_closed_for_wrong_client_or_incomplete_or_unverified_binding(self):
        for key,value in [('client_phone','+15555550103'),('verified',False),
                          ('company_phone_attested',False),('contact_id',None),
                          ('talk_id',''),('chat_id',None)]:
            with self.subTest(key=key):
                binding={**BINDING,key:value}
                self.assertFalse(test_mode.exact_binding(binding,tenant_id=TENANT,
                    account_domain=BINDING['account_domain'],lead_id=7001,contact_id=8001,
                    talk_id='9001',chat_id='chat-9001',channel_id='whatsapp-origin'))

    def test_rejects_other_tenant_account_channel_contact_deal_talk_and_chat(self):
        cases=[{'tenant_id':'another-tenant'},{'account_domain':'other.kommo.com'},
            {'channel_id':'instagram-origin'},{'contact_id':8002},{'lead_id':7002},
            {'talk_id':'9002'},{'chat_id':'other-chat'}]
        for change in cases:
            with self.subTest(change=change):
                self.assertFalse(test_mode.exact_binding({**BINDING,**change},tenant_id=TENANT,
                    account_domain=BINDING['account_domain'],lead_id=7001,contact_id=8001,
                    talk_id='9001',chat_id='chat-9001',channel_id='whatsapp-origin'))

    def test_emergency_stop_rejects_existing_job(self):
        job={'tenant_id':TENANT,'agent_id':'agent-1','lead_id':7001,'talk_id':'9001',
             'chat_id':'chat-9001','channel_id':'whatsapp-origin','payload':{'contact_id':8001}}
        conn=MagicMock(); conn.__enter__.return_value=conn
        cur=MagicMock(); cur.__enter__.return_value=cur; conn.cursor.return_value=cur
        cur.fetchone.side_effect=[{**BINDING,'tenant_id':TENANT,'emergency_stop':True,'connected_account_domain':BINDING['account_domain']},
            {'account_domain':BINDING['account_domain']}, None]
        with patch.object(test_mode,'_connect',return_value=conn),patch.object(test_mode,'ensure_schema'):
            with self.assertRaises(TenantLinearError): test_mode.assert_job_allowed(job)

    def test_agent_disabled_during_processing_rejects_job(self):
        job={'tenant_id':TENANT,'agent_id':'agent-1','lead_id':7001,'talk_id':'9001',
             'chat_id':'chat-9001','channel_id':'whatsapp-origin','payload':{'contact_id':8001}}
        conn=MagicMock(); conn.__enter__.return_value=conn
        cur=MagicMock(); cur.__enter__.return_value=cur; conn.cursor.return_value=cur
        cur.fetchone.side_effect=[{**BINDING,'tenant_id':TENANT,'connected_account_domain':BINDING['account_domain']},
            {'account_domain':BINDING['account_domain']}, {}, None]
        with patch.object(test_mode,'_connect',return_value=conn),patch.object(test_mode,'ensure_schema'):
            with self.assertRaises(TenantLinearError): test_mode.assert_job_allowed(job)

    def test_webhook_requires_full_exact_match_and_connected_account(self):
        cursor=MagicMock()
        binding={**BINDING,'tenant_id':TENANT,'connected_account_domain':BINDING['account_domain']}
        event={'lead_id':7001,'contact_id':8001,'talk_id':'9001','chat_id':'chat-9001','channel':'whatsapp-origin'}
        with patch.object(test_mode,'_db_binding',return_value=binding):
            self.assertTrue(test_mode.allowed_event(cursor,TENANT,BINDING['account_domain'],event))
            self.assertFalse(test_mode.allowed_event(cursor,TENANT,'other.kommo.com',event))
            for key,value in [('lead_id',7002),('contact_id',8002),('talk_id','old-talk'),
                              ('chat_id','other-chat'),('channel','instagram')]:
                with self.subTest(key=key):
                    self.assertFalse(test_mode.allowed_event(cursor,TENANT,BINDING['account_domain'],{**event,key:value}))

    def test_webhook_without_contact_or_channel_is_rejected_before_enqueue(self):
        incomplete={'entity_id':7001,'talk_id':'9001','chat_id':'chat-9001','id':'message-1'}
        with patch.object(queue,'_connect',side_effect=AssertionError('incomplete event must not touch DB')):
            self.assertEqual(queue.ingest_kommo_messages({'account':{'subdomain':'test-account'}},[incomplete]),0)

    def test_active_mode_allows_only_the_connected_tenant_account(self):
        cursor=MagicMock()
        cursor.fetchone.side_effect=[{'operating_mode':'active','enabled':True,'emergency_stop':False,'status':'active'},{'ok':1}]
        event={'lead_id':7002,'contact_id':8002,'talk_id':'9002','chat_id':'another-chat','channel':'whatsapp'}
        self.assertTrue(test_mode.allowed_event(cursor,TENANT,BINDING['account_domain'],event))
        self.assertEqual(cursor.execute.call_count,2)

    def test_active_mode_emergency_stop_blocks_before_account_lookup(self):
        cursor=MagicMock(); cursor.fetchone.return_value={'operating_mode':'active','enabled':True,'emergency_stop':True,'status':'active'}
        self.assertFalse(test_mode.allowed_event(cursor,TENANT,BINDING['account_domain'],{'lead_id':1}))
        self.assertEqual(cursor.execute.call_count,1)

    def test_connect_test_mode_initializes_transport_schema_before_channel_lookup(self):
        conn=MagicMock();conn.__enter__.return_value=conn
        cursor=MagicMock();cursor.__enter__.return_value=cursor;conn.cursor.return_value=cursor
        cursor.fetchone.return_value=None
        with patch.object(test_mode,'_connect',return_value=conn), \
             patch.object(test_mode,'ensure_schema') as ensure_ai_schema, \
             patch('tenant_connect_transport._schema') as ensure_connect_schema, \
             patch('tenant_ai_agents_store._authorize',return_value=(TENANT,42)), \
             patch('tenant_ai_agents_store._audit'):
            result=test_mode.save_connect_test_mode({'tenant_id':TENANT,'telegram_id':42},
                {'provider':'connect','channel_id':'channel-1','conversation_id':'conversation-1',
                 'enabled':False,'emergency_stop':False})
        ensure_ai_schema.assert_called_once_with(conn)
        ensure_connect_schema.assert_called_once_with(conn)
        self.assertFalse(result['verified'])
        self.assertTrue(result['draft_only'])


if __name__=='__main__': unittest.main()
