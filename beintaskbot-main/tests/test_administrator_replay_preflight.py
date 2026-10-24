"""Owner MCP preflight uses the existing pilot guards and never calls a model."""
from unittest.mock import Mock
import pytest
from test_historical_replay import ready
from test_tenant_support_inbox import database,db,A,CA,AGENT
import tenant_ai_learning_mcp as mcp
import tenant_ai_historical_replay as replay


def test_owner_mcp_preflight_requires_exact_scope_and_is_read_only(ready,monkeypatch):
    database,ids=ready
    provider=Mock(side_effect=AssertionError('No paid boundary in preflight'))
    monkeypatch.setattr(replay,'generate',provider)
    cfg={'AI_LEARNING_MCP_TENANT_ID':A,'AI_LEARNING_MCP_AGENT_ID':AGENT,'AI_LEARNING_MCP_CHANNEL_ID':CA,'actor_id':11,'granted_scopes':[mcp.TOOL_SCOPE]}
    with database.cursor() as cur:
        result=mcp._tools(cur,cfg,'administrator_historical_replay_preflight',{'message_ids':ids})
        assert result['cap_microusd']==5_000_000 and len(result['messages'])==5
        with pytest.raises(Exception): mcp._tools(cur,{**cfg,'granted_scopes':[]},'administrator_historical_replay_preflight',{'message_ids':ids})
        with pytest.raises(Exception): mcp._tools(cur,{**cfg,'AI_LEARNING_MCP_CHANNEL_ID':A},'administrator_historical_replay_preflight',{'message_ids':ids})
    assert not provider.called
    assert database.execute('SELECT count(*) AS n FROM saas_ai_spend_reservations')[0]['n']==0
