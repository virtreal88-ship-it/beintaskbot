import asyncio
from io import BytesIO
import sys
import types
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

if 'psycopg' not in sys.modules:
    psycopg=types.ModuleType('psycopg');psycopg.connect=Mock()
    rows=types.ModuleType('psycopg.rows');rows.dict_row=object()
    sys.modules['psycopg']=psycopg;sys.modules['psycopg.rows']=rows

from tenant_ai_agents_policy import agent, trigger, company_settings
from tenant_ai_agent_queue import _account_domain, _scope_matches
from tenant_ai_agent_actions import should_handoff, matching_triggers
from tenant_ai_agent_crm_actions import phone_candidate
import tenant_ai_agent_crm_actions as crm_actions
from tenant_chat_ai_policy import history as chat_history
from tenant_ai_agents_policy import chunk_text
from tenant_ai_knowledge_files import extract
from tenant_linear_policy import TenantLinearError

if hasattr(asyncio,'WindowsSelectorEventLoopPolicy'):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
import tenant_ai_agent_queue as queue
import tenant_ai_agent_worker as worker
import tenant_chat_send_service as send_service
import tenant_ai_agents_store as agent_store
import tenant_ai_agents_api as agents_api

TENANT='a9c11a16-01d5-469a-a6c1-9a726975ae42'
AGENT='417f1a16-01d5-469a-a6c1-9a726975ae42'
FINGERPRINT='a'*64


class PolicyTests(unittest.TestCase):
    def test_new_agent_defaults_to_manual_and_autosend_is_opt_in(self):
        clean=agent({'name':'Support'},creating=True)
        self.assertFalse(clean['enabled'])
        self.assertEqual(clean['mode'],'manual')
        self.assertEqual(clean['delay_seconds'],40)
        self.assertEqual(agent({'name':'Support','mode':'automatic'},creating=True)['mode'],'automatic')
        self.assertEqual(company_settings({'auto_send_enabled':True})['auto_send_enabled'],True)

    def test_trigger_schema_is_allowlisted_and_requires_explicit_destinations(self):
        with self.assertRaises(TenantLinearError):
            trigger({'name':'change stage','trigger_type':'phone_received','actions':['move_stage']},creating=True)
        clean=trigger({'name':'Phone','trigger_type':'phone_received','actions':['update_phone'],'enabled':True},creating=True)
        self.assertEqual(clean['actions'],['update_phone'])
        with self.assertRaises(TenantLinearError):
            trigger({'name':'Bad','trigger_type':'phrase','condition':{'phrase':'x'},'actions':['http']},creating=True)

    def test_admin_ai_agents_permission_is_checked_server_side(self):
        cur=MagicMock()
        cur.fetchone.return_value={'role':'owner','active':True,'status':'active'}
        self.assertEqual(agent_store._authorize(cur,{'tenant_id':TENANT,'telegram_id':20}),(TENANT,20))
        cur=MagicMock()
        cur.fetchone.side_effect=[{'role':'admin','active':True,'status':'active'}, {'value':{'20':{'ai_agents_manage':True}}}]
        self.assertEqual(agent_store._authorize(cur,{'tenant_id':TENANT,'telegram_id':20}),(TENANT,20))
        cur=MagicMock()
        cur.fetchone.side_effect=[{'role':'admin','active':True,'status':'active'}, {'value':{}}]
        with self.assertRaises(TenantLinearError):agent_store._authorize(cur,{'tenant_id':TENANT,'telegram_id':20})


