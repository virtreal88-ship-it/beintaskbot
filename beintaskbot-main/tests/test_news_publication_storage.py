"""Preserve published news when migrating the review queue."""
import ast
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock


class NewsPublicationStorageTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'tenant_platform.py').read_text(encoding='utf-8-sig'))
        names = {'list_linear_news', 'sync_linear_news_environments', 'require_linear_news_review_once', 'restore_linear_news_publications_once'}
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        self.conn = MagicMock()
        self.cur = self.conn.cursor.return_value.__enter__.return_value
        self.cur.fetchall.return_value = []
        connect = MagicMock()
        connect.return_value.__enter__.return_value = self.conn
        self.ns = {'_connect': connect, '_ensure_schema': MagicMock(), '_json': json.dumps}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), '<news-storage>', 'exec'), self.ns)

    def test_migration_preserves_delivery_marks(self):
        self.cur.fetchone.return_value = {'migration_key': 'test'}
        self.ns['require_linear_news_review_once'](migration_key='test')
        sql = self.cur.execute.call_args_list[-1].args[0]
        self.assertIn('first_published_at IS NULL', sql)
        self.assertIn('telegram_message_id IS NULL', sql)
        self.assertIn('telegram_published_at IS NULL', sql)
        self.assertNotIn('telegram_message_id=NULL', sql)
        self.assertNotIn('telegram_published_at=NULL', sql)

    def test_pending_pages_exclude_sent_news(self):
        self.ns['list_linear_news'](approval_status='pending', offset=60)
        sql, params = self.cur.execute.call_args.args
        self.assertIn('first_published_at IS NULL', sql)
        self.assertIn('LIMIT %s OFFSET %s', sql)
        self.assertEqual(params[-1], 60)

    def test_news_environment_recovered_from_snapshot_without_writes(self):
        self.cur.fetchall.return_value = [{
            'id': 'news', 'environment': 'Göstərilməyib',
            'raw': {'linear': {'labels': ['BETA']}},
            'title': 'Edited title', 'approval_status': 'approved',
        }]
        items = self.ns['list_linear_news']()
        self.assertEqual(items[0]['environment'], 'BETA')
        self.assertEqual(items[0]['title'], 'Edited title')
        self.assertEqual(items[0]['approval_status'], 'approved')
        self.assertEqual(self.cur.execute.call_count, 1)
        self.assertIn('SELECT', self.cur.execute.call_args.args[0])

    def test_live_environment_sync_is_batched_and_preserves_publication(self):
        self.ns['sync_linear_news_environments']([
            {'source_id': '1', 'labels': ['DEPLOY']},
            {'source_id': '2', 'labels': ['DEV']},
        ])
        self.assertEqual(self.cur.execute.call_count, 1)
        sql, params = self.cur.execute.call_args.args
        rows = json.loads(params[0])
        self.assertEqual([r['environment'] for r in rows], ['DEPLOY', 'DEV'])
        for field in ('title=', 'summary=', 'approval_status=', 'telegram_message_id=', 'expires_at='):
            self.assertNotIn(field, sql)
        self.assertIn('news.source_issue_id=source.source_id', sql)

    def test_owner_report_is_recovered_once_without_resending(self):
        self.cur.fetchone.return_value = {'migration_key': 'recovery'}
        self.ns['restore_linear_news_publications_once']()
        sql = self.cur.execute.call_args_list[-1].args[0]
        self.assertIn("identifier='BS-1321'", sql)
        self.assertIn('first_published_at=COALESCE(first_published_at, now())', sql)
        self.cur.reset_mock()
        self.cur.fetchone.return_value = None
        self.ns['restore_linear_news_publications_once']()
        self.assertEqual(self.cur.execute.call_count, 2)


if __name__ == '__main__':
    unittest.main()
