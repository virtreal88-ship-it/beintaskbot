import unittest
import ast
import asyncio
import os
from pathlib import Path
import tomllib
from types import SimpleNamespace
from unittest.mock import patch

from tenant_ai_agent_startup import _prepare_database_with_retry,prepare_database,start_worker


class _Deadlock(Exception):
    sqlstate = "40P01"


class _InvalidMigration(Exception):
    sqlstate = "42601"


class _LockTimeout(Exception):
    sqlstate = "55P03"


class _TransactionTimeout(Exception):
    sqlstate = "25P04"


class TenantAiAgentStartupTests(unittest.TestCase):
    def _bootstrap_modules(self,events,*,fail=False,server_version=180006,setup_error=None):
        class Cursor:
            def __enter__(self):return self
            def __exit__(self,*_args):pass
            def execute(self,statement,params=None):
                if setup_error:raise setup_error
                if 'pg_advisory_unlock(' in statement:events.append('unlock')
                elif 'pg_advisory_lock(' in statement:events.append('lock')
                else:events.append(statement)
        class Connection:
            info=SimpleNamespace(server_version=server_version)
            def cursor(self):return Cursor()
            def commit(self):events.append('commit')
            def rollback(self):raise AssertionError('Cleanup must not send SQL on a dead session')
            def close(self):events.append('close')
        def migrate(name):
            def run(conn):
                events.append(name)
                if name=='chat' and fail:raise _InvalidMigration()
                conn.commit()  # Model nested initializers' real commit boundary.
            return run
        modules={'tenant_platform_core':SimpleNamespace(_connect=Connection,_fernet=lambda:None),
          'tenant_ai_agents_schema':SimpleNamespace(ensure_schema=migrate('core')),
          'tenant_connect_health':SimpleNamespace(prepare_database=migrate('connect_health')),
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
        self.assertLess(events.index('verified'),events.index('close'))
        self.assertGreater(events.count('commit'),3)
        self.assertEqual(events.count('close'),1)
        self.assertEqual(events[:3],["SET transaction_timeout='5000ms'","SET lock_timeout='2000ms'",'lock'])
        self.assertNotIn('unlock',events)

    def test_bootstrap_failure_closes_session_without_masking_original_error(self):
        events=[]
        with patch.dict('sys.modules',self._bootstrap_modules(events,fail=True)):
            with self.assertRaises(_InvalidMigration):prepare_database()
        self.assertEqual(events[-2:],['chat','close'])
        self.assertNotIn('verified',events)
        self.assertNotIn('simulation',events)

    def test_pre_17_server_fails_closed_before_any_sql(self):
        events=[]
        with patch.dict('sys.modules',self._bootstrap_modules(events,server_version=160006)):
            with self.assertRaisesRegex(RuntimeError,'PostgreSQL 17 or newer'):prepare_database()
        self.assertEqual(events,['close'])

    def test_version_17_supports_transaction_budget(self):
        events=[]
        with patch.dict('sys.modules',self._bootstrap_modules(events,server_version=170000)):
            prepare_database()
        self.assertIn('verified',events)
        self.assertEqual(events[-1],'close')

    def test_setup_error_closes_connection_and_is_not_masked(self):
        events=[];error=_InvalidMigration()
        with patch.dict('sys.modules',self._bootstrap_modules(events,setup_error=error)):
            with self.assertRaises(_InvalidMigration) as caught:prepare_database()
        self.assertIs(caught.exception,error)
        self.assertEqual(events,['close'])

    def test_transaction_timeout_retries_on_fresh_connection_after_nested_commit(self):
        events=[];modules=self._bootstrap_modules(events);connections=[]
        factory=modules['tenant_platform_core']._connect
        def connect():
            connection=factory();connections.append(connection);return connection
        modules['tenant_platform_core']._connect=connect
        attempts=[]
        def chat(conn):
            attempts.append(conn)
            if len(attempts)==1:raise _TransactionTimeout()
        modules['tenant_chat_ai_schema'].ensure_schema=chat
        with patch.dict('sys.modules',modules):
            _prepare_database_with_retry(prepare_database,delay_seconds=0)
        self.assertEqual(len(connections),2)
        self.assertIsNot(connections[0],connections[1])
        self.assertEqual(events.count('close'),2)
        self.assertEqual(events.count('verified'),1)
        self.assertLess(events.index('commit'),events.index('close'))
        self.assertLess(events.index('close'),events.index('verified'))

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
        logger=SimpleNamespace(info=lambda message,*args:events.append(('log',message,args)))
        namespace={'start_http_thread':lambda *_args:events.append('http'),'start_webhook_server':None,'logger':logger}
        exec(compile(program,'bot-main-startup-prefix','exec'),namespace)
        import tenant_ai_agent_startup as startup
        with patch.object(startup,'prepare_database',side_effect=_InvalidMigration):
            with self.assertRaises(_InvalidMigration):namespace['main']()
        self.assertEqual(events,[])
        for environment,enabled in (({},False),({'TENANT_SCOPED_ACCESS_ENABLED':'true'},True)):
            with self.subTest(enabled=enabled),patch.dict('os.environ',environment,clear=True):
                events.clear()
                with patch.object(startup,'prepare_database',side_effect=lambda:events.append('prepared')):namespace['main']()
                self.assertEqual(events,['prepared',('log','Scoped access rollout enabled=%s',(enabled,)),'http'])
                self.assertIs(type(events[1][2][0]),bool)

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

    def test_transaction_budget_exhaustion_never_starts_worker(self):
        app={};attempts=[];events=[];error=_TransactionTimeout()
        def prepare():attempts.append(True);raise error
        async def worker(*_args):events.append('worker')
        with patch('tenant_ai_agent_startup.time.sleep') as sleep:
            with self.assertRaises(_TransactionTimeout) as caught:
                asyncio.run(start_worker(app,worker,None,None,None,prepare_database_first=True,
                    prepare=prepare,require_ready=lambda:events.append('ready')))
        self.assertIs(caught.exception,error)
        self.assertEqual(len(attempts),5)
        self.assertEqual([call.args[0] for call in sleep.call_args_list],[1,2,3,4])
        self.assertEqual(app,{})
        self.assertEqual(events,[])

    def test_unknown_connection_failures_are_not_retried_as_transaction_timeouts(self):
        for state in (None,'08006','08007','25P03','57014','57P01','42501'):
            error=Exception('not a proven transaction timeout');error.sqlstate=state
            with self.subTest(sqlstate=state),patch('tenant_ai_agent_startup.time.sleep') as sleep:
                def prepare():raise error
                with self.assertRaises(Exception) as caught:_prepare_database_with_retry(prepare)
                self.assertIs(caught.exception,error)
                sleep.assert_not_called()


class RailwayStartupReadinessTests(unittest.TestCase):
    root=Path(__file__).resolve().parents[1]

    def _source(self):
        return ast.parse((self.root/'bot.py').read_text(encoding='utf-8-sig'))

    def _runtime_config(self):
        tree=ast.parse((self.root/'legacy_runtime_config.py').read_text(encoding='utf-8-sig'))
        names={'WEBHOOK_PORT','LEGACY_RAILWAY_HOST','CANONICAL_WEB_ORIGIN'}
        statements=[node for node in tree.body if isinstance(node,ast.Assign)
            and any(isinstance(target,ast.Name) and target.id in names for target in node.targets)]
        namespace={'os':os}
        exec(compile(ast.Module(body=statements,type_ignores=[]),'readiness-runtime-config','exec'),namespace)
        return namespace

    def test_root_railway_config_gates_traffic_without_changing_runtime_settings(self):
        config=tomllib.loads((self.root.parent/'railway.toml').read_text())
        self.assertEqual(config,{'build':{'builder':'nixpacks'},'deploy':{
            'startCommand':'bash start.sh','restartPolicyType':'always',
            'healthcheckPath':'/health','healthcheckTimeout':300}})
        self.assertIs(type(config['deploy']['healthcheckTimeout']),int)
        self.assertIn('exec python3 bot.py',(self.root.parent/'start.sh').read_text())

    def test_health_route_accepts_railway_probe_without_auth_or_redirect(self):
        from aiohttp import web
        from aiohttp.test_utils import make_mocked_request
        tree=self._source()
        names={'health_check','cors_middleware','canonical_web_origin_middleware','telegram_auth_middleware'}
        definitions=[node for node in tree.body if isinstance(node,ast.AsyncFunctionDef) and node.name in names]
        self.assertEqual({node.name for node in definitions},names)
        namespace=self._runtime_config()
        namespace.update(web=web,_WA_LAST_HOOK={},_ALLOWED_WEB_ORIGINS=set())
        exec(compile(ast.Module(body=definitions,type_ignores=[]),'readiness-health-handler','exec'),namespace)
        # Evaluate the actual registration, without importing bot or providers.
        routes=[node for node in ast.walk(tree) if isinstance(node,ast.Call)
            and isinstance(node.func,ast.Attribute) and node.func.attr=='add_get'
            and node.args and isinstance(node.args[0],ast.Constant) and node.args[0].value=='/health']
        self.assertEqual(len(routes),1)
        self.assertIsInstance(routes[0].args[1],ast.Name)
        self.assertEqual(routes[0].args[1].id,'health_check')

        async def dispatch(request):
            async def authenticated(req):
                return await namespace['telegram_auth_middleware'](req,namespace['health_check'])
            async def canonical(req):
                return await namespace['canonical_web_origin_middleware'](req,authenticated)
            return await namespace['cors_middleware'](request,canonical)

        for method in ('GET','HEAD'):
            with self.subTest(method=method):
                request=make_mocked_request(method,'/health',headers={'Host':'healthcheck.railway.app'})
                response=asyncio.run(dispatch(request))
                self.assertEqual(response.status,200)
                self.assertNotIn('Location',response.headers)
                self.assertFalse(response.cookies)
                self.assertIn('Bot is running',response.text)
        # The new check does not remove the established public-host redirect.
        request=make_mocked_request('GET','/health',headers={'Host':namespace['LEGACY_RAILWAY_HOST']})
        with self.assertRaises(web.HTTPPermanentRedirect):asyncio.run(dispatch(request))

    def test_listener_cannot_bind_before_committed_schema_readiness(self):
        server=next(node for node in self._source().body
            if isinstance(node,ast.AsyncFunctionDef) and node.name=='start_webhook_server')
        bind=next(index for index,node in enumerate(server.body) if isinstance(node,ast.Expr)
            and isinstance(node.value,ast.Await) and isinstance(node.value.value,ast.Call)
            and isinstance(node.value.value.func,ast.Attribute) and node.value.value.func.attr=='start'
            and isinstance(node.value.value.func.value,ast.Name) and node.value.value.func.value.id=='site')
        prefix=ast.AsyncFunctionDef(name=server.name,args=server.args,body=server.body[:bind+1],decorator_list=[])
        program=ast.fix_missing_locations(ast.Module(body=[prefix],type_ignores=[]))
        events=[]
        async def setup():events.append('setup')
        async def listen():events.append('listen')
        def application(**kwargs):events.append('application');return object()
        def runner(app, **kwargs):
            events.append(('runner', kwargs.get('shutdown_timeout')))
            return SimpleNamespace(setup=setup)
        def site(runner,host,port):events.append(('site',host,port));return SimpleNamespace(start=listen)
        namespace={'asyncio':asyncio,'web':SimpleNamespace(Application=application,AppRunner=runner,TCPSite=site),
            'cors_middleware':None,'canonical_web_origin_middleware':None,'telegram_auth_middleware':None,
            '_register_legacy_web_routes':lambda app:None,'_register_tenant_web_routes':lambda app:None,
            'WEBHOOK_PORT':49157}
        exec(compile(program,'readiness-http-bootstrap','exec'),namespace)
        with patch('tenant_ai_agent_startup.require_database_ready',side_effect=_InvalidMigration):
            with self.assertRaises(_InvalidMigration):asyncio.run(namespace['start_webhook_server']())
        self.assertEqual(events,[])
        with patch('tenant_ai_agent_startup.require_database_ready',side_effect=lambda:events.append('ready')):
            asyncio.run(namespace['start_webhook_server']())
        self.assertEqual(events,['ready','application',('runner',30),'setup',('site','0.0.0.0',49157),'listen'])

    def test_listener_port_uses_railway_port_with_existing_default(self):
        for environment,expected in (({},8080),({'PORT':'49157'},49157)):
            with self.subTest(environment=environment),patch.dict(os.environ,environment,clear=True):
                self.assertEqual(self._runtime_config()['WEBHOOK_PORT'],expected)


if __name__ == "__main__":
    unittest.main()
