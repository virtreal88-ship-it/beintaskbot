"""Tenant rollout gates must not disable unrelated tenants or race dispatch."""
import ast
from pathlib import Path
from unittest.mock import MagicMock, patch
import tenant_knowledge_live_queue as live
import tenant_ai_agent_connect as ai
import tenant_ai_group_coordination as group
import tenant_connect_transport as transport

T='00000000-0000-0000-0000-000000000001'
C='00000000-0000-0000-0000-000000000002'

def test_global_worker_switch_independent_of_one_tenant(monkeypatch):
    monkeypatch.setenv('SUPPORT_CASES_LIVE_EXTRACTION_ENABLED','true')
    for key in live.SCOPE_ENV.values(): monkeypatch.delenv(key, raising=False)
    with patch('tenant_dialog_lifecycle.enabled',return_value=True) as tenant_gate:
        assert live.extraction_enabled() is True
        tenant_gate.assert_not_called()

def test_active_tenant_blocks_automatic_case_before_reads():
    cur=MagicMock()
    with patch('tenant_dialog_lifecycle.enabled',return_value=True) as enabled:
        assert live._configured(cur,T,C) is None
        enabled.assert_called_once_with(cur,T)
    cur.execute.assert_not_called()

def test_shadow_does_not_disable_legacy_case_configuration():
    cur=MagicMock()
    with patch('tenant_connect_identity.conflicted',return_value=False), patch('tenant_dialog_lifecycle.enabled',return_value=False), patch.object(live,'extraction_enabled',return_value=True), patch.object(live,'pilot_scope',return_value={'tenant_id':T,'channel_id':C,'conversation_id':'client','start_after':'2026-01-01T00:00:00+00:00'}):
        assert live._configured(cur,T,C,conversation='client') is not None

def test_group_barrier_precedes_group_lock():
    cur=MagicMock(); order=[]
    cur.execute.side_effect=lambda *a:order.append('group')
    with patch('tenant_dialog_rollout.guard',side_effect=lambda *a,**k:order.append('tenant')):
        group.lock_group(cur,T,'12000000001@g.us')
    assert order==['tenant','group']

def test_ai_admission_barrier_precedes_route_lock():
    conn=MagicMock(); cur=conn.cursor.return_value.__enter__.return_value; order=[]
    with patch('tenant_dialog_rollout.guard',side_effect=lambda *a,**k:order.append('tenant')), patch.object(ai,'lock_dialog',side_effect=lambda *a:order.append('route')), patch('tenant_dialog_lifecycle.ai_blocked',return_value=True):
        assert ai.enqueue(T,channel_id=C,conversation_id='12000000001@g.us',lead_id=1,message_id='m',event_id='e',text='',connection=conn)==0
    assert order==['tenant','route']

def test_no_process_global_lifecycle_checks_in_background_files():
    for module in (live,ai,group,transport):
        tree=ast.parse(Path(module.__file__).read_text())
        for node in ast.walk(tree):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='lifecycle_enabled':
                assert len(node.args)==2, (module.__name__,node.lineno)

def test_live_analysis_keeps_fence_open_until_provider_returns():
    conn=MagicMock(); conn.__enter__.return_value=conn
    item={'tenant_id':T,'channel_id':C,'conversation_id':'client','lead_id':1,
          'reviewer_id':2,'last_message_id':'m','last_message_at':'2026-01-01T00:00:00+00:00'}
    def analyze(*args,**kwargs):
        conn.__exit__.assert_not_called()
        return {'request':{'status':'completed'}}
    with patch.object(live,'extraction_enabled',return_value=True), patch.object(live,'claim',return_value=item), patch.object(live,'require_extraction_enabled'), patch.object(live,'_connect',return_value=conn), patch.object(live,'_configured',return_value=True), patch.object(live,'analyze',side_effect=analyze) as provider, patch.object(live,'finish'):
        assert live.tick() is True
        provider.assert_called_once()
        conn.__exit__.assert_called_once()

def test_activation_winning_before_analysis_prevents_provider_call():
    conn=MagicMock(); conn.__enter__.return_value=conn
    item={'tenant_id':T,'channel_id':C,'conversation_id':'client','lead_id':1,
          'reviewer_id':2,'last_message_id':'m','last_message_at':'2026-01-01T00:00:00+00:00'}
    with patch.object(live,'extraction_enabled',return_value=True), patch.object(live,'claim',return_value=item), patch.object(live,'require_extraction_enabled'), patch.object(live,'_connect',return_value=conn), patch.object(live,'_configured',return_value=None), patch.object(live,'analyze') as provider, patch.object(live,'finish'):
        assert live.tick() is False
        provider.assert_not_called()
