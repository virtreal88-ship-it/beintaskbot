"""Regression coverage for the MCP replay dispatch boundary.

This test is intentionally database-free.  PGlite has one connection and
cannot model PostgreSQL's AccessExclusive lock behavior; the assertion here is
structural: the read-only replay path must receive no outer cursor and must not
run schema DDL in the request transaction.
"""
import asyncio
import time

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import tenant_ai_learning_mcp as mcp


def test_replay_read_dispatch_has_no_outer_schema_lock_and_keeps_health_responsive(monkeypatch):
    cfg={
        'AI_LEARNING_MCP_TENANT_ID':'00000000-0000-0000-0000-000000000101',
        'AI_LEARNING_MCP_AGENT_ID':'00000000-0000-0000-0000-000000000301',
        'AI_LEARNING_MCP_CHANNEL_ID':'00000000-0000-0000-0000-000000000201',
        'actor_id':11,
        'granted_scopes':[mcp.TOOL_SCOPE],
    }
    monkeypatch.setattr(mcp,'_authorize_token',lambda request: cfg)
    # Schema setup belongs to the authenticated bootstrap transaction, not to
    # the replay dispatch transaction which opens its own read connection.
    monkeypatch.setattr(
        mcp,'ensure_mcp_schema',
        lambda conn: (_ for _ in ()).throw(AssertionError('outer schema DDL')),
    )
    observed={}

    def replay_tool(cur, received_cfg, name, args):
        observed.update(cur=cur,cfg=received_cfg,name=name,args=args)
        time.sleep(.15)  # representative synchronous database read
        return {'items':[],'selected_message_ids':[],'read_only':True}

    monkeypatch.setattr(mcp,'_tools',replay_tool)

    async def check():
        app=web.Application(); app.router.add_post('/mcp',mcp.mcp)

        async def health(_request):
            return web.Response(text='ok')

        app.router.add_get('/health',health)
        async with TestClient(TestServer(app)) as client:
            replay_task=asyncio.create_task(client.post('/mcp',json={
                'jsonrpc':'2.0','id':1,'method':'tools/call',
                'params':{'name':'administrator_historical_replay_candidates','arguments':{}},
            }))
            await asyncio.sleep(.02)
            assert not replay_task.done()
            health_response=await client.get('/health')
            replay_response=await replay_task
            assert health_response.status==200 and replay_response.status==200

    asyncio.run(check())
    assert observed['cur'] is None and observed['cfg'] == cfg
    assert observed['name']=='administrator_historical_replay_candidates'
    assert observed['args']=={}
