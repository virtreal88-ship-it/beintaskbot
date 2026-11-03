"""Real signed SID + HTTP recovery against an isolated PostgreSQL fixture.

This resets the process-local schema-ready marker to model a cold process; it
is not a production or multi-process deployment test. The shared fixture's
explicit loopback/name guard does not prove disposability: the test environment
must independently provide a disposable PostgreSQL service.
"""
import asyncio
import importlib
import json
import logging
import os
import unittest
from unittest.mock import patch

fixture=importlib.import_module('test_customer_memory_postgres')


@unittest.skipUnless(fixture.DSN,'CUSTOMER_MEMORY_TEST_DSN is not set')
class PendingErasureOffHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.addClassCleanup(fixture.CustomerMemoryPostgresTests.tearDownClass.__func__,cls)
        fixture.CustomerMemoryPostgresTests.setUpClass.__func__(cls)

    link=fixture.CustomerMemoryPostgresTests.link
    fact=fixture.CustomerMemoryPostgresTests.fact

    def setUp(self):
        fixture.CustomerMemoryPostgresTests.setUp(self)

    def _snapshot(self):
        from tenant_platform import _connect
        rows={}
        predicates={
          'saas_customer_memory_erasure_requests':('tenant_id=%s AND contact_id=%s',(self.tenant,self.contact)),
          'saas_customer_memory_erased_fact_ids':('tenant_id=%s',(self.tenant,)),
          'saas_customer_memory_source_erasure_events':('tenant_id=%s',(self.tenant,)),
          'saas_customer_memory_claim_suppressions':('tenant_id=%s AND contact_id=%s',(self.tenant,self.contact)),
          'saas_customer_memory_group_fences':('tenant_id=%s AND contact_id=%s',(self.tenant,self.contact)),
          'saas_customer_memory_suppressions':('tenant_id=%s AND contact_id=%s',(self.tenant,self.contact)),
          'saas_customer_memory_facts':('tenant_id=%s AND contact_id=%s',(self.tenant,self.contact)),
          'saas_customer_memory_subject_revisions':('tenant_id=%s AND contact_id=%s',(self.tenant,self.contact)),
        }
        from psycopg import sql
        with _connect() as conn:
            with conn.cursor() as cur:
                for table,(where,args) in predicates.items():
                    # Static internal table/predicate allowlist; never client input.
                    cur.execute(sql.SQL('SELECT to_jsonb(x) AS row FROM {} x WHERE ').format(sql.Identifier(table))+sql.SQL(where),args)
                    rows[table]=sorted(json.dumps(r['row'],sort_keys=True,default=str) for r in cur.fetchall())
        return rows

    def test_cold_off_real_sid_http_recovery_keeps_existing_pending_and_fences(self):
        from aiohttp import web
        from aiohttp.test_utils import TestClient,TestServer
        import tenant_account_store as accounts
        import tenant_member_store as members
        import tenant_platform_schema as platform_schema
        import tenant_platform_schema_crm as crm_schema
        from tenant_browser_session import TenantBrowserSession,COOKIE_NAME
        from tenant_platform_core import TenantPlatformError
        from tenant_customer_memory_api import handler

        link=self.link();fact=self.fact(link,value='Synthetic pending fact')
        pending=self.memory.change_fact(self.profile,self.contact,fact['id'],1,'delete')
        self.assertEqual(pending['status'],'pending')
        before=self._snapshot()
        self.assertEqual(len(before['saas_customer_memory_erasure_requests']),1)
        self.assertEqual(len(before['saas_customer_memory_facts']),0)
        for table in ('saas_customer_memory_erased_fact_ids','saas_customer_memory_source_erasure_events',
                      'saas_customer_memory_claim_suppressions','saas_customer_memory_group_fences',
                      'saas_customer_memory_suppressions'):
            self.assertGreater(len(before[table]),0,table)

        browser=TenantBrowserSession(secret=lambda:'synthetic-only-cookie-signing-key',
          member=members.member,identity_allowed=lambda *_:False,membership_error=TenantPlatformError,
          create_session=accounts.create_session,session_active=accounts.session_is_active)
        owner_cookie=browser.issue(self.tenant,71)
        foreign_cookie=browser.issue(self.other,72)
        self.assertEqual(len(owner_cookie.rsplit('.',4)),5,'fixture must use a signed SID cookie')
        old_ready=platform_schema._schema_ready
        self.addCleanup(setattr,platform_schema,'_schema_ready',old_ready)
        origin='https://synthetic.invalid'
        route='/memory/'+self.contact
        query='?expected_tenant_id='+self.tenant+'&expected_user_id=71'

        async def run_http():
            app=web.Application()
            app.router.add_route('*','/memory/{contact_id}',handler(browser.profile,origin,logging.getLogger('synthetic-off-memory')))
            client=TestClient(TestServer(app));await client.start_server()
            try:
                owner={'Cookie':COOKIE_NAME+'='+owner_cookie}
                status=await client.get(route+query+'&erasure_request_id='+pending['request_id'],headers=owner)
                self.assertEqual(status.status,200);body=await status.json()
                self.assertEqual(body['data']['request_id'],pending['request_id']);self.assertEqual(body['data']['status'],'pending')
                self.assertEqual(status.headers.get('Cache-Control'),'no-store')
                listing=await client.get(route+query+'&erasure_status=1',headers=owner)
                self.assertEqual(listing.status,200);listing_body=await listing.json()
                self.assertIn(pending['request_id'],[entry['request_id'] for entry in listing_body['data']['requests']])
                again=await client.post(route+query,json={'action':'delete','fact_id':fact['id'],'expected_version':1},
                    headers={**owner,'Origin':origin})
                self.assertEqual(again.status,200);self.assertEqual((await again.json())['data'],pending)
                normal=await client.get(route+query,headers=owner)
                self.assertEqual(normal.status,404)
                foreign_query='?expected_tenant_id='+self.other+'&expected_user_id=72'
                foreign={'Cookie':COOKIE_NAME+'='+foreign_cookie}
                blocked=await client.get(route+foreign_query+'&erasure_request_id='+pending['request_id'],headers=foreign)
                self.assertEqual(blocked.status,404)
                blocked_write=await client.post(route+foreign_query,json={'action':'delete','fact_id':fact['id'],'expected_version':1},
                    headers={**foreign,'Origin':origin})
                self.assertIn(blocked_write.status,(403,404))
                owner_sid=owner_cookie.rsplit('.',4)[3]
                # Revoke only this synthetic SID. The account-wide helper also
                # audits every tenant for actor 71, which is shared among the
                # isolated fixture's unrelated test tenants.
                from tenant_platform import _connect
                with _connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute('UPDATE saas_tenant_browser_sessions SET revoked_at=now() WHERE id=%s::uuid AND tenant_id=%s::uuid',(owner_sid,self.tenant))
                        self.assertEqual(cur.rowcount,1)
                    conn.commit()
                after_revoke=await client.get(route+query+'&erasure_request_id='+pending['request_id'],headers=owner)
                self.assertEqual(after_revoke.status,403)
            finally:
                await client.close()

        # This is the actual SID checker and active-membership lookup. Only
        # migration is intercepted as a sentinel; no auth or DB access is mocked.
        real_active=accounts.session_is_active
        real_crm_migration=platform_schema.migrate_crm_schema
        with patch.dict(os.environ,{'CUSTOMER_MEMORY_ENABLED':'0'}), \
             patch.object(crm_schema,'migrate_customer_memory',side_effect=AssertionError('OFF memory migration executed')) as memory_migration, \
             patch.object(platform_schema,'migrate_crm_schema',wraps=real_crm_migration) as crm_migration, \
             patch.object(accounts,'session_is_active',wraps=real_active) as sid_checker:
            # The browser captures its callable when constructed; preserve the
            # real wrapped function while observing that real checks occurred.
            browser=TenantBrowserSession(secret=lambda:'synthetic-only-cookie-signing-key',
              member=members.member,identity_allowed=lambda *_:False,membership_error=TenantPlatformError,
              create_session=accounts.create_session,session_active=accounts.session_is_active)
            platform_schema._schema_ready=False
            asyncio.run(run_http())
            self.assertGreater(sid_checker.call_count,0,'the real SID database check must run')
            self.assertGreater(crm_migration.call_count,0,'must exercise cold schema initialization')
            memory_migration.assert_not_called()
            self.assertEqual(self._snapshot(),before,'retries or OFF migration changed erasure fences')
