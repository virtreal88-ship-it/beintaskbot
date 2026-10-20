from datetime import datetime, timedelta, timezone

import pytest

import tenant_knowledge_pilot_admin as pilot
from tenant_knowledge_pilot_admin import _validate_scope, handler, readiness
from tenant_linear_policy import TenantLinearError


def scope(**overrides):
    value={'tenant_id':'11111111-1111-4111-8111-111111111111',
           'channel_id':'22222222-2222-4222-8222-222222222222',
           'conversation_id':'15551234567@s.whatsapp.net',
           'start_after':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()}
    value.update(overrides)
    return value


def test_requires_exact_ids_and_future_timezone_boundary():
    assert _validate_scope(scope())[2]=='15551234567@s.whatsapp.net'
    for bad in (scope(tenant_id='bad'),scope(channel_id='bad'),scope(conversation_id=''),
                scope(start_after='2020-01-01T00:00:00Z'),scope(start_after='2030-01-01T00:00:00'),
                {**scope(),'extra':'unexpected'}):
        with pytest.raises(TenantLinearError): _validate_scope(bad)


class Request:
    method='GET';headers={};content_type='application/json'
    async def json(self): return {}


class Logger:
    def exception(self,*_args): pass


class FakeCursor:
    def __init__(self, parent): self.parent=parent; self.sql=''; self.params=()
    def __enter__(self): return self
    def __exit__(self,*_args): return False
    def execute(self,sql,params=()): self.sql=sql;self.params=params;self.parent.queries.append((sql,params))
    def fetchone(self):
        q,p=self.sql,self.params
        if 'FROM saas_support_case_pilot_plan' in q:
            return {'tenant_id':A,'channel_id':CA,'conversation_id':'c1','start_after':datetime.now(timezone.utc)+timedelta(days=1),'updated_at':datetime.now(timezone.utc)}
        if 'FROM saas_connect_conversations' in q:return {'exists':1}
        if 'FROM saas_tenant_integrations' in q:return {'metadata':{'daily_limit':25}}
        if "to_regclass('saas_knowledge_live_queue')" in q:return {'table_name':'saas_knowledge_live_queue'}
        if 'FROM saas_knowledge_live_queue' in q:return {'pending':2,'failed':1}
        if "to_regclass('saas_knowledge_extractions')" in q:return {'table_name':None}
        if "to_regclass('saas_ai_agent_token_usage')" in q:return {'table_name':None}
        if 'INSERT INTO saas_support_case_pilot_plan' in q:return None
        raise AssertionError(q)


class ForeignRouteCursor(FakeCursor):
    def fetchone(self):
        if 'FROM saas_connect_conversations' in self.sql:return None
        return super().fetchone()


class FakeConnection:
    def __init__(self):self.queries=[]
    def __enter__(self):return self
    def __exit__(self,*_args):return False
    def cursor(self):return FakeCursor(self)
    def commit(self):pass


class ForeignRouteConnection(FakeConnection):
    def cursor(self):return ForeignRouteCursor(self)


A='11111111-1111-4111-8111-111111111111'; CA='22222222-2222-4222-8222-222222222222'


class PostRequest(Request):
    method='POST';headers={'Origin':'https://example.test'}
    async def json(self):return scope(tenant_id=A,channel_id=CA,conversation_id='c1')


@pytest.mark.asyncio
async def test_tenant_owner_cannot_access_platform_pilot():
    response=await handler(lambda _r:{'telegram_id':7,'tenant_id':'tenant'},lambda _p:False,'https://example.test',Logger())(Request())
    assert response.status==403


@pytest.mark.asyncio
async def test_admin_scope_preparation_only_reads_exact_queue_and_keeps_switch_unknown(monkeypatch):
    conn=FakeConnection();monkeypatch.setattr(pilot,'_connect',lambda:conn)
    response=await handler(lambda _r:{'telegram_id':9},lambda _p:True,'https://example.test',Logger())(PostRequest())
    assert response.status==200
    payload=response.body.decode()
    assert 'not_confirmed' in payload and '"launch_enabled": false' in payload
    queue_queries=[(q,p) for q,p in conn.queries if 'FROM saas_knowledge_live_queue' in q]
    assert len(queue_queries)==1
    assert queue_queries[0][1][-3:]==(A,CA,'c1')
    assert not any('UPDATE saas_knowledge_live_queue' in q for q,_ in conn.queries)


@pytest.mark.asyncio
async def test_platform_admin_cannot_prepare_mismatched_tenant_channel_conversation(monkeypatch):
    conn=ForeignRouteConnection();monkeypatch.setattr(pilot,'_connect',lambda:conn)
    response=await handler(lambda _r:{'telegram_id':9},lambda _p:True,'https://example.test',Logger())(PostRequest())
    assert response.status==400
    assert not any('INSERT INTO saas_support_case_pilot_plan' in q for q,_ in conn.queries)


def test_preparation_is_separate_from_runtime_flag_and_queue():
    import inspect
    import tenant_knowledge_pilot_admin as module
    source=inspect.getsource(module)
    assert 'os.environ' not in source
    assert 'SUPPORT_CASES_LIVE_EXTRACTION_ENABLED' in source  # explanatory status only; never read or written
    assert 'UPDATE saas_knowledge_live_queue' not in source
    assert 'analyze(' not in source
    assert "q.tenant_id=%s::uuid AND q.channel_id=%s::uuid AND q.conversation_id=%s" in source
    assert 'INSERT INTO saas_support_case_pilot_plan' in source
    assert "'global_switch':'not_confirmed'" in source
    assert 'launch_enabled\':False' in source


def test_preflight_fails_closed_for_unknown_switch_and_old_backlog():
    limits={'pilot_caps_enforced':True,'cost_cap':1}
    result=readiness(scope(),route_verified=True,start_future=True,
      queue={'running':0,'eligible_pending':0,'invalid_source':0,'old_pending':4},
      queue_verified=True,global_switch='not_confirmed',limits=limits)
    assert result['status']=='not_ready' and any('təsdiqlənməyib' in x.lower() for x in result['reasons'])
    assert any('köhnə sərhədöncəsi' in x.lower() for x in result['reasons'])
    assert result['launch_enabled'] is False
    safe=readiness(scope(),route_verified=True,start_future=True,
      queue={'running':0,'eligible_pending':0,'invalid_source':0,'old_pending':0},
      queue_verified=True,global_switch='off',limits=limits)
    assert safe['status']=='ready_to_configure'
