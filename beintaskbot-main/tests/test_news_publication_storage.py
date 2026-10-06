"""Preserve published news when migrating the review queue."""
import ast
from pathlib import Path
import unittest
from unittest.mock import MagicMock


class NewsPublicationStorageTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'tenant_platform.py').read_text(encoding='utf-8-sig'))
        names = {'list_linear_news', 'require_linear_news_review_once', 'restore_linear_news_publications_once'}
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        self.conn = MagicMock()
        self.cur = self.conn.cursor.return_value.__enter__.return_value
        self.cur.fetchall.return_value = []
        connect = MagicMock()
        connect.return_value.__enter__.return_value = self.conn
        self.ns = {'_connect': connect, '_ensure_schema': MagicMock()}
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
