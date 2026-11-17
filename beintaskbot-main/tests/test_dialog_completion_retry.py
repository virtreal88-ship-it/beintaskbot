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
    if action=='close':
        lifecycle.mutate(PROFILE,payload('accept'))
    request=payload(action,1 if action=='close' else 0,**({'message_id':incoming} if action=='dismiss' else {}))
    execute=active.execute
    failed=False
    def injected(sql,params=()):
        nonlocal failed
        result=execute(sql,params)
        target=('INSERT INTO saas_dialog_workflows' in sql if action=='dismiss' else "UPDATE saas_dialog_workflows SET state='closed'" in sql)
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
        session=Session();connections.append(session);return session
    monkeypatch.setattr(tenant_platform,'_connect',connect)
    monkeypatch.setattr(lifecycle.time,'sleep',Mock())
    result=lifecycle.mutate(PROFILE,request)
    assert len(connections)==2 and connections[0] is not connections[1]
    assert result['lifecycle']['revision']==(2 if action=='close' else 1)
    assert result['lifecycle']['state']==('closed' if action=='close' else 'dismissed')
    assert active.execute('SELECT count(*) AS n FROM saas_dialog_lifecycle_receipts WHERE request_id=%s::uuid',(request['request_id'],))[0]['n']==1
    assert active.execute('SELECT count(*) AS n FROM saas_tenant_audit_events WHERE action=%s',('dialog_'+action,))[0]['n']==1
    active.commit()
    assert lifecycle.mutate(PROFILE,request)==result


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
