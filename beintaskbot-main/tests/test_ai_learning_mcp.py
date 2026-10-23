"""Regression coverage for the externally consumable, read-only Learning MCP."""
import asyncio
import hashlib
import logging
import os
import uuid

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from test_tenant_support_inbox import A, CA, PROFILE, database, db, seed_simulation_run
import tenant_ai_learning_mcp as mcp


AGENT='67da1058-2ffb-48fa-bfd9-d3d01c1990d1'


def test_mcp_is_fail_closed_without_explicit_railway_configuration(monkeypatch):
    for key in ('AI_LEARNING_MCP_ENABLED','AI_LEARNING_MCP_ISSUER','AI_LEARNING_MCP_TENANT_ID',
                'AI_LEARNING_MCP_AGENT_ID','AI_LEARNING_MCP_CHANNEL_ID','AI_LEARNING_MCP_CURSOR_SECRET'):
        monkeypatch.delenv(key, raising=False)
    import pytest
    with pytest.raises(Exception):
        mcp._require_cfg()


def test_cursor_is_opaque_signed_and_context_redaction(monkeypatch):
    monkeypatch.setenv('AI_LEARNING_MCP_ENABLED','true'); monkeypatch.setenv('AI_LEARNING_MCP_ISSUER','https://crm.test')
    monkeypatch.setenv('AI_LEARNING_MCP_TENANT_ID',A); monkeypatch.setenv('AI_LEARNING_MCP_AGENT_ID',AGENT)
    monkeypatch.setenv('AI_LEARNING_MCP_CHANNEL_ID',CA); monkeypatch.setenv('AI_LEARNING_MCP_CURSOR_SECRET','secret')
    cfg=mcp._require_cfg(); cursor=mcp._cursor(cfg,'2026-10-23T10:00:00+00:00',uuid.uuid4())
    assert mcp._parse_cursor(cfg,cursor)[0].startswith('2026-10-23')
    assert '[phone]' in mcp._redact('call +994501234567') and '[redacted]' in mcp._redact('api_key=private')


def _setup(monkeypatch, database):
    monkeypatch.setattr(mcp, '_connect', lambda: database)
    monkeypatch.setenv('AI_LEARNING_MCP_ENABLED','true')
    monkeypatch.setenv('AI_LEARNING_MCP_ISSUER','https://crm.test')
    monkeypatch.setenv('AI_LEARNING_MCP_TENANT_ID',A)
    monkeypatch.setenv('AI_LEARNING_MCP_AGENT_ID',AGENT)
    monkeypatch.setenv('AI_LEARNING_MCP_CHANNEL_ID',CA)
    monkeypatch.setenv('AI_LEARNING_MCP_CURSOR_SECRET','test-only-cursor-secret')
    mcp.ensure_mcp_schema(database)
    token='read-only-test-token'
    database.execute('''INSERT INTO saas_ai_learning_mcp_tokens(id,tenant_id,token_hash,agent_id,channel_id,client_id,expires_at,created_by)
      VALUES(%s::uuid,%s::uuid,%s,%s::uuid,%s::uuid,'https://chatgpt.com/oauth/client.json',now()+interval '1 hour',11)''',
      (str(uuid.uuid4()),A,hashlib.sha256(token.encode()).hexdigest(),AGENT,CA))
    database.commit()
    return token


def test_mcp_requires_token_and_returns_only_scoped_runs(db, monkeypatch):
    token=_setup(monkeypatch,db)
    run,_,_=seed_simulation_run(db,outcome='virtual_reply',events=[{'type':'provider_result_unknown'}])
    db.execute('UPDATE saas_ai_simulation_runs SET primary_agent_id=%s::uuid WHERE id=%s::uuid',(AGENT,run)); db.commit()
    async def check():
        app=web.Application(); app.router.add_post('/mcp',mcp.mcp)
        async with TestClient(TestServer(app)) as client:
            listed=await client.post('/mcp',json={'jsonrpc':'2.0','id':1,'method':'tools/list'})
            assert listed.status==200 and (await listed.json())['result']['tools'][0]['annotations']['readOnlyHint'] is True
            assert (await client.post('/mcp',json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'simulation_runs','arguments':{}}})).status==401
            response=await client.post('/mcp',headers={'Authorization':'Bearer '+token},json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'simulation_runs','arguments':{'limit':1}}})
            data=await response.json(); text=data['result']['content'][0]['text']
            assert response.status==200 and run in text and A not in text
            assert db.execute("SELECT count(*) AS n FROM saas_tenant_audit_events WHERE action='ai_learning_mcp_read'")[0]['n']==1
    asyncio.run(check())


def test_cursor_is_signed_and_rate_limit_is_enforced(db, monkeypatch):
    token=_setup(monkeypatch,db)
    cfg=mcp._require_cfg()
    with __import__('pytest').raises(Exception): mcp._parse_cursor(cfg,'not-a-cursor')
    db.execute('UPDATE saas_ai_learning_mcp_tokens SET requests_in_window=60 WHERE token_hash=%s',(hashlib.sha256(token.encode()).hexdigest(),)); db.commit()
    request=type('Request',(),{'headers':{'Authorization':'Bearer '+token}})()
    with __import__('pytest').raises(Exception): mcp._authorize_token(request)
