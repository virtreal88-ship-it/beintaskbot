"""No paid calls, real media or production database are used by these contracts."""
import asyncio
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, MagicMock, patch
import test_tenant_hot_orders as loaders
from tenant_chat_ai_policy import configuration, command, history, media_address, media_identity, fingerprint
from tenant_chat_media import public_addresses, download
from tenant_linear_policy import TenantLinearError
from tenant_policy import TenantPolicy, validate_workflow_patch

TENANT='aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'
REQUEST='bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb'
SESSION={'tenant_id':TENANT,'telegram_id':20}
COMMAND={'request_id':REQUEST,'lead_id':10,'mode':'reply','draft':'','message_id':''}
CONFIG={'enabled':True,'consent':True,'audio_enabled':True,'model':'text-model','transcription_model':'audio-model','daily_limit':50,'media_hosts':['cdn.example.com']}
CONNECTION={'secrets':b'cipher','metadata':CONFIG,'updated_at':'version-1'}
ROW={'external_id':'voice-1','message_type':'audio','media_url':'https://cdn.example.com/voice.ogg','direction':'incoming','body':'','happened_at':'2026-10-08'}


class Policy(unittest.TestCase):
    def test_configuration_requires_consent_and_strict_types(self):
        self.assertEqual(configuration(CONFIG),CONFIG)
        for changes in ({'enabled':1},{'consent':False},{'daily_limit':True},{'model':'bad model'},{'media_hosts':['*.example.com']},{'media_hosts':['https://cdn.example.com']}):
            with self.assertRaises(TenantLinearError):configuration({**CONFIG,**changes})

    def test_commands_are_bounded_and_no_arbitrary_url_input(self):
        self.assertEqual(command(COMMAND),COMMAND)
        for changes in ({'mode':'send'},{'lead_id':True},{'draft':'x'*3501},{'request_id':'bad'},{'mode':'transcribe'}):
            with self.assertRaises(TenantLinearError):command({**COMMAND,**changes})
        self.assertNotIn('url',command({**COMMAND,'url':'https://private.invalid'}))

    def test_last_thirty_speakers_and_unavailable_audio(self):
        lines,missing=history([{'direction':'incoming','body':str(i)} for i in range(31)]+[ROW,{'direction':'outgoing','body':'answer'}],{})
        self.assertEqual(len(lines),30);self.assertEqual(lines[0]['text'],'3')
        self.assertEqual(lines[-1]['speaker'],'SALES MANAGER');self.assertEqual(lines[-2]['speaker'],'CLIENT')
        self.assertEqual(missing,1);self.assertIn('do not infer',lines[-2]['text'])
        self.assertEqual(history([ROW],{'voice-1':'transcript'})[1],0)
        self.assertEqual(history([{'direction':'ambiguous'}],{})[0][0]['speaker'],'UNKNOWN SPEAKER')

    def test_media_exact_domain_https_no_credentials_ports_or_controls(self):
        self.assertEqual(media_address(ROW['media_url'],CONFIG['media_hosts']).hostname,'cdn.example.com')
        for url in ('http://cdn.example.com/a','https://cdn.example.com.evil/a','https://user:secret@cdn.example.com/a','https://cdn.example.com:444/a','https://cdn.example.com/a\n','https://cdn.example.com/a#fragment','https://cdn.example.com\\@localhost/a'):
            with self.assertRaises(ValueError):media_address(url,CONFIG['media_hosts'])

    def test_media_hash_includes_url_and_model(self):
        self.assertNotEqual(media_identity(ROW,'audio-model'),media_identity(ROW,'other'))
        self.assertNotEqual(media_identity(ROW,'audio-model'),media_identity({**ROW,'media_url':'new'},'audio-model'))

    def test_permissions_not_implied_by_admin_or_name(self):
        base={**SESSION,'active':True,'role':'admin','permissions':['customers'],'modules':{'customers':True},'workflow':{'policies':{'members':{'20':{'ai_enabled':True}}}}}
        self.assertTrue(TenantPolicy(base).can_use_chat_ai())
        self.assertFalse(TenantPolicy({**base,'workflow':{}}).can_use_chat_ai())
        self.assertFalse(TenantPolicy({**base,'role':'master'}).can_use_chat_ai())
        self.assertFalse(TenantPolicy({**base,'permissions':[]}).can_use_chat_ai())
        with self.assertRaises(ValueError):validate_workflow_patch(None,None,{'members':{'20':{'ai_enabled':1}}})

    def test_dns_private_mixed_answers_blocked_before_connect(self):
        for addresses in (['127.0.0.1'],['169.254.169.254'],['::1'],['1.1.1.1','10.0.0.1'],['::ffff:127.0.0.1']):
            with patch('tenant_chat_media.socket.getaddrinfo',return_value=[(2,1,6,'',(ip,443)) for ip in addresses]):
                with self.assertRaises(ValueError):public_addresses('cdn.example.com')

    def test_media_no_redirect_and_content_limit(self):
        for status,kind,size in ((302,'audio/ogg','10'),(200,'text/html','10'),(200,'audio/ogg',str(21*1024*1024))):
            response=Mock(status=status);response.getheader.side_effect=lambda key,default=None:{'Content-Type':kind,'Content-Length':size}.get(key,default)
            connection=Mock();connection.getresponse.return_value=response
            with patch('tenant_chat_media.public_addresses',return_value=['1.1.1.1']),patch('tenant_chat_media.PinnedHTTPS',return_value=connection):
                with self.assertRaises(ValueError):download(ROW['media_url'],CONFIG['media_hosts'])
            connection.close.assert_called_once();response.read1.assert_not_called()

    def test_talk_normalization_attachment_and_unknown_sender(self):
        tree=ast.parse((Path(__file__).resolve().parents[1]/'bot.py').read_text(encoding='utf-8-sig'))
        node=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='_tenant_normalize_talk_message')
        namespace={'_tenant_kommo_timestamp':lambda value:value}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'normalize','exec'),namespace)
        normalize=namespace['_tenant_normalize_talk_message']
        result=normalize({'direction':'incoming','type':'incoming','attachment':{'type':'audio','url':ROW['media_url']}},'whatsapp')
        self.assertEqual(result['direction'],'incoming');self.assertEqual(result['message_type'],'audio')
        self.assertEqual(normalize({'type':'audio'},'other')['direction'],'unknown')
        self.assertEqual(normalize({'incoming':False},'other')['direction'],'outgoing')

    def test_visible_history_limits_recent_rows_then_orders_chronologically(self):
        tree=ast.parse((Path(__file__).resolve().parents[1]/'tenant_platform.py').read_text(encoding='utf-8-sig'))
        node=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='list_crm_messages')
        connect=MagicMock();cur=connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value=[]
        namespace={'_connect':connect,'_ensure_schema':Mock(),'_public_crm_row':lambda row:row}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'history','exec'),namespace)
        namespace['list_crm_messages'](tenant_id=TENANT,kommo_lead_id=10,limit=1000)
        sql,params=cur.execute.call_args.args
        self.assertLess(sql.index('DESC'),sql.index('LIMIT'));self.assertLess(sql.index('LIMIT'),sql.index(' ASC'))
        self.assertEqual(params,(TENANT,10,300))


