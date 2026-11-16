"""Focused identity guard tests; native PG races are tested separately."""
from pathlib import Path
import ast
from unittest.mock import MagicMock, patch
import pytest
import tenant_connect_dialog_equivalence as eq
from tenant_linear_policy import TenantLinearError
from tenant_dialog_rollout import RolloutState

T='00000000-0000-0000-0000-000000000001'
C='00000000-0000-0000-0000-000000000002'

@pytest.mark.parametrize('responses', [
    [{'present':True}, {'exists':1}],
    [{'present':True}, None, {'present':True}, {'exists':1}],
])
def test_any_workflow_or_historical_episode_blocks_exact_component(responses):
    cur=MagicMock(); cur.fetchone.side_effect=responses
    with patch.object(eq,'lifecycle_routes',return_value=['a','alias','b']):
        with pytest.raises(TenantLinearError):
            eq.assert_lifecycle_unaffected(cur,T,C,['a','b'])
    queries=[args[0] for args,_ in cur.execute.call_args_list]
    assert all('state=' not in q for q in queries)
    assert cur.execute.call_args.args[1] == (T,C,['a','alias','b'])

def test_unaffected_pair_allowed_not_tenant_ban():
    cur=MagicMock(); cur.fetchone.side_effect=[{'present':True},None,{'present':True},None]
    with patch.object(eq,'lifecycle_routes',return_value=['a','b']):
        assert eq.assert_lifecycle_unaffected(cur,T,C,['a','b']) == ['a','b']
    for args,_ in cur.execute.call_args_list:
        if 'LIMIT 1' in args[0]:
            assert 'channel_id=%s::uuid' in args[0]
            assert 'conversation_id=ANY' in args[0]
            assert args[1] == (T,C,['a','b'])

def test_missing_lifecycle_schema_active_fails_closed():
    cur=MagicMock();cur.fetchone.return_value={'present':None}
    with patch.object(eq,'lifecycle_routes',return_value=['a']), patch('tenant_dialog_rollout.guard',return_value=RolloutState('active',True,True,1)):
        with pytest.raises(TenantLinearError):eq.assert_lifecycle_unaffected(cur,T,C,['a'])

def test_lock_checks_changed_component_after_routes_locked():
    cur=MagicMock();cur.fetchall.return_value=[]
    with patch.object(eq,'lifecycle_routes',side_effect=[['a','b'],['a','b','c']]), patch('tenant_ai_dialog_scope.lock_dialog') as lock, patch.object(eq,'assert_lifecycle_unaffected') as check:
        with pytest.raises(TenantLinearError):eq.lock(cur,T,C,['a','b'])
        assert [call.args[-1] for call in lock.call_args_list]==['a','b']
        check.assert_not_called()

def test_lock_assertion_runs_after_all_routes():
    cur=MagicMock();cur.fetchall.return_value=[];seen=[]
    with patch.object(eq,'lifecycle_routes',return_value=['a','alias','b']), patch('tenant_ai_dialog_scope.lock_dialog',side_effect=lambda *a:seen.append(a[-1])), patch.object(eq,'assert_lifecycle_unaffected',side_effect=lambda *a:seen.append('check')):
        assert eq.lock(cur,T,C,['a','b'])==['a','alias','b']
    assert seen==['a','alias','b','check']

def test_defensive_activate_rejects_before_any_mutation():
    cur=MagicMock()
    with patch.object(eq,'assert_lifecycle_unaffected',side_effect=TenantLinearError('blocked',409)):
        with pytest.raises(TenantLinearError):eq.activate(cur,T,C,'pair',['a','b'],[1,2],{})
    cur.execute.assert_not_called()

@pytest.mark.parametrize('filename',['tenant_ai_dialogue_merge.py','tenant_connect_owner_identity.py'])
def test_public_writers_take_barrier_before_owner(filename):
    tree=ast.parse((Path(__file__).parents[1]/filename).read_text())
    execute=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='execute')
    calls=[n for n in ast.walk(execute) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)]
    first=lambda name:min(n.lineno for n in calls if n.func.id==name)
    assert first('rollout_guard') < first('_owner')

def test_rollback_fences_stored_pair_before_update_lock():
    cur=MagicMock();cur.fetchone.return_value={'source_id':'a','canonical_id':'b'}
    with patch.object(eq,'lock',side_effect=TenantLinearError('blocked',409)) as lock:
        with pytest.raises(TenantLinearError):eq.rollback(cur,T,C,'pair')
    lock.assert_called_once_with(cur,T,C,['b','a'])
    assert cur.execute.call_count==1
    assert 'FOR UPDATE' not in cur.execute.call_args.args[0]
