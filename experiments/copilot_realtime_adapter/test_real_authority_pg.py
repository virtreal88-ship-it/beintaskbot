"""Synthetic direct-dialog authority checks on a disposable local PostgreSQL.

Set VOICE_AUTH_PG_URL to a disposable localhost DB named test_voice_auth_ plus
32 lowercase hex digits. Creates/drops one random schema only. No network/API
providers or real users; no schema DDL outside this test's random namespace.
"""
import json
import os
from pathlib import Path
import re
import sys
import unittest
import uuid
from urllib.parse import urlsplit
from unittest.mock import patch

URL=os.environ.get('VOICE_AUTH_PG_URL')
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'beintaskbot-main'))


@unittest.skipUnless(URL,'VOICE_AUTH_PG_URL is required for disposable local DB')
class RealVoiceAuthority(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from psycopg import connect
        from psycopg.conninfo import conninfo_to_dict
        from psycopg.rows import dict_row
        if any(name.startswith('PG') for name in os.environ):raise RuntimeError('unexpected_libpq_env')
        parsed=urlsplit(URL);args=conninfo_to_dict(URL)
        if (parsed.scheme not in ('postgres','postgresql') or parsed.query or parsed.fragment or
            parsed.hostname not in ('localhost','127.0.0.1','::1') or args.get('host') not in ('localhost','127.0.0.1','::1') or
            not re.fullmatch(r'test_voice_auth_[a-f0-9]{32}',parsed.path.removeprefix('/'))):
            raise RuntimeError('refusing_nonlocal_nonisolated_database')
        cls.connect_impl=staticmethod(connect);cls.dict_row=dict_row
        cls.address='::1' if parsed.hostname=='::1' else '127.0.0.1'
        cls.schema='voice_auth_'+uuid.uuid4().hex
        with cls.connect() as conn:
            conn.execute('CREATE SCHEMA '+cls.schema)
            conn.execute('SET search_path TO '+cls.schema)
            conn.execute('''CREATE TABLE saas_tenants(id uuid PRIMARY KEY,status text NOT NULL,modules jsonb,onboarding jsonb);
              CREATE TABLE saas_tenant_members(tenant_id uuid,telegram_id bigint,active boolean,role text,permissions jsonb);
              CREATE TABLE saas_tenant_pipelines(tenant_id uuid,pipeline_id bigint,name text,active boolean,owner_telegram_id bigint,settings jsonb);
              CREATE TABLE saas_tenant_pipeline_stages(tenant_id uuid,pipeline_id bigint,stage_id bigint,name text,sort_order int,stage_type text,settings jsonb);
              CREATE TABLE saas_tenant_workflow_policies(tenant_id uuid,policy_key text,value jsonb,updated_by bigint,updated_at timestamptz);
              CREATE TABLE saas_connect_conversations(tenant_id uuid,connect_channel_id uuid,conversation_id text,lead_id bigint);
              CREATE TABLE saas_connect_identity_conflicts(tenant_id uuid,connect_channel_id uuid,conversation_id text);
              CREATE TABLE saas_connect_direct_identities(tenant_id uuid,connect_channel_id uuid,identity_key text,canonical_conversation_id text);
              CREATE TABLE saas_connect_dialog_links(tenant_id uuid,channel_id uuid,source_id text,canonical_id text,active boolean);
              CREATE TABLE saas_crm_messages(tenant_id uuid,channel text,raw jsonb);
              CREATE TABLE saas_tenant_integrations(tenant_id uuid,provider text,metadata jsonb,updated_at timestamptz,secrets bytea);
              CREATE TABLE saas_ai_agent_settings(tenant_id uuid,emergency_stop boolean,company_instructions text);
              CREATE TABLE saas_ai_agents(tenant_id uuid,id uuid,version bigint,name text,model text,instructions text,language text,style text,archived_at timestamptz);
              CREATE TABLE saas_ai_conversation_state(tenant_id uuid,provider text,channel_id text,conversation_id text,agent_id uuid,revision bigint);''')
            conn.commit()

    @classmethod
    def connect(cls):
        return cls.connect_impl(URL,hostaddr=cls.address,row_factory=cls.dict_row)

    @classmethod
    def tearDownClass(cls):
        with cls.connect() as conn:
            conn.execute('DROP SCHEMA '+cls.schema+' CASCADE')
            conn.commit()

    def setUp(self):
        self.tenant=str(uuid.uuid4());self.other=str(uuid.uuid4());self.channel=str(uuid.uuid4());self.agent=str(uuid.uuid4())
        self.conversation='123456789@s.whatsapp.net'
        self.route={'provider':'connect','lead_id':41,'channel_id':self.channel,'conversation_id':self.conversation}
        self.conn=self.connect();self.conn.execute('SET search_path TO '+self.schema)
        self.conn.execute('INSERT INTO saas_tenants VALUES (%s,%s,%s::jsonb,%s::jsonb),(%s,%s,%s::jsonb,%s::jsonb)',
                          (self.tenant,'active',json.dumps({'deals':True}),'{}',self.other,'active','{}','{}'))
        self.conn.execute('''INSERT INTO saas_tenant_members VALUES (%s,7,true,'owner','[]'::jsonb),(%s,7,true,'owner','[]'::jsonb),
          (%s,8,true,'operator','["dialogs"]'::jsonb),(%s,9,true,'manager','["dialogs","deals"]'::jsonb)''',
                          (self.tenant,self.other,self.tenant,self.tenant))
        self.conn.execute('''INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value) VALUES(%s,'members',%s::jsonb)''',
                          (self.tenant,json.dumps({'9':{'ai_enabled':True}})))
        self.conn.execute('INSERT INTO saas_connect_conversations VALUES(%s,%s,%s,41)',(self.tenant,self.channel,self.conversation))
        self.conn.execute('INSERT INTO saas_tenant_integrations VALUES(%s,\'chat_ai\',%s::jsonb,now(),%s)',
                          (self.tenant,json.dumps({'enabled':True,'consent':True,'audio_enabled':True,'copilot_enabled':True}),b'synthetic'))
        self.conn.execute("INSERT INTO saas_ai_agent_settings VALUES(%s,false,'synthetic guidance')",(self.tenant,))
        self.conn.execute("INSERT INTO saas_ai_agents VALUES(%s,%s,1,'synthetic','valid-model','synthetic','auto','plain',NULL)",
                          (self.tenant,self.agent))
        self.conn.execute("INSERT INTO saas_ai_conversation_state VALUES(%s,'connect',%s,%s,%s,1)",
                          (self.tenant,self.channel,self.conversation,self.agent))
        self.conn.commit()

    def tearDown(self):
        self.conn.rollback()
        self.conn.execute('''TRUNCATE saas_tenants,saas_tenant_members,saas_tenant_workflow_policies,saas_connect_conversations,
          saas_tenant_integrations,saas_ai_agent_settings,saas_ai_agents,saas_ai_conversation_state,saas_connect_identity_conflicts''')
        self.conn.commit();self.conn.close()

    def admit(self,actor=7,tenant=None,route=None):
        from tenant_operator_voice_context import authorize
        with self.conn.cursor() as cur:
            return authorize(cur,{'tenant_id':tenant or self.tenant,'telegram_id':actor},route or self.route)

    def test_real_owner_and_manager_permissions_and_scope(self):
        owner=self.admit();manager=self.admit(actor=9)
        self.assertEqual((owner.tenant,owner.actor,owner.channel_id,owner.conversation_id,owner.lead_id),
                         (self.tenant,7,self.channel,self.conversation,41))
        self.assertEqual(manager.actor,9);self.assertEqual(owner.agent_binding,manager.agent_binding)

    def test_other_tenant_wrong_route_and_role_are_denied(self):
        from tenant_linear_policy import TenantLinearError
        for kwargs in [{'tenant':self.other},{'actor':8},{'actor':404},{'route':{**self.route,'lead_id':42}},
                       {'route':{**self.route,'channel_id':str(uuid.uuid4())}}]:
            with self.subTest(kwargs=kwargs),self.assertRaises(TenantLinearError):self.admit(**kwargs)

    def test_revocation_of_membership_role_consent_and_route(self):
        from tenant_linear_policy import TenantLinearError
        statements=[("UPDATE saas_tenant_members SET active=false WHERE tenant_id=%s AND telegram_id=7",(self.tenant,)),
                    ("UPDATE saas_tenants SET status='archived' WHERE id=%s",(self.tenant,)),
                    ("UPDATE saas_tenant_integrations SET metadata=metadata || '{\"consent\":false}'::jsonb WHERE tenant_id=%s",(self.tenant,)),
                    ("UPDATE saas_ai_agent_settings SET emergency_stop=true WHERE tenant_id=%s",(self.tenant,)),
                    ("DELETE FROM saas_ai_conversation_state WHERE tenant_id=%s",(self.tenant,)),
                    ("INSERT INTO saas_connect_identity_conflicts VALUES(%s,%s,%s)",(self.tenant,self.channel,self.conversation))]
        for sql,args in statements:
            with self.subTest(sql=sql):
                self.conn.rollback();self.conn.execute(sql,args)
                with self.assertRaises(TenantLinearError):self.admit()
        self.conn.rollback()

    def test_binding_changes_after_assignment_revision_or_agent_version(self):
        old=self.admit()
        self.conn.execute('UPDATE saas_ai_conversation_state SET revision=revision+1 WHERE tenant_id=%s',(self.tenant,))
        updated=self.admit();self.assertNotEqual(updated.agent_binding,old.agent_binding)
        self.conn.execute('UPDATE saas_ai_agents SET version=version+1 WHERE tenant_id=%s',(self.tenant,))
        self.assertNotEqual(self.admit().agent_binding,updated.agent_binding)

    def test_real_check_on_fresh_connection_fences_changed_config_and_revoked_member(self):
        from tenant_linear_policy import TenantLinearError
        import tenant_operator_voice_context as voice
        def scoped_connect():
            conn=self.connect();conn.execute('SET search_path TO '+self.schema);return conn
        profile={'tenant_id':self.tenant,'telegram_id':7}
        with patch.object(voice,'_connect',scoped_connect):
            old=voice.check(profile,self.route)
            self.conn.execute("UPDATE saas_tenant_integrations SET updated_at=updated_at+interval '1 second' WHERE tenant_id=%s",(self.tenant,))
            self.conn.commit()
            with self.assertRaises(TenantLinearError):voice.check(profile,self.route,expected=old)
            fresh=voice.check(profile,self.route)
            self.conn.execute('UPDATE saas_tenant_members SET active=false WHERE tenant_id=%s AND telegram_id=7',(self.tenant,))
            self.conn.commit()
            with self.assertRaises(TenantLinearError):voice.check(profile,self.route,expected=fresh)

    def test_real_disabled_company_options_and_ai_access_fail_closed(self):
        from tenant_linear_policy import TenantLinearError
        statements=[("UPDATE saas_tenant_integrations SET metadata=metadata || %s::jsonb WHERE tenant_id=%s",(json.dumps({key:False}),self.tenant))
                    for key in ('enabled','consent','audio_enabled','copilot_enabled')]
        statements.extend([
            ('UPDATE saas_tenant_integrations SET secrets=NULL WHERE tenant_id=%s',(self.tenant,)),
            ("UPDATE saas_ai_agents SET archived_at=now() WHERE tenant_id=%s",(self.tenant,)),
            ("UPDATE saas_ai_agents SET model='' WHERE tenant_id=%s",(self.tenant,)),
            ("UPDATE saas_tenant_workflow_policies SET value=%s::jsonb WHERE tenant_id=%s AND policy_key='members'",(json.dumps({'9':{'ai_enabled':False}}),self.tenant)),
        ])
        for index,(sql,args) in enumerate(statements):
            with self.subTest(sql=sql,args_index=index):
                self.conn.rollback();self.conn.execute(sql,args)
                with self.assertRaises(TenantLinearError):self.admit(actor=9 if index==len(statements)-1 else 7)
        self.conn.rollback()


if __name__=='__main__':unittest.main()
