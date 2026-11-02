import unittest
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tenant_ai_agent_startup import _prepare_database_with_retry,prepare_database,start_worker


class _Deadlock(Exception):
    sqlstate = "40P01"


class _InvalidMigration(Exception):
    sqlstate = "42601"


class _LockTimeout(Exception):
    sqlstate = "55P03"


class TenantAiAgentStartupTests(unittest.TestCase):
    def _bootstrap_modules(self,events,*,fail=False):
        class Cursor:
            def __enter__(self):return self
            def __exit__(self,*_args):pass
            def execute(self,statement,params=None):
                if 'pg_advisory_unlock(' in statement:events.append('unlock')
                elif 'pg_advisory_lock(' in statement:events.append('lock')
        class Connection:
            def __enter__(self):return self
            def __exit__(self,*_args):pass
            def cursor(self):return Cursor()
            def commit(self):events.append('commit')
            def rollback(self):events.append('rollback')
        connection=Connection()
        def migrate(name):
            def run(conn):
                events.append(name)
                if name=='chat' and fail:raise _InvalidMigration()
                conn.commit()  # Model nested initializers' real commit boundary.
            return run
        modules={'tenant_platform_core':SimpleNamespace(_connect=lambda:connection,_fernet=lambda:None),
          'tenant_ai_agents_schema':SimpleNamespace(ensure_schema=migrate('core')),
          'tenant_dialog_consultations':SimpleNamespace(schema=migrate('unified'),require_schema=lambda _conn:events.append('verified'))}
        for module,name in [('tenant_chat_ai_schema','chat'),('tenant_ai_simulation_schema','simulation'),
          ('tenant_ai_administrator_drafts','drafts'),('tenant_ai_administrator_approvals','approvals'),
          ('tenant_ai_administrator_observations','observations'),('tenant_ai_administrator_apply','apply')]:
            modules[module]=SimpleNamespace(ensure_schema=migrate(name))
        return modules

    def test_full_bootstrap_holds_session_lock_across_nested_commits_and_verifies(self):
        events=[]
        with patch.dict('sys.modules',self._bootstrap_modules(events)):prepare_database()
        self.assertLess(events.index('lock'),events.index('core'))
        self.assertLess(events.index('unified'),events.index('chat'))
        self.assertLess(events.index('apply'),events.index('verified'))
        self.assertLess(events.index('verified'),events.index('unlock'))
        self.assertGreater(events.count('commit'),3)
        self.assertEqual(events.count('unlock'),1)

    def test_bootstrap_failure_rolls_back_and_releases_session_lock(self):
        events=[]
        with patch.dict('sys.modules',self._bootstrap_modules(events,fail=True)):
            with self.assertRaises(_InvalidMigration):prepare_database()
        self.assertEqual(events[-3:],['rollback','unlock','commit'])
        self.assertNotIn('verified',events)
        self.assertNotIn('simulation',events)

    def test_main_finishes_bootstrap_before_starting_http_or_building_scheduler(self):
        module=ast.parse((Path(__file__).resolve().parents[1]/'bot.py').read_text())
        main=next(node for node in module.body if isinstance(node,ast.FunctionDef) and node.name=='main')
        def position(name):
            return next(index for index,node in enumerate(main.body) if any(isinstance(child,ast.Call) and isinstance(child.func,ast.Name) and child.func.id==name for child in ast.walk(node)))
        migration,http,scheduler=(position(name) for name in ('_prepare_database_with_retry','start_http_thread','build_telegram_application'))
        self.assertLess(migration,http);self.assertLess(http,scheduler)
        # Execute the actual prefix: a synchronous migration failure must
        # escape before the HTTP-thread wrapper can swallow its own failures.
        prefix=ast.FunctionDef(name='main',args=main.args,body=main.body[:http+1],decorator_list=[])
        program=ast.fix_missing_locations(ast.Module(body=[prefix],type_ignores=[]));events=[]
        namespace={'start_http_thread':lambda *_args:events.append('http'),'start_webhook_server':None,'logger':None}
        exec(compile(program,'bot-main-startup-prefix','exec'),namespace)
        import tenant_ai_agent_startup as startup
        with patch.object(startup,'prepare_database',side_effect=_InvalidMigration):
            with self.assertRaises(_InvalidMigration):namespace['main']()
        self.assertEqual(events,[])
        with patch.object(startup,'prepare_database',side_effect=lambda:events.append('prepared')):namespace['main']()
        self.assertEqual(events,['prepared','http'])

    def test_direct_http_entry_checks_readiness_before_constructing_listener(self):
        module=ast.parse((Path(__file__).resolve().parents[1]/'bot.py').read_text())
        server=next(node for node in module.body if isinstance(node,ast.AsyncFunctionDef) and node.name=='start_webhook_server')
        calls=[node.func.id if isinstance(node.func,ast.Name) else node.func.attr for node in ast.walk(server.body[1]) if isinstance(node,ast.Call)]
        self.assertIn('to_thread',calls)
        self.assertTrue(any(isinstance(node,ast.Name) and node.id=='require_database_ready' for node in ast.walk(server.body[1])))
        self.assertIsInstance(server.body[2],ast.Assign)

    def test_worker_readiness_failure_creates_no_background_task(self):
        app={};events=[]
        async def worker(*_args):events.append('worker')
        def require_ready():raise _InvalidMigration()
        with self.assertRaises(_InvalidMigration):asyncio.run(start_worker(app,worker,None,None,None,require_ready=require_ready))
        self.assertEqual(app,{})
        self.assertEqual(events,[])

    def test_transient_postgres_deadlock_retries_and_recovers(self):
        calls = 0

        def prepare():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise _Deadlock()

        _prepare_database_with_retry(prepare, delay_seconds=0)
        self.assertEqual(calls, 2)

    def test_bounded_postgres_lock_timeout_retries_and_recovers(self):
        calls=[]
        def prepare():
            calls.append(True)
            if len(calls)==1:raise _LockTimeout()
        _prepare_database_with_retry(prepare,delay_seconds=0)
        self.assertEqual(len(calls),2)

    def test_non_transient_migration_error_is_not_retried(self):
        calls = 0

        def prepare():
            nonlocal calls
            calls += 1
            raise _InvalidMigration()

        with self.assertRaises(_InvalidMigration):
            _prepare_database_with_retry(prepare, delay_seconds=0)
        self.assertEqual(calls, 1)

    def test_repeated_deadlocks_eventually_surface(self):
        calls = 0

        def prepare():
            nonlocal calls
            calls += 1
            raise _Deadlock()

        with self.assertRaises(_Deadlock):
            _prepare_database_with_retry(prepare, attempts=3, delay_seconds=0)
        self.assertEqual(calls, 3)


if __name__ == "__main__":
    unittest.main()
