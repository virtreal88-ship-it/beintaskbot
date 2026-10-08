import json
import unittest
from unittest.mock import Mock, MagicMock, patch
from cryptography.fernet import Fernet
import test_tenant_hot_orders as loaders
from tenant_linear_policy import TenantLinearError
import tenant_news_ai_provider as provider
from test_tenant_news import TENANT, OTHER, STAMP, SESSION, row


class Provider(unittest.TestCase):
    def response(self,**changes):
        result={'category':'feature','confidence':.9,'evidence':'','title':'Yeni imkan','summary':'Qısa xəbər',**changes}
        return Mock(status_code=200,json=Mock(return_value={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(result)}]}]}))

    def test_direct_bounded_request_and_uncertainty_not_excluded(self):
        with patch.object(provider.requests,'post',return_value=self.response(category='uncertain')) as post:
            result=provider.classify('company-key','gpt-4o-mini','Feature','Details')
        self.assertFalse(result['excluded']);args=post.call_args
        self.assertEqual(args.args[0],'https://api.openai.com/v1/responses');self.assertFalse(args.kwargs['allow_redirects'])
        self.assertFalse(args.kwargs['json']['store']);self.assertNotIn('tools',args.kwargs['json']);self.assertEqual(args.kwargs['json']['max_output_tokens'],1500)

    def test_bug_needs_high_confidence_and_verbatim_evidence(self):
        for confidence,evidence,excluded in ((.99,'broken feature',True),(.8,'broken feature',False),(.99,'invented evidence',False)):
            with patch.object(provider.requests,'post',return_value=self.response(category='bug',confidence=confidence,evidence=evidence)):
                self.assertEqual(provider.classify('key','model','Repair','broken feature')['excluded'],excluded)

    def test_bad_output_and_provider_failure_do_not_become_success(self):
        for response in (Mock(status_code=429),Mock(status_code=200,json=Mock(return_value={'status':'incomplete'})),
            self.response(confidence=True),self.response(confidence=float('nan')),self.response(summary='')):
            with patch.object(provider.requests,'post',return_value=response),self.assertRaises(ValueError):provider.classify('key','model','title','body')


class Settings(unittest.TestCase):
    def setUp(self):
        self.connect=MagicMock();self.conn=self.connect.return_value.__enter__.return_value;self.cur=self.conn.cursor.return_value.__enter__.return_value
        self.cipher=Fernet(Fernet.generate_key())
        self.service=loaders.load('tenant_news_ai_settings',{'_connect':self.connect,'_ensure_schema':Mock(),'_fernet':Mock(return_value=self.cipher)}, {'tenant_platform'})
        self.person={'role':'owner','active':True,'status':'active'}
        self.data={'enabled':True,'consent':True,'daily_limit':10,'model':'gpt-4o-mini','api_key':'company-private-key-123456','expected_updated_at':''}

    def test_key_encrypted_and_bound_to_company_no_echo(self):
        self.cur.fetchone.side_effect=[self.person,None,{'secrets':b'encrypted','metadata':{'enabled':True},'updated_at':STAMP}]
        result=self.service.save(SESSION,self.data);token=self.cur.execute.call_args.args[1][1]
        payload=json.loads(self.cipher.decrypt(token));self.assertEqual(payload['tenant_id'],TENANT);self.assertEqual(payload['provider'],'news_ai')
        self.assertNotIn('private',str(result));self.conn.commit.assert_called_once()

    def test_no_consent_budget_invalid_or_stale_settings_rejected(self):
        for changes in ({'consent':False},{'daily_limit':True},{'daily_limit':101}):
            with self.assertRaises(TenantLinearError):self.service.save(SESSION,{**self.data,**changes})
        self.connect.assert_not_called()
        self.cur.fetchone.side_effect=[self.person,{'updated_at':STAMP}]
        with self.assertRaises(TenantLinearError) as caught:self.service.save(SESSION,self.data)
        self.assertEqual(caught.exception.status,409);self.conn.commit.assert_not_called()

    def test_admin_cannot_change_owner_ai_key(self):
        self.cur.fetchone.return_value={**self.person,'role':'admin'}
        with self.assertRaises(TenantLinearError):self.service.read(SESSION)


class Worker(unittest.TestCase):
    def setUp(self):
        self.connect=MagicMock();self.conn=self.connect.return_value.__enter__.return_value;self.cur=self.conn.cursor.return_value.__enter__.return_value
        self.cipher=Fernet(Fernet.generate_key());self.classify=Mock()
        self.service=loaders.load('tenant_news_ai_worker',{'_connect':self.connect,'_fernet':Mock(return_value=self.cipher),
            'ensure_ai_schema':Mock(),'classify':self.classify}, {'tenant_platform','tenant_news_ai_schema','tenant_news_ai_provider'})
        self.item={**row(),'tenant_id':TENANT,'api_key':'company-key','model':'gpt-4o-mini','config_version':'v1'}
        self.connection={'metadata':{'enabled':True,'consent':True,'daily_limit':10,'model':'gpt-4o-mini'},'updated_at':'v1',
            'secrets':self.cipher.encrypt(json.dumps({'tenant_id':TENANT,'provider':'news_ai','api_key':'company-key'}).encode())}

    def test_paid_call_receipt_committed_first_and_quota_checked(self):
        self.cur.fetchone.side_effect=[self.item,self.connection,{'used':0},{'news_id':OTHER}]
        item=self.service.claim();self.assertEqual(item['api_key'],'company-key');self.conn.commit.assert_called_once()
        sql=str(self.cur.execute.call_args_list);self.assertIn("'sending'",sql);self.assertIn('UTC',sql);self.assertIn('daily_limit',sql)
        self.classify.assert_not_called()

    def test_foreign_key_quarantined_without_provider(self):
        connection={**self.connection,'secrets':self.cipher.encrypt(json.dumps({'tenant_id':OTHER,'provider':'news_ai','api_key':'secret'}).encode())}
        self.cur.fetchone.side_effect=[self.item,connection,{'used':0},{'news_id':OTHER}]
        self.assertIsNone(self.service.claim());self.classify.assert_not_called();self.conn.commit.assert_called_once()
        self.assertIn("status='unknown'",self.cur.execute.call_args.args[0])

    def test_daily_limit_prevents_charge(self):
        self.cur.fetchone.side_effect=[self.item,self.connection,{'used':10}]
        self.assertIsNone(self.service.claim());self.assertNotIn('INSERT INTO',str(self.cur.execute.call_args_list))

    def test_manual_publication_wins_over_late_ai_result(self):
        self.cur.fetchone.return_value={**row(status='published'),'ai_version':'v1','company_status':'active','ai_config':self.connection['metadata']}
        self.service.finish(self.item,{'title':'AI title','summary':'AI summary','category':'feature','confidence':.99,'excluded':False})
        self.assertNotIn('UPDATE saas_linear_news SET',str(self.cur.execute.call_args_list));self.assertEqual(self.cur.execute.call_args.args[1][0],'skipped')

    def test_classifier_never_publishes(self):
        self.cur.fetchone.return_value={**row(),'ai_version':'v1','company_status':'active','ai_config':self.connection['metadata']}
        self.service.finish(self.item,{'title':'AI title','summary':'AI summary','category':'bug','confidence':.99,'excluded':True})
        news_update=self.cur.execute.call_args_list[-2]
        self.assertEqual(news_update.args[1][2],'rejected');self.assertNotIn("status='published'",str(self.cur.execute.call_args_list))


if __name__=='__main__':unittest.main()