class Storage(unittest.TestCase):
    def setUp(self):
        self.connect=MagicMock();self.conn=self.connect.return_value.__enter__.return_value
        self.cur=self.conn.cursor.return_value.__enter__.return_value
        self.fernet=Mock();self.fernet.decrypt.return_value=b'{"tenant_id":"'+TENANT.encode()+b'","provider":"chat_ai","api_key":"company-key"}'
        self.service=loaders.load('tenant_chat_ai_store',{'_connect':self.connect,'_fernet':lambda:self.fernet,'_read_tenant_workflow':Mock(return_value={}), 'ensure_schema':Mock()}, {'tenant_platform','tenant_chat_ai_schema'})
        self.access=Mock(return_value=(TENANT,20,CONNECTION));self.service.reserve.__globals__['access']=self.access
        self.cur.fetchall.return_value=[{'external_id':'text-1','direction':'incoming','body':'hello','message_type':'text'}]
        self.cur.fetchone.side_effect=[None,{'used':0}]

    def test_receipt_and_quota_committed_before_provider(self):
        item=self.service.reserve(SESSION,COMMAND)
        self.assertEqual(item['api_key'],'company-key');self.conn.commit.assert_called_once()
        sql=' '.join(call.args[0] for call in self.cur.execute.call_args_list)
        self.assertIn('LIMIT 30',sql);self.assertIn('DESC',sql);self.assertIn('reserved_calls',sql)
        self.assertIn('INSERT INTO saas_chat_ai_runs',sql)
        self.assertEqual(self.cur.execute.call_args_list[0].args[1][0],'tenant-chat-ai:'+TENANT)

    def test_completed_receipt_replays_without_new_quota(self):
        self.cur.fetchone.side_effect=[{'requested_by':20,'input_hash':fingerprint(COMMAND),'status':'completed','result':{'text':'saved'},'config_version':'version-1'}]
        self.assertEqual(self.service.reserve(SESSION,COMMAND),{'cached_result':{'text':'saved'}})
        self.fernet.decrypt.assert_not_called();self.conn.commit.assert_not_called()

    def test_same_uuid_different_author_or_payload_rejected(self):
        for changes in ({'requested_by':21},{'input_hash':'other'}):
            self.cur.fetchone.side_effect=[{'requested_by':20,'input_hash':fingerprint(COMMAND),**changes}]
            with self.assertRaises(TenantLinearError):self.service.reserve(SESSION,COMMAND)

    def test_unknown_never_replayed_and_revocation_checked_first(self):
        self.cur.fetchone.side_effect=[{'requested_by':20,'input_hash':fingerprint(COMMAND),'status':'unknown'}]
        with self.assertRaises(TenantLinearError) as error:self.service.reserve(SESSION,COMMAND)
        self.assertTrue(error.exception.keep_request)
        self.access.side_effect=TenantLinearError('Denied',403)
        with self.assertRaises(TenantLinearError):self.service.reserve(SESSION,COMMAND)
        self.fernet.decrypt.assert_not_called()

    def test_daily_limit_does_not_call_provider_or_commit_receipt(self):
        self.cur.fetchone.side_effect=[None,{'used':50}]
        with self.assertRaises(TenantLinearError) as error:self.service.reserve(SESSION,COMMAND)
        self.assertEqual(error.exception.status,429);self.fernet.decrypt.assert_not_called();self.conn.commit.assert_not_called()

    def test_cross_company_encrypted_credential_rejected(self):
        self.fernet.decrypt.return_value=b'{"tenant_id":"other","provider":"chat_ai","api_key":"key"}'
        with self.assertRaises(TenantLinearError):self.service.reserve(SESSION,COMMAND)
        self.conn.commit.assert_not_called()

    def test_real_access_rechecks_membership_deal_and_configuration(self):
        real=loaders.load('tenant_chat_ai_store',{'_connect':self.connect,'_fernet':lambda:self.fernet,'_read_tenant_workflow':Mock(return_value={'pipelines':[{'pipeline_id':1}]}),'ensure_schema':Mock()}, {'tenant_platform','tenant_chat_ai_schema'})
        person={**SESSION,'role':'owner','active':True,'tenant_status':'active'}
        deal={'tenant_id':TENANT,'pipeline_id':1,'status_id':10}
        for changes in ({'active':False},{'tenant_status':'inactive'}):
            self.cur.fetchone.side_effect=[{**person,**changes}]
            with self.assertRaises(TenantLinearError):real.access(self.cur,SESSION,10)
        self.cur.fetchone.side_effect=[person,{**deal,'tenant_id':'foreign'}]
        with self.assertRaises(TenantLinearError):real.access(self.cur,SESSION,10)
        self.cur.fetchone.side_effect=[person,deal,CONNECTION]
        self.assertEqual(real.access(self.cur,SESSION,10),(TENANT,20,CONNECTION))


