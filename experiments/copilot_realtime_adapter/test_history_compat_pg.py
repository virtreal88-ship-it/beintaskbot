"""Schema transition tests against a disposable localhost PostgreSQL only."""
from pathlib import Path
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from psycopg import sql
from psycopg.errors import LockNotAvailable,UndefinedColumn,InsufficientPrivilege
from tenant_linear_policy import TenantLinearError
import tenant_operator_copilot_threads as threads
from test_rolling_history_pg import RollingHistory


class SchemaCompatibility(RollingHistory):
    def test_catalog_detection_under_restricted_role_and_denied_table(self):
        # The application role can read a table without owning its schema.
        # PostgreSQL catalogs, rather than information_schema's filtered
        # view, supply the schema check. Missing permission must never be
        # interpreted as a missing revision column.
        role='voice_compat_'+uuid.uuid4().hex
        with self.scoped() as conn:
            schema=conn.execute('SELECT current_schema() AS s').fetchone()['s']
            conn.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
            conn.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
            conn.execute(sql.SQL('GRANT SELECT ON saas_operator_copilot_threads TO {}').format(sql.Identifier(role)))
            conn.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(role)))
            with conn.cursor() as cur:self.assertTrue(threads.revision_schema(cur))
            conn.execute('RESET ROLE')
            conn.execute('ALTER TABLE saas_operator_copilot_threads DROP COLUMN clear_revision')
            conn.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(role)))
            with conn.cursor() as cur:self.assertFalse(threads.revision_schema(cur))
            conn.execute('RESET ROLE')
            conn.execute(sql.SQL('REVOKE SELECT ON saas_operator_copilot_threads FROM {}').format(sql.Identifier(role)))
            conn.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(role)))
            with self.assertRaises(InsufficientPrivilege):
                with conn.cursor() as cur:threads.revision_schema(cur)
            # Roll back role/GRANT/ALTER even if the expected error aborted the
            # transaction; no cluster-global test role remains.
            conn.rollback()

    def drop_revision(self):
        with self.scoped() as conn:conn.execute('ALTER TABLE saas_operator_copilot_threads DROP COLUMN clear_revision')

    def expand(self):
        sql=Path(__file__).with_name('rolling_history_expand.sql').read_text()
        with self.scoped() as conn:conn.commit();conn.execute(sql)

    def test_pre_expansion_inflight_text_runs_original_writer(self):
        self.drop_revision();command=self.command();old=self.snapshot(command);self.make_run(command)
        self.assertEqual(command['_thread_schema_version'],0)
        result=self.finish(command,old)
        self.assertEqual(result['text'],'synthetic answer')
        with self.scoped() as conn:
            turns=conn.execute('SELECT turns FROM saas_operator_copilot_threads').fetchone()['turns']
        self.assertEqual([turn['text'] for turn in turns],['synthetic prompt','synthetic answer'])

    def test_old_schema_request_can_outlive_expansion_and_overwrite_clear(self):
        self.drop_revision();command=self.command();old=self.snapshot(command);self.make_run(command)
        self.expand()
        with self.scoped() as conn,conn.cursor() as cur:threads.clear(cur,self.tenant,7,self.channel,self.conversation)
        self.finish(command,old)
        # This is an intentional counterexample: additive DDL plus fallback
        # does not prove safe voice admission until every old request drains.
        row=self.read_thread()
        self.assertEqual(row['clear_revision'],1)
        self.assertEqual([turn['text'] for turn in row['turns']],['synthetic prompt','synthetic answer'])

    def test_schema_decision_holds_access_share_until_transaction_ends(self):
        self.drop_revision();reader=self.scoped()
        with reader.cursor() as cur:self.assertFalse(threads.revision_schema(cur))
        entered=threading.Event()
        def expand():
            with self.scoped() as conn:
                conn.execute("SET LOCAL lock_timeout='250ms'");entered.set()
                with self.assertRaises(LockNotAvailable):
                    conn.execute('ALTER TABLE saas_operator_copilot_threads ADD COLUMN clear_revision bigint NOT NULL DEFAULT 0')
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending=pool.submit(expand);self.assertTrue(entered.wait(2));pending.result(timeout=2)
        finally:reader.rollback();reader.close()
        self.expand()
        with self.scoped() as conn,conn.cursor() as cur:self.assertTrue(threads.revision_schema(cur))

    def test_rollback_during_new_inflight_request_fails_without_silent_write(self):
        command=self.command();old=self.snapshot(command);self.make_run(command)
        self.assertEqual(command['_thread_schema_version'],1)
        self.drop_revision()
        with self.assertRaises(UndefinedColumn):self.finish(command,old)
        with self.scoped() as conn:
            self.assertEqual(conn.execute('SELECT status FROM saas_chat_ai_runs WHERE request_id=%s',(command['request_id'],)).fetchone()['status'],'sending')
        # Rollback therefore needs a proven new-writer drain; no automatic
        # destructive migration or forced work termination is performed.

    def test_invalid_existing_column_does_not_silently_choose_legacy(self):
        self.drop_revision()
        with self.scoped() as conn:conn.execute("ALTER TABLE saas_operator_copilot_threads ADD COLUMN clear_revision text NOT NULL DEFAULT '0'")
        with self.scoped() as conn,conn.cursor() as cur:
            with self.assertRaises(TenantLinearError):threads.revision_schema(cur)
