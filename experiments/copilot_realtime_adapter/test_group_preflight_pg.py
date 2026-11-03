"""Direct/group regression using disposable PostgreSQL; no provider or voice."""
from unittest.mock import patch
import tenant_operator_voice_context as voice
import tenant_operator_voice_orchestration as orchestration
from tenant_linear_policy import TenantLinearError
from test_real_authority_pg import RealVoiceAuthority


class GroupPreflight(RealVoiceAuthority):
    def scoped(self):
        conn=self.connect();conn.execute('SET search_path TO '+self.schema);return conn

    def test_direct_still_authorized_and_group_identity_and_metadata_denied(self):
        profile={'tenant_id':self.tenant,'telegram_id':7}
        group='123456789-123456789@g.us'
        with patch.object(voice,'_connect',self.scoped):
            original=voice.check(profile,self.route)
            self.assertEqual(original.conversation_id,self.conversation)
            with self.scoped() as conn:
                conn.execute('INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,conversation_kind) VALUES(%s,%s,%s,41,%s)',
                             (self.tenant,self.channel,group,'group'))
            with self.assertRaises(TenantLinearError):voice.check(profile,{**self.route,'conversation_id':group})
            with self.scoped() as conn:
                conn.execute("UPDATE saas_connect_conversations SET conversation_kind='group' WHERE tenant_id=%s AND conversation_id=%s",(self.tenant,self.conversation))
            with self.assertRaises(TenantLinearError):voice.check(profile,self.route)
            with self.scoped() as conn:
                conn.execute("UPDATE saas_connect_conversations SET conversation_kind='direct' WHERE tenant_id=%s AND conversation_id=%s",(self.tenant,self.conversation))
            self.assertEqual(voice.check(profile,self.route),original)
            for unknown in ('','unknown','group'):
                with self.scoped() as conn:
                    conn.execute('UPDATE saas_connect_conversations SET conversation_kind=%s WHERE tenant_id=%s AND conversation_id=%s',(unknown,self.tenant,self.conversation))
                with self.assertRaises(TenantLinearError):voice.check(profile,self.route)
            for kind in ('contact','direct','personal'):
                with self.scoped() as conn:
                    conn.execute('UPDATE saas_connect_conversations SET conversation_kind=%s WHERE tenant_id=%s AND conversation_id=%s',(kind,self.tenant,self.conversation))
                self.assertEqual(voice.check(profile,self.route),original)
            for unknown in ('opaque','123456789-123456789@G.US',''):
                with self.assertRaises(TenantLinearError):voice.check(profile,{**self.route,'conversation_id':unknown})
            with self.assertRaises(TenantLinearError):voice.context(original)
            with self.assertRaises(TenantLinearError):orchestration.require_history_cutover()