class QueueTests(unittest.TestCase):
    def test_domain_and_scope_are_bound(self):
        self.assertEqual(_account_domain({'account':{'subdomain':'test-account'}}),'test-account.kommo.com')
        self.assertEqual(_account_domain({'account':{'domain':'https://evil.example'}}),'')
        self.assertTrue(_scope_matches([{'pipeline_id':'1','stage_id':'2','channel_id':'wa'}],'1','2','WA'))
        self.assertFalse(_scope_matches([{'pipeline_id':'1','stage_id':'2','channel_id':'wa'}],'1','2','ig'))

    def test_incoming_commits_before_ack_and_debounces_at_least_40_seconds(self):
        conn=MagicMock();conn.__enter__.return_value=conn;cur=MagicMock();cur.__enter__.return_value=cur;conn.cursor.return_value=cur
        cur.fetchall.side_effect=[ [{'tenant_id':TENANT,'tenant_status':'active'}], [{'id':AGENT,'scopes':[{'pipeline_id':'1','stage_id':'2','channel_id':'wa'}],'delay_seconds':0,'mode':'automatic'}], [] ]
        cur.fetchone.return_value={'fingerprint':FINGERPRINT}
        with patch.object(queue,'_connect',return_value=conn),patch.object(queue,'ensure_schema'),patch.object(queue,'allowed_event',return_value=True):
            count=queue.ingest_kommo_messages({'account':{'subdomain':'tenant'}},[{'type':'incoming','entity_type':'lead','entity_id':'7','contact_id':'77','talk_id':'9','chat_id':'chat','origin':'wa','id':'message-1'}])
        self.assertEqual(count,1)
        inserts=[call for call in cur.execute.call_args_list if 'INSERT INTO saas_ai_agent_jobs' in call.args[0]]
        self.assertEqual(len(inserts),1)
        self.assertEqual(inserts[0].args[1][-1],40)
        conn.commit.assert_called_once()

    def test_duplicate_webhook_does_not_enqueue_again(self):
        conn=MagicMock();conn.__enter__.return_value=conn;cur=MagicMock();cur.__enter__.return_value=cur;conn.cursor.return_value=cur
        cur.fetchall.side_effect=[ [{'tenant_id':TENANT,'tenant_status':'active'}], [{'id':AGENT,'scopes':[{}],'delay_seconds':40,'mode':'manual'}] ]
        cur.fetchone.return_value=None
        with patch.object(queue,'_connect',return_value=conn),patch.object(queue,'ensure_schema'),patch.object(queue,'allowed_event',return_value=True):
            count=queue.ingest_kommo_messages({'account':{'subdomain':'tenant'}},[{'entity_id':7,'contact_id':77,'talk_id':9,'chat_id':'chat','origin':'wa','id':'duplicate'}])
        self.assertEqual(count,0)
        self.assertFalse(any('INSERT INTO saas_ai_agent_jobs' in call.args[0] for call in cur.execute.call_args_list))
        conn.commit.assert_called_once()

    def test_worker_lease_uses_skip_locked_and_durable_claim(self):
        import tenant_ai_agent_run_store as run_store
        queued={'tenant_id':TENANT,'agent_id':AGENT,'provider':'kommo','conversation_id':'chat','revision':1,
          'event_fingerprint':FINGERPRINT,'state':'processing','lease_token':'lease'}
        conn=MagicMock();conn.cursor.return_value.__enter__.return_value=MagicMock()
        cur=conn.cursor.return_value.__enter__.return_value
        claimed_row={**queued,'lease_token':'lease'}
        cur.fetchall.return_value=[];cur.fetchone.side_effect=[queued,claimed_row,{'payload':{'message_id':'msg'}}]
        with patch.object(run_store,'_connect',return_value=conn),patch.object(run_store,'ensure_schema'):
            claimed=run_store.claim_next()
        self.assertEqual(claimed['payload']['message_id'],'msg')
        sql='\n'.join(call.args[0] for call in cur.execute.call_args_list)
        self.assertIn('FOR UPDATE OF j SKIP LOCKED',sql)
        self.assertIn('ORDER BY j.due_at,j.updated_at LIMIT 1 FOR UPDATE OF j SKIP LOCKED',sql)
        self.assertIn("lease_until=now()+interval '3 minutes'",sql)
        conn.commit.assert_called_once();conn.close.assert_called_once()


