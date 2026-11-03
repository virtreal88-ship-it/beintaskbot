"""Observed source change detection on a disposable local PostgreSQL only."""
import asyncio
import json
import uuid
from unittest.mock import patch
import test_integrated_synthetic_pg as fixture
import tenant_operator_voice_context as voice
import tenant_operator_voice_orchestration as service
import tenant_ai_agent_run_store as knowledge_store
from tenant_linear_policy import TenantLinearError


class SourceSnapshot(fixture.IntegratedSynthetic):
    def tearDown(self):
        self.conn.rollback();self.conn.execute('DROP TABLE IF EXISTS saas_instruction_lessons')
        self.conn.execute('ALTER TABLE saas_crm_messages DROP COLUMN IF EXISTS synthetic_blob');self.conn.commit()
        super().tearDown()

    def run_async(self,fn):
        with patch.object(voice,'_connect',self.scoped),patch.object(service,'_connect',self.scoped),patch.object(knowledge_store,'_connect',self.scoped):
            return asyncio.run(fn())

    async def context(self):
        authority=await service.ScopedAuthority.create({'tenant_id':self.tenant,'telegram_id':7},self.route)
        ctx=service.CopilotContext(authority)
        projection,sealed=await asyncio.to_thread(ctx._seal)
        self.assertTrue(await ctx.revalidate(authority.session_scope))
        return authority,ctx,projection,sealed

    def test_edit_delete_new_message_and_revoked_access_invalidate(self):
        async def exercise():
            for action in ('edit','delete','new','revoke'):
                with self.scoped() as conn:
                    conn.execute("DELETE FROM saas_crm_messages WHERE tenant_id=%s AND external_id='new-old'",(self.tenant,))
                    conn.execute("DELETE FROM saas_crm_messages WHERE tenant_id=%s AND external_id='customer-1'",(self.tenant,))
                    conn.execute("INSERT INTO saas_crm_messages(tenant_id,channel,raw,kommo_lead_id,external_id,direction,body,message_type) VALUES(%s,%s,'{}',41,'customer-1','incoming','synthetic customer','text')",(self.tenant,'connect:'+self.channel))
                    conn.execute('UPDATE saas_tenant_members SET active=true WHERE tenant_id=%s AND telegram_id=7',(self.tenant,))
                authority,ctx,_,sealed=await self.context()
                self.assertNotIn('synthetic customer',repr(sealed))
                with self.scoped() as conn:
                    if action=='edit':conn.execute("UPDATE saas_crm_messages SET body='edited' WHERE tenant_id=%s AND external_id='customer-1'",(self.tenant,))
                    elif action=='delete':conn.execute("DELETE FROM saas_crm_messages WHERE tenant_id=%s AND external_id='customer-1'",(self.tenant,))
                    elif action=='new':conn.execute("INSERT INTO saas_crm_messages(tenant_id,channel,raw,kommo_lead_id,external_id,direction,body,message_type,happened_at) VALUES(%s,%s,'{}',41,'new-old','incoming','backfill','text','2000-01-01')",(self.tenant,'connect:'+self.channel))
                    else:conn.execute('UPDATE saas_tenant_members SET active=false WHERE tenant_id=%s AND telegram_id=7',(self.tenant,))
                with self.assertRaises(TenantLinearError):await ctx.revalidate(authority.session_scope)
        self.run_async(exercise)

    def test_selected_knowledge_active_and_full_chunk_changes_invalidate(self):
        source=str(uuid.uuid4());text='synthetic customer '+'A'*1400
        with self.scoped() as conn:
            conn.execute('ALTER TABLE saas_ai_knowledge_sources ADD COLUMN content_hash text')
            conn.execute("INSERT INTO saas_ai_knowledge_sources(tenant_id,id,title,active,agent_id,provenance,updated_at,content_hash) VALUES(%s,%s,'Synthetic knowledge',true,NULL,'{}',now(),'hash-1')",(self.tenant,source))
            conn.execute("INSERT INTO saas_ai_knowledge_chunks VALUES(%s,%s,%s,to_tsvector('simple',%s),0)",(self.tenant,source,text,text))
        async def exercise():
            authority,ctx,projection,_=await self.context()
            self.assertEqual(len(projection['extra_context']['verified_knowledge']),1)
            with self.scoped() as conn:conn.execute('UPDATE saas_ai_knowledge_sources SET active=false WHERE tenant_id=%s AND id=%s',(self.tenant,source))
            with self.assertRaises(TenantLinearError):await ctx.revalidate(authority.session_scope)
            with self.scoped() as conn:conn.execute('UPDATE saas_ai_knowledge_sources SET active=true WHERE tenant_id=%s AND id=%s',(self.tenant,source))
            _,ctx,projection,_=await self.context()
            with self.scoped() as conn:conn.execute("UPDATE saas_ai_knowledge_chunks SET content=content || 'changed past the shown 1200 characters' WHERE tenant_id=%s AND source_id=%s",(self.tenant,source))
            changed,_=await asyncio.to_thread(ctx._capture_once)
            self.assertEqual(projection,changed)  # same visible excerpt, changed underlying version
            with self.assertRaises(TenantLinearError):await ctx.revalidate(authority.session_scope)
        self.run_async(exercise)

    def test_recreated_message_with_same_external_id_and_content_is_detected(self):
        async def exercise():
            authority,ctx,projection,_=await self.context()
            with self.scoped() as conn:
                old=conn.execute("SELECT happened_at,created_at FROM saas_crm_messages WHERE tenant_id=%s AND external_id='customer-1'",(self.tenant,)).fetchone()
                conn.execute("DELETE FROM saas_crm_messages WHERE tenant_id=%s AND external_id='customer-1'",(self.tenant,))
                conn.execute("INSERT INTO saas_crm_messages(tenant_id,channel,raw,kommo_lead_id,external_id,direction,body,message_type,happened_at,created_at) VALUES(%s,%s,'{}',41,'customer-1','incoming','synthetic customer','text',%s,%s)",
                             (self.tenant,'connect:'+self.channel,old['happened_at'],old['created_at']))
            changed,_=await asyncio.to_thread(ctx._capture_once)
            self.assertEqual(projection,changed)
            with self.assertRaises(TenantLinearError):await ctx.revalidate(authority.session_scope)
        self.run_async(exercise)

    def test_raw_json_and_synthetic_bytea_mutations_are_detected(self):
        with self.scoped() as conn:
            conn.execute('ALTER TABLE saas_crm_messages ADD COLUMN synthetic_blob bytea')
            conn.execute("UPDATE saas_crm_messages SET synthetic_blob=%s WHERE tenant_id=%s AND external_id='customer-1'",(b'\x00\x01',self.tenant))
        async def exercise():
            authority,ctx,_,_=await self.context()
            with self.scoped() as conn:conn.execute("UPDATE saas_crm_messages SET raw=raw || '{\"synthetic_revision\":1}'::jsonb WHERE tenant_id=%s AND external_id='customer-1'",(self.tenant,))
            with self.assertRaises(TenantLinearError):await ctx.revalidate(authority.session_scope)
            _,ctx,projection,_=await self.context()
            with self.scoped() as conn:conn.execute("UPDATE saas_crm_messages SET synthetic_blob=%s WHERE tenant_id=%s AND external_id='customer-1'",(b'\x00\x02',self.tenant))
            changed,_=await asyncio.to_thread(ctx._capture_once);self.assertEqual(projection,changed)
            with self.assertRaises(TenantLinearError):await ctx.revalidate(authority.session_scope)
        self.run_async(exercise)

    def test_approved_provenance_and_published_instruction_changes_invalidate(self):
        source=str(uuid.uuid4());candidate=str(uuid.uuid4());lesson=str(uuid.uuid4());content='synthetic customer approved'
        provenance={'candidate_id':candidate,'approved_by':'7','instruction_id':lesson,'review_status':'approved'}
        with self.scoped() as conn:
            conn.execute('ALTER TABLE saas_ai_knowledge_sources ADD COLUMN content_hash text')
            conn.execute('CREATE TABLE saas_instruction_lessons(tenant_id uuid,id uuid,state text,published_doc jsonb)')
            conn.execute("INSERT INTO saas_instruction_lessons VALUES(%s,%s,'published',%s::jsonb)",(self.tenant,lesson,json.dumps({'review_status':'approved','text':'original'})))
            conn.execute("INSERT INTO saas_ai_knowledge_sources(tenant_id,id,title,active,agent_id,provenance,updated_at,content_hash) VALUES(%s,%s,'Approved',true,NULL,%s::jsonb,now(),'hash-2')",(self.tenant,source,json.dumps(provenance)))
            conn.execute("INSERT INTO saas_ai_knowledge_chunks VALUES(%s,%s,%s,to_tsvector('simple',%s),0)",(self.tenant,source,content,content))
            conn.execute("INSERT INTO saas_knowledge_candidates VALUES(%s,%s,7,'approved')",(self.tenant,candidate))
            conn.execute('INSERT INTO saas_knowledge_candidate_sources VALUES(%s,%s,%s,%s)',(self.tenant,candidate,source,self.agent))
        async def exercise():
            authority,ctx,projection,_=await self.context()
            self.assertEqual(len(projection['extra_context']['verified_knowledge']),1)
            with self.scoped() as conn:conn.execute("UPDATE saas_knowledge_candidates SET status='rejected' WHERE tenant_id=%s AND id=%s",(self.tenant,candidate))
            with self.assertRaises(TenantLinearError):await ctx.revalidate(authority.session_scope)
            with self.scoped() as conn:conn.execute("UPDATE saas_knowledge_candidates SET status='approved' WHERE tenant_id=%s AND id=%s",(self.tenant,candidate))
            _,ctx,projection,_=await self.context()
            with self.scoped() as conn:conn.execute("UPDATE saas_ai_knowledge_sources SET provenance=provenance || '{\"audit_revision\":2}'::jsonb WHERE tenant_id=%s AND id=%s",(self.tenant,source))
            changed,_=await asyncio.to_thread(ctx._capture_once);self.assertEqual(projection,changed)
            with self.assertRaises(TenantLinearError):await ctx.revalidate(authority.session_scope)
            _,ctx,projection,_=await self.context()
            with self.scoped() as conn:conn.execute("UPDATE saas_instruction_lessons SET published_doc=published_doc || '{\"text\":\"changed\"}'::jsonb WHERE tenant_id=%s AND id=%s",(self.tenant,lesson))
            changed,_=await asyncio.to_thread(ctx._capture_once);self.assertEqual(projection,changed)
            with self.assertRaises(TenantLinearError):await ctx.revalidate(authority.session_scope)
        self.run_async(exercise)

    def test_after_successful_check_later_mutation_is_still_blocked_from_provider(self):
        async def exercise():
            authority,ctx,_,_=await self.context()
            self.assertTrue(await ctx.revalidate(authority.session_scope))
            with self.scoped() as conn:conn.execute("UPDATE saas_crm_messages SET body='changed after check' WHERE tenant_id=%s AND external_id='customer-1'",(self.tenant,))
            # A database read cannot lock an external provider send. The hard
            # gate remains necessary even after a successful prior check.
            with self.assertRaises(TenantLinearError):await ctx.read_authorized(authority.session_scope)
            with self.assertRaises(TenantLinearError):await service.open_server_session(None,None)
        self.run_async(exercise)

    def test_aba_is_not_detected_and_hard_gate_remains_closed(self):
        async def exercise():
            authority,ctx,_,_=await self.context()
            with self.scoped() as conn:
                conn.execute("UPDATE saas_crm_messages SET body='transient' WHERE tenant_id=%s AND external_id='customer-1'",(self.tenant,))
            with self.scoped() as conn:
                conn.execute("UPDATE saas_crm_messages SET body='synthetic customer' WHERE tenant_id=%s AND external_id='customer-1'",(self.tenant,))
            # This schema has no monotonically increasing revision: a revert
            # between checks is undetectable. It must not unlock live use.
            self.assertTrue(await ctx.revalidate(authority.session_scope))
            with self.assertRaises(TenantLinearError):await ctx.read_authorized(authority.session_scope)
            with self.assertRaises(TenantLinearError):await service.open_server_session(None,None)
        self.run_async(exercise)
