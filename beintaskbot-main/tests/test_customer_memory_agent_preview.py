"""Internal non-dispatchable projection; disposable local PostgreSQL only."""
import importlib
import unittest
import uuid

fixture=importlib.import_module('test_customer_memory_postgres')


@unittest.skipUnless(fixture.DSN,'CUSTOMER_MEMORY_TEST_DSN is not set')
class AgentPreviewPostgresTests(unittest.TestCase):
    setUpClass=classmethod(fixture.CustomerMemoryPostgresTests.setUpClass.__func__)
    tearDownClass=classmethod(fixture.CustomerMemoryPostgresTests.tearDownClass.__func__)
    link=fixture.CustomerMemoryPostgresTests.link
    fact=fixture.CustomerMemoryPostgresTests.fact

    def setUp(self):
        fixture.CustomerMemoryPostgresTests.setUp(self)
        from tenant_platform import _connect
        self.agent=str(uuid.uuid4())
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO saas_ai_agents(id,tenant_id,name,enabled,created_by) VALUES(%s,%s,'Synthetic',TRUE,71)",(self.agent,self.tenant))
                cur.execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,conversation_id,channel_id,agent_id) VALUES(%s,'connect',%s,%s,%s)",(self.tenant,self.jid,self.channel,self.agent))
            conn.commit()

    def preview(self,**kw):
        from tenant_customer_memory_agent_preview import preview
        args=dict(tenant_id=self.tenant,subject_id=self.contact,channel_id=self.channel,
                  conversation_id=self.jid,agent_id=self.agent,agent_version=1,assignment_revision=1)
        args.update(kw)
        return preview(self.profile,**args)

    def test_no_memory_and_only_confirmed_current_facts(self):
        from tenant_platform import _connect
        self.assertEqual(self.preview(),{'status':'no_memory','dispatchable':False})
        link=self.link()
        first=self.fact(link,value='Confirmed synthetic',reliability='confirmed')
        self.fact(link,value='Unconfirmed synthetic',reliability='needs_review',distinct_confirmation=True)
        outdated=self.fact(link,value='Old synthetic',reliability='outdated',distinct_confirmation=True)
        expiry=self.fact(link,value='Expired synthetic',reliability='confirmed',distinct_confirmation=True)
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE saas_customer_memory_facts SET valid_to=now()-interval '1 second' WHERE id=%s",(expiry['id'],))
            conn.commit()
        result=self.preview()
        self.assertEqual(result['status'],'preview');self.assertIs(result['dispatchable'],False)
        self.assertEqual(result['content_trust'],'untrusted_customer_data')
        self.assertEqual([f['value'] for f in result['facts']],['Confirmed synthetic'])
        self.assertTrue(result['incomplete'])
        self.assertNotIn('summary',result)

    def test_agent_binding_and_tenant_acl_fail_closed(self):
        from tenant_platform import _connect
        self.link()
        for kw in ({'agent_id':str(uuid.uuid4())},{'agent_version':2},{'assignment_revision':2},
                   {'tenant_id':self.other},{'subject_id':str(uuid.uuid4())},
                   {'conversation_id':'123456789@g.us'}):
            result=self.preview(**kw)
            self.assertIn(result['status'],('denied','ambiguous_identity'))
            self.assertNotIn('facts',result)
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('UPDATE saas_tenant_members SET active=FALSE WHERE tenant_id=%s AND telegram_id=71',(self.tenant,))
            conn.commit()
        self.assertEqual(self.preview(),{'status':'denied','dispatchable':False})

    def test_source_edit_and_revoked_link_hide_fact(self):
        from tenant_platform import _connect
        link=self.link();self.fact(link,value='Confirmed synthetic',reliability='confirmed')
        self.assertEqual(self.preview()['status'],'preview')
        with _connect() as conn:
            with conn.cursor() as cur:cur.execute("UPDATE saas_crm_messages SET body='Changed synthetic' WHERE id=%s",(self.message,))
            conn.commit()
        self.assertEqual(self.preview()['status'],'no_memory')
        self.memory.revoke_link(self.profile,self.contact,link,1)
        self.assertEqual(self.preview()['status'],'no_memory')

    def test_projection_is_bounded_and_never_contains_raw_message(self):
        import json
        link=self.link()
        for i in range(12):
            self.fact(link,value=('Synthetic%02d'%i)+('x'*140),reliability='confirmed',distinct_confirmation=True)
        result=self.preview()
        self.assertEqual(result['status'],'preview')
        self.assertLessEqual(len(json.dumps(result['facts'],ensure_ascii=False,default=str,separators=(',',':')).encode('utf-8')),1200)
        self.assertLess(len(result['facts']),12)
        self.assertNotIn('Synthetic customer statement',repr(result))

    def test_flag_disabled_and_service_principal_rejected(self):
        import os
        from tenant_customer_memory_agent_preview import preview
        args=dict(tenant_id=self.tenant,subject_id=self.contact,channel_id=self.channel,
                  conversation_id=self.jid,agent_id=self.agent,agent_version=1,assignment_revision=1)
        self.assertEqual(preview({'tenant_id':self.tenant,'service_principal':'synthetic'},**args),{'status':'denied','dispatchable':False})
        flag=os.environ.pop('CUSTOMER_MEMORY_ENABLED')
        try:self.assertEqual(self.preview(),{'status':'denied','dispatchable':False})
        finally:os.environ['CUSTOMER_MEMORY_ENABLED']=flag

    def test_concurrent_subject_revision_discards_projection(self):
        from unittest.mock import patch
        from tenant_platform import _connect
        import tenant_customer_memory_agent_preview as adapter
        link=self.link();self.fact(link,value='Confirmed synthetic',reliability='confirmed')
        verify=adapter._verify_subject_revision
        def changed(cur,tenant,subject,expected):
            with _connect() as conn:
                with conn.cursor() as other:
                    other.execute('UPDATE saas_customer_memory_subject_revisions SET revision=revision+1 WHERE tenant_id=%s AND contact_id=%s',(tenant,subject))
                conn.commit()
            return verify(cur,tenant,subject,expected)
        with patch.object(adapter,'_verify_subject_revision',changed):
            self.assertEqual(self.preview(),{'status':'temporary_error','dispatchable':False})

    def test_legacy_wrong_source_attribution_is_not_projected(self):
        from tenant_platform import _connect
        link=self.link();fact=self.fact(link,value='Synthetic legacy statement',reliability='confirmed')
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE saas_customer_memory_facts SET source_kind='employee_observation' WHERE id=%s",(fact['id'],))
            conn.commit()
        self.assertEqual(self.preview(),{'status':'no_memory','dispatchable':False})

    def test_legacy_conflicted_source_is_not_projected_even_with_matching_fingerprint(self):
        from unittest.mock import patch
        from tenant_platform import _connect
        from tenant_customer_memory import _source,_source_kind_supported
        import tenant_customer_memory_agent_preview as adapter
        link=self.link();fact=self.fact(link,value='Synthetic legacy statement',reliability='confirmed')
        self.assertEqual(self.preview()['status'],'preview')
        # Simulate a fact saved by an old implementation after ingesting the
        # conflict marker. Update its fingerprint to the new source version so
        # fingerprint invalidation alone cannot make the test pass.
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE saas_crm_messages SET raw=jsonb_set(COALESCE(raw,'{}'::jsonb),'{conversationIdentityConflict}','true'::jsonb) WHERE id=%s",(self.message,))
                cur.execute('SELECT lead_id,channel_id FROM saas_customer_memory_links WHERE id=%s',(link,))
                route=cur.fetchone()
                source,version=_source(cur,self.tenant,route,self.message)
                self.assertIs(source['raw']['conversationIdentityConflict'],True)
                self.assertFalse(_source_kind_supported(source,'customer_statement'))
                cur.execute('UPDATE saas_customer_memory_facts SET source_version=%s WHERE id=%s',(version,fact['id']))
            conn.commit()
        self.assertEqual(self.preview(),{'status':'no_memory','dispatchable':False})
        # Positive control: bypass only the source-kind check, preserving all
        # source/ACL/fingerprint checks, and the same fact becomes visible.
        with patch.object(adapter,'_source_kind_supported',return_value=True):
            self.assertEqual([item['value'] for item in self.preview()['facts']],['Synthetic legacy statement'])

    def test_slow_schema_and_connect_do_not_reset_deadline(self):
        import time
        from unittest.mock import patch
        import tenant_customer_memory_agent_preview as adapter
        original_schema=adapter._ensure_schema
        def slow_schema(conn):
            time.sleep(.55)
            return original_schema(conn)
        with patch.object(adapter,'_ensure_schema',slow_schema):
            self.assertEqual(self.preview(),{'status':'temporary_error','dispatchable':False})
        original_connect=adapter._connect
        def slow_connect():
            time.sleep(.55)
            return original_connect()
        with patch.object(adapter,'_connect',slow_connect):
            self.assertEqual(self.preview(),{'status':'temporary_error','dispatchable':False})