class TriggerTests(unittest.TestCase):
    def test_phone_requires_unambiguous_international_value(self):
        self.assertEqual(phone_candidate('Nömrəm +15555550103'),'+15555550103')
        self.assertEqual(phone_candidate('call 0501234567'),'')
        self.assertEqual(phone_candidate('+15555550103 / +15555550104'),'')

    def test_handoff_matches_explicit_customer_request_and_phrase_trigger(self):
        self.assertTrue(should_handoff('Please connect me to a human manager'))
        self.assertTrue(should_handoff('Zəhmət olmasa menecerlə danışmaq istəyirəm'))
        self.assertFalse(should_handoff('What are your working hours?'))
        rows=[{'trigger_type':'phrase','condition':{'phrase':'price list'},'actions':['handoff']}]
        self.assertEqual(matching_triggers(rows,'Please send price list'),rows)

    def test_knowledge_and_media_handling_are_bounded_and_explicit(self):
        text='A'*1500+' '+'B'*1500
        chunks=chunk_text(text)
        self.assertTrue(all(len(part)<=1200 for part in chunks))
        self.assertTrue(all(chunks[i][-100:]==chunks[i+1][:100] for i in range(len(chunks)-1)))
        filename,kind,content=extract('../company.txt',b'\xef\xbb\xbfTrusted business facts')
        self.assertEqual((filename,kind,content),('company.txt','txt','Trusted business facts'))
        document=BytesIO()
        with zipfile.ZipFile(document,'w') as archive:
            archive.writestr('word/document.xml','''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Stage guidance</w:t></w:r></w:p></w:body></w:document>''')
        self.assertEqual(extract('faq.docx',document.getvalue()),('faq.docx','docx','Stage guidance'))
        audio,missing=chat_history([{'external_id':'voice-1','direction':'incoming','body':'','message_type':'audio'}],{})
        image,_=chat_history([{'external_id':'image-1','direction':'incoming','body':'','message_type':'image','media_url':'https://media.example/image.jpg'}],{})
        self.assertEqual(missing,1);self.assertIn('Audio unavailable',audio[0]['text'])
        self.assertIn('Attachment not analyzed',image[0]['text'])


class CrmActionSafetyTests(unittest.TestCase):
    def test_interrupted_reserved_action_becomes_unknown_and_is_not_replayed(self):
        current={'state':'processing','revision':1,'lease_token':'lease','enabled':True,'mode':'automatic',
          'agents_enabled':True,'crm_actions_enabled':True,'conversation_state':'active','tenant_status':'active',
          'test_mode_allowed':True}
        conn=MagicMock();conn.__enter__.return_value=conn;cur=MagicMock();cur.__enter__.return_value=cur;conn.cursor.return_value=cur
        cur.fetchone.side_effect=[current,None,{'state':'running'}]
        job={'tenant_id':TENANT,'agent_id':AGENT,'provider':'kommo','conversation_id':'chat','revision':1,
             'lease_token':'lease','event_fingerprint':FINGERPRINT}
        with patch.dict('os.environ',{'STAGING_MODE':'false','AI_AGENTS_CRM_ENABLED':'true'}),patch.object(crm_actions,'_connect',return_value=conn),patch.object(crm_actions,'ensure_schema'):
            reservation=crm_actions._reserve_action(job,'trigger:0','move_stage',{'target_stage_id':'2'})
        self.assertEqual(reservation,(None,'unknown'))
        updates=[call.args[0] for call in cur.execute.call_args_list if 'worker_interrupted_after_reservation' in str(call.args)]
        self.assertTrue(updates)
        conn.commit.assert_called_once()


class ApiSafetyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.profile={'tenant_id':TENANT,'telegram_id':20,'role':'owner'}
        self.logger=Mock()
        self.origin='https://crm.pro.az'

    def request(self,method='POST',payload=None,origin=None,resource_id=''):
        return SimpleNamespace(method=method,headers={'Origin':origin or self.origin},content_length=100,
            content_type='application/json',json=AsyncMock(return_value=payload or {}),
            match_info={'draft_id':resource_id},query={})

    async def test_mutations_require_same_origin_and_session_identity(self):
        handle=agents_api.handler(lambda _request:self.profile,self.origin,self.logger,'settings')
        body={'expected_tenant_id':TENANT,'expected_user_id':20,'enabled':True}
        inline=AsyncMock(side_effect=lambda fn,*args,**kwargs:fn(*args,**kwargs))
        with patch.object(agents_api,'save_settings',return_value={'enabled':True}) as save,patch.object(agents_api.asyncio,'to_thread',inline):
            wrong_origin=await handle(self.request(payload=body,origin='https://attacker.example'))
            self.assertEqual(wrong_origin.status,403);save.assert_not_called()
            wrong_tenant=await handle(self.request(payload={**body,'expected_tenant_id':'b9c11a16-01d5-469a-a6c1-9a726975ae42'}))
            self.assertEqual(wrong_tenant.status,409);save.assert_not_called()
            accepted=await handle(self.request(payload=body))
            self.assertEqual(accepted.status,200);save.assert_called_once_with(self.profile,{'enabled':True})

    async def test_draft_send_keeps_exact_chat_source_and_explicit_send_service(self):
        provider=AsyncMock();wait_slot=Mock()
        handle=agents_api.handler(lambda _request:self.profile,self.origin,self.logger,'draft_send',provider,wait_slot)
        reserved={'tenant_id':TENANT,'id':'draft-id','lead_id':7,'draft':'test reply',
          'expected_route':{'talk_id':'9','chat_id':'chat','message_id':'source'}}
        inline=AsyncMock(side_effect=lambda fn,*args,**kwargs:fn(*args,**kwargs))
        with patch.object(agents_api,'reserve_draft_send',return_value=reserved),patch.object(agents_api,'finish_draft_send') as finish,\
                patch.object(agents_api,'send_chat_message',AsyncMock(return_value={'state':'accepted'})) as send,\
                patch.object(agents_api.asyncio,'to_thread',inline):
            response=await handle(self.request(payload={'expected_tenant_id':TENANT,'expected_user_id':20},resource_id='draft-id'))
        self.assertEqual(response.status,200)
        send.assert_awaited_once_with(self.profile,{'request_id':'draft-id','lead_id':7,'text':'test reply'},
          provider,wait_slot,self.logger,expected_route=reserved['expected_route'])
        finish.assert_called_once_with(TENANT,'draft-id','accepted')


class SendTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.job={'tenant_id':TENANT,'agent_id':AGENT,'provider':'kommo','conversation_id':'chat','chat_id':'chat','talk_id':'9','lead_id':7,'channel_id':'wa','event_fingerprint':FINGERPRINT,'revision':1,'lease_token':'lease','payload':{'contact_id':77,'channel':'wa'}}
        self.reservation={'run_id':'run','route':{'talk_id':'9','chat_id':'chat','origin':'wa','conversation_revision':3},'source_message_id':'source','pipeline_id':'1','stage_id':'2'}
        self.item={'key':(TENANT,0,'send-id'),'job':self.job,'credentials':{'account_domain':'tenant.kommo.com','access_token':'token'},'route':self.reservation['route'],'text':'reply'}
        self.talk=AsyncMock(return_value=[{'talk_id':'9','chat_id':'chat','origin':'wa','is_in_work':True}])
        self.messages=AsyncMock(return_value=[{'id':'source','chat_id':'chat','created_at':100,'type':'incoming'}])
        self.provider=AsyncMock(return_value={'id':7,'pipeline_id':1,'status_id':2})
        self.send=Mock(return_value='provider-id');self.finish=Mock();self.mark=Mock()
        self.inline_to_thread=AsyncMock(side_effect=lambda fn,*args,**kwargs:fn(*args,**kwargs))

    async def invoke(self):
        return await send_service.run_agent(self.job,self.reservation,'reply',self.provider,Mock(),Mock())

    async def test_auto_send_uses_shared_receipt_and_single_provider_post(self):
        with patch.object(send_service,'reserve_agent',return_value=self.item),patch.object(send_service,'mark_agent_sending',self.mark),patch.object(send_service,'finish_agent',self.finish),patch.object(send_service,'read_talks',self.talk),patch.object(send_service,'read_messages',self.messages),patch.object(send_service,'send',self.send),patch.object(send_service,'assert_job_allowed'),patch.object(send_service,'assert_live_job_allowed',AsyncMock()),patch.object(send_service.asyncio,'to_thread',self.inline_to_thread):
            result=await self.invoke()
        self.assertEqual(result,{'state':'accepted','message_id':'provider-id'})
        self.mark.assert_called_once_with(self.item);self.send.assert_called_once();self.finish.assert_called_once_with(self.item,'accepted','provider-id')

    async def test_new_manager_message_blocks_post(self):
        self.messages.return_value.append({'id':'manager','chat_id':'chat','created_at':101,'type':'outgoing'})
        with patch.object(send_service,'reserve_agent',return_value=self.item),patch.object(send_service,'mark_agent_sending',self.mark),patch.object(send_service,'finish_agent',self.finish),patch.object(send_service,'read_talks',self.talk),patch.object(send_service,'read_messages',self.messages),patch.object(send_service,'send',self.send),patch.object(send_service,'assert_job_allowed'),patch.object(send_service,'assert_live_job_allowed',AsyncMock()),patch.object(send_service.asyncio,'to_thread',self.inline_to_thread):
            with self.assertRaises(TenantLinearError):await self.invoke()
        self.send.assert_not_called();self.mark.assert_not_called();self.finish.assert_called_once_with(self.item,'blocked')

    async def test_timeout_is_unknown_and_never_retried(self):
        with patch.object(send_service,'reserve_agent',return_value=self.item),patch.object(send_service,'mark_agent_sending',self.mark),patch.object(send_service,'finish_agent',self.finish),patch.object(send_service,'read_talks',self.talk),patch.object(send_service,'read_messages',self.messages),patch.object(send_service,'send',Mock(side_effect=RuntimeError('timeout'))),patch.object(send_service,'assert_job_allowed'),patch.object(send_service,'assert_live_job_allowed',AsyncMock()),patch.object(send_service.asyncio,'to_thread',self.inline_to_thread):
            with self.assertRaises(TenantLinearError):await self.invoke()
        self.finish.assert_called_once_with(self.item,'unknown')


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.job={'tenant_id':TENANT,'agent_id':AGENT,'provider':'kommo','conversation_id':'chat','chat_id':'chat','talk_id':'9','lead_id':7,'event_fingerprint':FINGERPRINT,'revision':1,'lease_token':'lease','channel_id':'wa','payload':{'message_id':'source','contact_id':77,'channel':'wa'}}
        self.talk={'talk_id':'9','chat_id':'chat','origin':'wa','is_in_work':True}
        self.message={'id':'source','chat_id':'chat','created_at':100,'type':'incoming','text':'Salam'}
        self.request=AsyncMock(side_effect=[{'id':7,'pipeline_id':1,'status_id':2}])
        self.logger=Mock()
        self.reservation={'run_id':'run','api_key':'secret','config':{},'config_version':'cv','settings_version':'sv','company_instructions':'Company truth','agent_name':'Agent','agent_instructions':'Agent rules','agent_language':'az','agent_style':'balanced','scope_instructions':'Stage rules','agent_version':1,'conversation_revision':3,'mode':'manual','auto_send_enabled':False,'crm_actions_enabled':False,'max_replies_per_chat':20,'model':'gpt-mini'}

    async def call_worker(self, message_pages=None, media_result=None):
        media_result = media_result or ({},[],set())
        inline_to_thread=AsyncMock(side_effect=lambda fn,*args,**kwargs:fn(*args,**kwargs))
        with patch.object(worker,'read_talks',AsyncMock(return_value=[self.talk])),patch.object(worker,'read_messages',AsyncMock(side_effect=message_pages or [[self.message],[self.message]])),patch.object(worker,'load_agent_config',None,create=True),patch('tenant_ai_agent_run_store.load_agent_config',return_value={'scopes':[{'pipeline_id':'1','stage_id':'2'}],'agent_model':'gpt-mini','chat_model':'gpt-mini','schedule':{},'mode':'manual','crm_actions_enabled':False}),patch.object(worker,'enabled_triggers',return_value=[]),patch.object(worker,'should_handoff',return_value=False),patch.object(worker,'knowledge_context',return_value=[{'source_id':'kb','title':'FAQ','content':'Open hours'}]),patch.object(worker,'history',return_value=([{'speaker':'CLIENT','text':'Salam'}],0)),patch.object(worker,'_prepare_agent_media',AsyncMock(return_value=media_result)),patch.object(worker,'reserve_run',return_value=self.reservation),patch.object(worker,'generate',return_value='Salam, sizə necə kömək edə bilərəm?') as generate,patch.object(worker,'complete_run',return_value=True) as complete,patch.object(worker,'execute_matching_actions',AsyncMock(return_value={'handoff':False,'actions':[]})),patch.object(worker,'_next_open',return_value=None),patch.object(worker,'finish_superseded') as superseded,patch.object(worker,'assert_job_allowed'),patch.object(worker,'assert_live_job_allowed',AsyncMock()),patch.object(worker.asyncio,'to_thread',inline_to_thread):
            await worker.process_one(self.job,self.request,Mock(),self.logger)
        return complete,superseded,generate

    async def test_worker_builds_manual_draft_without_sending(self):
        complete,superseded,generate=await self.call_worker()
        complete.assert_called_once();superseded.assert_not_called()
        self.assertIn('Company truth',generate.call_args.kwargs['instructions'])
        self.assertIn('Agent rules',generate.call_args.kwargs['instructions'])
        self.assertIn('Stage rules',generate.call_args.kwargs['instructions'])
        self.assertEqual(generate.call_args.kwargs['extra_context']['knowledge'][0]['content'],'Open hours')

    async def test_manager_reply_during_generation_discards_answer(self):
        pages=[[self.message],[self.message,{ 'id':'manager','chat_id':'chat','created_at':101,'type':'outgoing','text':'I will handle this'}]]
        complete,superseded,_=await self.call_worker(pages)
        complete.assert_not_called();superseded.assert_called_once()

    async def test_worker_passes_downloaded_image_to_chat_ai_generation(self):
        image={'message_id':'photo','content':b'actual-image','content_type':'image/jpeg'}
        _,_,generate=await self.call_worker(media_result=({},[image],{'photo'}))
        self.assertEqual(generate.call_args.kwargs['images'],[image])
        self.assertEqual(generate.call_args.kwargs['extra_context']['image_message_ids_for_analysis'],['photo'])


class AgentMediaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.rows=[{'external_id':'voice','direction':'incoming','message_type':'audio','media_url':'https://cdn.example.com/v.ogg'},
                   {'external_id':'photo','direction':'incoming','message_type':'image','media_url':'https://cdn.example.com/p.png'}]
        self.logger=Mock()

    async def prepare(self, download_side_effect, transcribe_side_effect='spoken words', config=None):
        inline=AsyncMock(side_effect=lambda fn,*args,**kwargs:fn(*args,**kwargs))
        with patch.object(worker.asyncio,'to_thread',inline), patch.object(worker,'download',side_effect=download_side_effect) as download, \
             patch.object(worker,'transcribe',return_value=transcribe_side_effect) as transcribe:
            result=await worker._prepare_agent_media(self.rows,config or {'audio_enabled':True,'transcription_model':'audio-model','media_hosts':['cdn.example.com']},'secret',self.logger)
        return result,download,transcribe

    async def test_audio_transcription_and_image_bytes_are_returned_for_model_input(self):
        result,download,transcribe=await self.prepare([(b'audio','ogg','audio/ogg'),(b'pngbytes','png','image/png')])
        transcripts,images,analyzed=result
        self.assertEqual(transcripts,{'voice':'spoken words'})
        self.assertEqual(images,[{'message_id':'photo','content':b'pngbytes','content_type':'image/png'}])
        self.assertEqual(analyzed,{'photo'})
        self.assertEqual(download.call_count,2);transcribe.assert_called_once()
        lines,missing=chat_history(self.rows,transcripts,analyzed_media=analyzed)
        self.assertEqual(missing,0)
        self.assertIn('spoken words',lines[0]['text'])
        self.assertIn('vision model',lines[1]['text'])

    async def test_download_error_and_unsupported_audio_and_image_are_unprocessed(self):
        result,_,transcribe=await self.prepare([ValueError('expired link'),(b'html','html','text/html')])
        transcripts,images,analyzed=result
        self.assertEqual((transcripts,images,analyzed),({},[],set()))
        transcribe.assert_not_called()
        _,missing=chat_history(self.rows,transcripts,analyzed_media=analyzed)
        self.assertEqual(missing,2)

    async def test_audio_setting_off_does_not_transcribe_and_reports_missing(self):
        result,download,transcribe=await self.prepare([],config={'audio_enabled':False,'media_hosts':[]})
        transcripts,images,analyzed=result
        self.assertEqual(transcripts,{})
        self.assertEqual(download.call_count,1) # image still uses the same allowlisted loader
        transcribe.assert_not_called()
        _,missing=chat_history(self.rows,transcripts,analyzed_media=analyzed)
        self.assertEqual(missing,2)

    def test_provider_sends_actual_image_bytes_as_responses_input_image(self):
        from tenant_chat_ai_provider import generate
        response=Mock(status_code=200)
        response.json.return_value={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'Looked at it.'}]}]}
        with patch('tenant_chat_ai_provider.requests.post',return_value=response) as post:
            self.assertEqual(generate('secret','vision-model','reply',[{'speaker':'CLIENT','text':'What is this?'}],'',images=[
                {'message_id':'photo','content':b'pngbytes','content_type':'image/png'}]),'Looked at it.')
        body=post.call_args.kwargs['json']
        self.assertEqual(body['input'][0]['content'][1]['type'],'input_image')
        self.assertTrue(body['input'][0]['content'][1]['image_url'].startswith('data:image/png;base64,'))

    def test_pdf_extraction_uses_pypdf_when_installed(self):
        try:
            from pypdf import PdfReader, PdfWriter
        except ImportError:
            self.skipTest('pypdf is not installed in this Python environment')
        from tenant_ai_knowledge_files import extract
        output=BytesIO();writer=PdfWriter();page=writer.add_blank_page(width=300,height=300)
        from pypdf.generic import DictionaryObject, NameObject, TextStringObject
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        font_ref=writer._add_object(font);page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font_ref})})
        page[NameObject('/Contents')]=writer._add_object(__import__('pypdf').generic.DecodedStreamObject())
        stream=page['/Contents'].get_object();stream.set_data(b'BT /F1 12 Tf 20 100 Td (Trusted PDF fact) Tj ET')
        writer.write(output)
        filename,kind,text=extract('facts.pdf',output.getvalue())
        self.assertEqual((filename,kind),('facts.pdf','pdf'))
        self.assertIn('Trusted PDF fact',text)

    def test_attachment_without_media_url_is_explicitly_unprocessed(self):
        lines,missing=chat_history([{'external_id':'unavailable-photo','direction':'incoming','body':'What is this?',
            'message_type':'image','media_url':'','has_attachment':True}],{},analyzed_media=set())
        self.assertEqual(missing,1)
        self.assertIn('Attachment not analyzed',lines[0]['text'])


if __name__=='__main__':unittest.main()
