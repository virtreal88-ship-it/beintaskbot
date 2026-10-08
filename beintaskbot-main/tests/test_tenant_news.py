"""News review/publication contracts with simulated SQL, no real publication."""
import unittest
from unittest.mock import Mock, MagicMock
from datetime import datetime, timezone
import test_tenant_hot_orders as loaders
from tenant_news_policy import fields, link, cursor, position, public
from tenant_linear_policy import TenantLinearError

TENANT='aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa'
OTHER='bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb'
STAMP=datetime(2026,10,8,12,tzinfo=timezone.utc)
SESSION={'tenant_id':TENANT,'telegram_id':20}
CONTENT={'title':'New feature','summary':'Short explanation','project_name':'AKUL','url':''}


def row(**changes):
    return {**CONTENT,'id':OTHER,'identifier':'BS-10','status':'pending','created_at':STAMP,'updated_at':STAMP,
        'published_at':None,'source':{'description':'PRIVATE CUSTOMER','account':'PRIVATE ACCOUNT'},'reviewed_by':20,**changes}


def profile(**changes):
    return {**SESSION,'role':'owner','active':True,'tenant_status':'active','modules':{'linear':True},'permissions':['linear'],**changes}


class Policy(unittest.TestCase):
    def test_fields_required_short_and_no_url_default(self):
        self.assertEqual(fields(CONTENT),CONTENT);self.assertEqual(link(''),'')
        for change in ({'title':''},{'summary':''},{'project_name':''},{'summary':'x'*1201},{'title':None}):
            with self.assertRaises(TenantLinearError):fields({**CONTENT,**change})

    def test_phone_preserved_and_safe_link_only(self):
        number='+994 50 123 45 67';self.assertEqual(fields({**CONTENT,'url':number})['url'],number)
        self.assertEqual(link(number),'tel:+994501234567');self.assertEqual(link('https://akul.az'),'https://akul.az')
        for value in ('javascript:alert(1)','data:text/html,x','https://user:password@example.org','https://example.org/\nunsafe','www.akul.az'):
            with self.assertRaises(TenantLinearError):link(value)

    def test_cursor_roundtrip_and_invalid_inputs(self):
        self.assertEqual(position(cursor(row(),'created_at')),(STAMP,OTHER));self.assertIsNone(position(None))
        for value in ('bad','x'*401):
            with self.assertRaises(TenantLinearError):position(value)

    def test_serialization_never_exposes_raw_source(self):
        result=public(row());self.assertNotIn('PRIVATE',str(result));self.assertNotIn('reviewed_by',result)