class Service(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.item={'tenant_id':TENANT,'user_id':20,'config_version':'version-1','command':COMMAND,'config':CONFIG,'api_key':'company-key', 'rows':[ROW], 'pending_audio':[{**ROW,'media_hash':'hash'}], 'transcripts':{}}
        self.reserve=Mock(return_value=self.item);self.finish=Mock();self.validate=Mock();self.cache=Mock();self.generate=Mock(return_value='draft');self.transcribe=Mock(return_value='voice text');self.download=Mock(return_value=(b'audio','ogg','audio/ogg'))
        self.service=loaders.load('tenant_chat_ai_service',{'reserve':self.reserve,'finish':self.finish,'validate':self.validate,'cache_transcript':self.cache,'generate':self.generate,'transcribe':self.transcribe,'download':self.download}, {'tenant_chat_ai_store','tenant_chat_media','tenant_chat_ai_provider'})
        self.logger=Mock()

    async def test_audio_is_transcribed_before_reply_and_cache_scoped(self):
        result=await self.service.run(SESSION,COMMAND,self.logger)
        self.assertEqual(result['missing_audio'],0);self.assertEqual(result['transcribed_audio'],1)
        self.assertIn('voice text',self.generate.call_args.args[3][0]['text']);self.cache.assert_called_once()
        self.finish.assert_called_once_with(self.item,result)

    async def test_missing_audio_disclosed_not_invented(self):
        self.download.side_effect=ValueError('blocked')
        result=await self.service.run(SESSION,COMMAND,self.logger)
        self.assertEqual(result['missing_audio'],1);self.transcribe.assert_not_called()
        self.assertIn('do not infer',self.generate.call_args.args[3][0]['text'])

    async def test_revoked_access_stops_paid_call(self):
        self.validate.side_effect=TenantLinearError('Denied',403)
        with self.assertRaises(TenantLinearError):await self.service.run(SESSION,COMMAND,self.logger)
        self.generate.assert_not_called();self.transcribe.assert_not_called();self.finish.assert_called_once_with(self.item,None)

    async def test_provider_timeout_unknown_receipt_without_retry(self):
        self.generate.side_effect=TimeoutError()
        with self.assertRaises(TenantLinearError):await self.service.run(SESSION,COMMAND,self.logger)
        self.generate.assert_called_once();self.finish.assert_called_once_with(self.item,None)


if __name__=='__main__':unittest.main()
