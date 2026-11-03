"""History-only test against a disposable, randomly scoped local PG fixture."""
from pathlib import Path
from unittest.mock import patch
from psycopg.errors import UndefinedColumn
import tenant_chat_ai_schema as chat_schema
from test_rolling_history_pg import RollingHistory


class Review(RollingHistory):
    def test_existing_schema_bootstrap_does_not_expand_before_new_read(self):
        # Match old deployed shape; all schema changes are inside the random
        # fixture schema in this disposable localhost database.
        with self.scoped() as conn:
            conn.execute('ALTER TABLE saas_operator_copilot_threads DROP COLUMN clear_revision')
            conn.execute("ALTER TABLE saas_chat_ai_runs ADD COLUMN usage_day date DEFAULT CURRENT_DATE")
        try:
            with patch.object(chat_schema,'_ensure_schema'),patch.object(chat_schema,'_ready',False):
                with self.scoped() as conn:chat_schema.ensure_schema(conn)
            with self.scoped() as conn:
                has_column=conn.execute("SELECT EXISTS(SELECT 1 FROM pg_attribute WHERE attrelid='saas_operator_copilot_threads'::regclass AND attname='clear_revision' AND NOT attisdropped) AS present").fetchone()['present']
            self.assertFalse(has_column)
            with self.assertRaises(UndefinedColumn):self.snapshot(self.command())
            sql=Path('experiments/copilot_realtime_adapter/rolling_history_expand.sql').read_text()
            with self.scoped() as conn:
                conn.commit();conn.execute(sql)
            command=self.command();self.assertEqual(self.snapshot(command),[])
            self.assertEqual(command['_thread_clear_revision'],0)
        finally:
            with self.scoped() as conn:
                conn.execute('DROP TABLE IF EXISTS saas_chat_transcripts')
                conn.execute('ALTER TABLE saas_operator_copilot_threads ADD COLUMN IF NOT EXISTS clear_revision bigint NOT NULL DEFAULT 0')
