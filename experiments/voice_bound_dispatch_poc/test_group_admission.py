"""Synthetic proof of current group admission; never opens voice or provider."""
import json
from unittest.mock import patch

import tenant_operator_voice_context as voice
import tenant_operator_voice_orchestration as orchestration
from tenant_linear_policy import TenantLinearError
from test_real_authority_pg import RealVoiceAuthority


class GroupAdmission(RealVoiceAuthority):
    def setUp(self):
        super().setUp()
        self.conversation='123456789-123456789@g.us'
        self.route['conversation_id']=self.conversation
        self.conn.execute('''
          ALTER TABLE saas_ai_agents ADD COLUMN role text DEFAULT 'primary', ADD COLUMN enabled boolean DEFAULT true,
            ADD COLUMN scopes jsonb DEFAULT '[]';
          CREATE TABLE saas_ai_group_coordination(tenant_id uuid,provider text,group_jid text,revision bigint,state text,assignments jsonb);
          CREATE TABLE saas_connect_channels(tenant_id uuid,connect_channel_id uuid,active boolean);
          CREATE FUNCTION ai_legacy_blocked(uuid,text,text,text) RETURNS boolean LANGUAGE sql AS 'SELECT false';
        ''')
        self.conn.execute('UPDATE saas_connect_conversations SET conversation_id=%s WHERE tenant_id=%s',(self.conversation,self.tenant))
        self.conn.execute('INSERT INTO saas_connect_channels VALUES(%s,%s,true)',(self.tenant,self.channel))
        self.conn.execute('UPDATE saas_ai_agents SET scopes=%s::jsonb WHERE tenant_id=%s AND id=%s',
            (json.dumps([{'provider':'connect','channel_id':self.channel,'allow_group_messages':True}]),self.tenant,self.agent))
        self.conn.execute('INSERT INTO saas_ai_group_coordination VALUES(%s,%s,%s,1,%s,%s::jsonb)',
            (self.tenant,'connect',self.conversation,'active',json.dumps([{'channel_id':self.channel,'agent_id':self.agent}])))
        self.conn.commit()

    def tearDown(self):
        self.conn.rollback()
        self.conn.execute('DROP TABLE IF EXISTS saas_ai_group_coordination,saas_connect_channels')
        self.conn.execute('DROP FUNCTION IF EXISTS ai_legacy_blocked(uuid,text,text,text)')
        self.conn.execute('ALTER TABLE saas_ai_agents DROP COLUMN role,DROP COLUMN enabled,DROP COLUMN scopes')
        self.conn.commit();super().tearDown()

    def scoped(self):
        conn=self.connect();conn.execute('SET search_path TO '+self.schema);return conn

    def test_actual_voice_check_accepts_assigned_group_while_context_remains_closed(self):
        profile={'tenant_id':self.tenant,'telegram_id':7}
        with patch.object(voice,'_connect',self.scoped):
            admitted=voice.check(profile,self.route)
            self.assertEqual(admitted.conversation_id,self.conversation)
            # This is authorization/preflight only, not a working voice call.
            with self.assertRaises(TenantLinearError):voice.context(admitted)
            with self.assertRaises(TenantLinearError):orchestration.require_history_cutover()
            with self.scoped() as conn:
                conn.execute('UPDATE saas_ai_group_coordination SET revision=2 WHERE tenant_id=%s',(self.tenant,))
            with self.assertRaises(TenantLinearError):voice.check(profile,self.route,expected=admitted)
