"""Completion retries only after SQLSTATE 40P01 has rolled back completely."""
from unittest.mock import Mock
import uuid
import pytest
from psycopg.errors import DeadlockDetected, OperationalError, StatementCompletionUnknown
from test_dialog_completion_preview import active, fixture, operator_db, database, db, A, CA, PROFILE, message
from tenant_linear_policy import TenantLinearError
import tenant_dialog_lifecycle as lifecycle


@pytest.mark.parametrize('phase',['write','commit'])
@pytest.mark.parametrize('action',['dismiss','close'])
def test_sql_40p01_after_write_rolls_back_before_new_connection(active,monkeypatch,action,phase):
    import tenant_platform
    from test_dialog_lifecycle_pglite import payload
    incoming=message(active,20)
    lifecycle.mutate(PROFILE,payload('accept'))
    no_reply=action=='dismiss'
    request=payload(action,1,
        **({'message_id':incoming,'individual_no_reply':True} if no_reply else {}))
    action_name='dialog_no_reply' if no_reply else 'dialog_close'
    audit_sql="""SELECT id::text, payload FROM saas_tenant_audit_events
      WHERE tenant_id=%s::uuid AND action=%s"""
    receipt_sql="""SELECT actor_id,result FROM saas_dialog_lifecycle_receipts
      WHERE tenant_id=%s::uuid AND request_id=%s::uuid"""
    workflow_sql="""SELECT state,operator_id,closed_at,last_inbound_id FROM saas_dialog_workflows
      WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id='client'"""
    prior_workflow=active.query(workflow_sql,(A,CA))
    execute=active.execute
    failed=False
    def injected(sql,params=()):
        nonlocal failed
        result=execute(sql,params)
        # For no-reply, abort after the audit marker, the last write, so the
        # first attempt has exercised both the marker and the receipt.
        target=('INSERT INTO saas_tenant_audit_events' in sql if no_reply else
                "UPDATE saas_dialog_workflows SET state='closed'" in sql)
        if phase=='write' and target and not failed:
            failed=True
            # PostgreSQL itself reports 40P01 and aborts the transaction after
            # its write. This is not a simulated successful/unknown commit.
            execute("DO $$ BEGIN RAISE EXCEPTION 'synthetic deadlock' USING ERRCODE='40P01'; END $$")
        return result
    monkeypatch.setattr(active,'execute',injected)
    connections=[]
    class Session:
        def __enter__(self):return self
        def __exit__(self,kind,*rest):return active.__exit__(kind,*rest)
        def cursor(self):return active.cursor()
        def commit(self):
            nonlocal failed
            if phase=='commit' and not failed:
                failed=True
                execute("DO $$ BEGIN RAISE EXCEPTION 'synthetic commit-boundary deadlock' USING ERRCODE='40P01'; END $$")
            active.commit()
    def connect():
        assert active.transaction is False, 'previous transaction must be rolled back first'
        if len(connections)==1:
            assert not active.query(receipt_sql,(A,request['request_id']))
            assert not active.query(audit_sql,(A,action_name))
            assert active.query(workflow_sql,(A,CA))==prior_workflow
        session=Session();connections.append(session);return session
    monkeypatch.setattr(tenant_platform,'_connect',connect)
    monkeypatch.setattr(lifecycle.time,'sleep',Mock())
    result=lifecycle.mutate(PROFILE,request)
    assert failed and len(connections)==2 and connections[0] is not connections[1]
    assert result['lifecycle']['revision']==2
    assert result['lifecycle']['state']==('accepted' if no_reply else 'closed')
    assert active.execute(receipt_sql,(A,request['request_id']))==[
        {'actor_id':PROFILE['telegram_id'],'result':result}]
    events=active.execute(audit_sql,(A,action_name))
    assert len(events)==1
    if no_reply:
        assert result['lifecycle']['no_reply_needed'] and result['lifecycle']['operator_id']==PROFILE['telegram_id']
        assert events[0]['id']==lifecycle._no_reply_id(A,CA,'client',incoming)
        assert events[0]['payload']['message_id']==incoming and events[0]['payload']['counts_as_missed'] is False
        assert active.execute(workflow_sql,(A,CA))==[
            {'state':'accepted','operator_id':PROFILE['telegram_id'],'closed_at':None,'last_inbound_id':incoming}]
        assert len(active.execute('SELECT 1 FROM saas_dialog_episodes WHERE tenant_id=%s::uuid AND closed_at IS NULL',(A,)))==1
    active.commit()
    assert lifecycle.mutate(PROFILE,request)==result
    assert active.execute(audit_sql,(A,action_name))==events
    assert len(active.execute(receipt_sql,(A,request['request_id'])))==1
    active.commit()


@pytest.mark.parametrize('action',['close','dismiss'])
def test_exhaustion_is_bounded_and_preserves_original_tokens(monkeypatch,action):
    request={'action':action,'request_id':str(uuid.uuid4()),'expected_revision':7,'message_id':'original'}
    original=dict(request)
    operation=Mock(side_effect=DeadlockDetected('40P01'))
    pause=Mock()
    monkeypatch.setattr(lifecycle,'_mutate_once',operation)
    monkeypatch.setattr(lifecycle.time,'sleep',pause)
    with pytest.raises(DeadlockDetected):lifecycle.mutate(PROFILE,request)
    assert operation.call_count==3 and pause.call_count==2
    assert request==original
    assert all(call.args==(PROFILE,request) and call.args[1] is request for call in operation.call_args_list)


@pytest.mark.parametrize('action',['accept','transfer','case'])
def test_other_actions_are_never_retried(monkeypatch,action):
    operation=Mock(side_effect=DeadlockDetected('40P01'))
    monkeypatch.setattr(lifecycle,'_mutate_once',operation)
    with pytest.raises(DeadlockDetected):lifecycle.mutate(PROFILE,{'action':action})
    assert operation.call_count==1


@pytest.mark.parametrize('error',[OperationalError('connection lost'),StatementCompletionUnknown('unknown'),RuntimeError('unknown')])
def test_ambiguous_or_generic_failures_are_never_retried(monkeypatch,error):
    operation=Mock(side_effect=error)
    monkeypatch.setattr(lifecycle,'_mutate_once',operation)
    with pytest.raises(type(error)):lifecycle.mutate(PROFILE,{'action':'dismiss'})
    assert operation.call_count==1


@pytest.mark.parametrize('status',[403,404,409])
def test_retry_rechecks_fresh_denials_and_stops(monkeypatch,status):
    operation=Mock(side_effect=[DeadlockDetected('40P01'),TenantLinearError('changed',status),AssertionError('must stop')])
    monkeypatch.setattr(lifecycle,'_mutate_once',operation)
    monkeypatch.setattr(lifecycle.time,'sleep',Mock())
    with pytest.raises(TenantLinearError) as rejected:lifecycle.mutate(PROFILE,{'action':'dismiss'})
    assert rejected.value.status==status and operation.call_count==2