class Storage(unittest.TestCase):
    def setUp(self):
        self.connect=MagicMock();self.conn=self.connect.return_value.__enter__.return_value
        self.cur=self.conn.cursor.return_value.__enter__.return_value
        self.service=loaders.load('tenant_news',{'_connect':self.connect,'_read_tenant_workflow':Mock(return_value={}),
            'ensure_observer_schema':Mock()}, {'tenant_platform','tenant_news_telegram_schema'})
        self.cur.fetchone.side_effect=[profile(),{'metadata':{}}]

    def test_review_requires_live_member_and_explicit_right_not_admin_role(self):
        for change in ({'active':False},{'tenant_status':'inactive'},{'role':'admin'},{'role':'worker','permissions':[]}):
            self.cur.fetchone.side_effect=[profile(**change),{'metadata':{}}]
            with self.assertRaises(TenantLinearError):self.service.access(self.cur,SESSION)
        self.cur.fetchone.side_effect=[profile(role='worker'),{'metadata':{'settings':{'members':{'20':{'can_review_news':True}}}}}]
        self.assertEqual(self.service.access(self.cur,SESSION),(TENANT,20))

    def test_owner_archive_access_without_connection_or_decryption(self):
        self.cur.fetchone.side_effect=[profile(),None]
        self.assertEqual(self.service.access(self.cur,SESSION),(TENANT,20))

    def test_company_scoped_keyset_listing(self):
        self.cur.fetchall.return_value=[row() for _ in range(31)]
        result=self.service.listing(SESSION);self.assertEqual(len(result['news']),30);self.assertTrue(result['after'])
        sql,args=self.cur.execute.call_args.args;self.assertEqual(args,(TENANT,'pending'))
        self.assertIn('LIMIT 31',sql);self.assertIn('90 days',sql);self.assertNotIn('PRIVATE',str(result))

    def test_save_and_publish_one_update_only(self):
        self.cur.fetchone.side_effect=[profile(),None,row(),row(status='published',published_at=STAMP)]
        result=self.service.command(SESSION,{**CONTENT,'action':'publish','id':OTHER,'expected_updated_at':str(STAMP),'confirm_publication':True})
        self.assertEqual(result['news']['status'],'published');self.conn.commit.assert_called_once()
        updates=[call for call in self.cur.execute.call_args_list if 'UPDATE saas_linear_news SET' in call.args[0]]
        self.assertEqual(len(updates),1);self.assertEqual(updates[0].args[1][-2:],(TENANT,OTHER))
        self.assertIn("status='published'",updates[0].args[0]);self.assertEqual(updates[0].args[1][3],'')

    def test_repeated_publish_is_read_only(self):
        self.cur.fetchone.side_effect=[profile(),None,row(status='published',published_at=STAMP)]
        result=self.service.command(SESSION,{**CONTENT,'action':'publish','id':OTHER,'expected_updated_at':'old','confirm_publication':True})
        self.assertEqual(result['news']['status'],'published')
        self.assertNotIn('UPDATE saas_linear_news SET',str(self.cur.execute.call_args_list))

    def test_stale_edit_foreign_or_missing_news_and_no_confirmation(self):
        with self.assertRaises(TenantLinearError):self.service.command(SESSION,{**CONTENT,'action':'publish','id':OTHER})
        self.connect.assert_not_called()
        for record,status in ((row(),409),(None,404)):
            self.cur.fetchone.side_effect=[profile(),None,record]
            with self.assertRaises(TenantLinearError) as caught:
                self.service.command(SESSION,{**CONTENT,'action':'publish','id':OTHER,'expected_updated_at':'old','confirm_publication':True})
            self.assertEqual(caught.exception.status,status)
        self.conn.commit.assert_not_called()

    def test_reject_only_pending(self):
        self.cur.fetchone.side_effect=[profile(),None,row(),row(status='rejected')]
        self.assertEqual(self.service.command(SESSION,{'action':'reject','id':OTHER,'expected_updated_at':str(STAMP)})['news']['status'],'rejected')
        self.cur.fetchone.side_effect=[profile(),None,row(status='published')]
        with self.assertRaises(TenantLinearError):self.service.command(SESSION,{'action':'reject','id':OTHER,'expected_updated_at':str(STAMP)})

    def test_manual_retry_has_same_deterministic_key(self):
        import hashlib,json
        fingerprint=hashlib.sha256(json.dumps(CONTENT,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        data={**CONTENT,'action':'create_publish','request_id':OTHER,'confirm_publication':True}
        self.cur.fetchone.side_effect=[profile(),None,None,{'issue_id':OTHER},row(status='published',source={'fingerprint':fingerprint})]
        self.service.command(SESSION,data)
        first_key=[call.args[1][1] for call in self.cur.execute.call_args_list if 'SELECT * FROM saas_linear_news' in call.args[0]][0]
        self.cur.reset_mock();self.cur.fetchone.side_effect=[profile(),None,row(status='published',source={'fingerprint':fingerprint})]
        self.service.command(SESSION,data)
        self.assertEqual(self.cur.execute.call_args_list[-1].args[1][1],first_key)
        self.assertNotIn('INSERT INTO',str(self.cur.execute.call_args_list))

    def test_expired_manual_receipt_prevents_recreation(self):
        self.cur.fetchone.side_effect=[profile(),None,None,None]
        with self.assertRaises(TenantLinearError) as caught:
            self.service.command(SESSION,{**CONTENT,'action':'create_publish','request_id':OTHER,'confirm_publication':True})
        self.assertEqual(caught.exception.status,409);self.conn.commit.assert_not_called()

    def test_public_feed_only_approved_active_company_and_no_private_fields(self):
        self.cur.fetchone.side_effect=[{'id':TENANT}];self.cur.fetchall.return_value=[row(status='published',published_at=STAMP)]
        result=self.service.published(TENANT);self.assertNotIn('PRIVATE',str(result));self.assertNotIn('reviewed_by',str(result))
        sql,args=self.cur.execute.call_args.args;self.assertEqual(args,(TENANT,))
        self.assertIn("status='published'",sql);self.assertIn('90 days',sql)
        self.cur.fetchone.side_effect=[None]
        with self.assertRaises(TenantLinearError) as caught:self.service.published(TENANT)
        self.assertEqual(caught.exception.status,404)

    def test_optional_telegram_intent_is_committed_with_publication(self):
        import sys
        from types import SimpleNamespace
        from unittest.mock import patch
        self.cur.fetchone.side_effect=[profile(),None,row(),row(status='published',published_at=STAMP)]
        def enqueue(cur,tenant,user,record,data):
            self.conn.commit.assert_not_called();self.assertEqual(record['status'],'published');self.assertEqual(tenant,TENANT)
            return 'pending'
        hook=Mock(side_effect=enqueue)
        with patch.dict(sys.modules,{'tenant_news_telegram_queue':SimpleNamespace(enqueue=hook)}):
            result=self.service.command(SESSION,{**CONTENT,'action':'publish','id':OTHER,'expected_updated_at':str(STAMP),'confirm_publication':True,'confirm_telegram':True})
        self.assertEqual(result['telegram_status'],'pending');hook.assert_called_once();self.conn.commit.assert_called_once()

    def test_channel_conflict_does_not_commit_half_publication(self):
        import sys
        from types import SimpleNamespace
        from unittest.mock import patch
        self.cur.fetchone.side_effect=[profile(),None,row(),row(status='published',published_at=STAMP)]
        with patch.dict(sys.modules,{'tenant_news_telegram_queue':SimpleNamespace(enqueue=Mock(side_effect=TenantLinearError('Changed',409)))}):
            with self.assertRaises(TenantLinearError):self.service.command(SESSION,{**CONTENT,'action':'publish','id':OTHER,'expected_updated_at':str(STAMP),'confirm_publication':True,'confirm_telegram':True})
        self.conn.commit.assert_not_called()


if __name__=='__main__':unittest.main()
