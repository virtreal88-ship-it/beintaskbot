"""Native multi-process episode creation races; disposable DB, no providers."""
import multiprocessing
import uuid
import pytest
import psycopg
from psycopg.rows import dict_row
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db, A, CA, PROFILE
from test_dialog_lifecycle_pglite import episodes, command, payload
from test_case_lifecycle_creation import create_data, seed_members


def worker(dsn, operation, request, barrier, output):
    import tenant_platform
    import tenant_support_inbox_store as store
    import tenant_dialog_lifecycle as lifecycle
    from tenant_linear_policy import TenantLinearError
    connect=lambda:psycopg.connect(dsn,row_factory=dict_row)
    tenant_platform._connect=connect;store._connect=connect
    try:
        barrier.wait(20)
        if operation=='create':
            result=store.create(PROFILE,request)
            output.put(('ok',operation,result['ticket']['id']))
        else:
            result=lifecycle.mutate(PROFILE,request)
            output.put(('ok',operation,result['lifecycle']))
    except TenantLinearError as exc:output.put(('denied',operation,exc.status))
    except BaseException as exc:output.put(('error',operation,type(exc).__name__,str(exc)))


def race(db, operations):
    ctx=multiprocessing.get_context('spawn');barrier=ctx.Barrier(len(operations));queue=ctx.Queue()
    workers=[ctx.Process(target=worker,args=(db.dsn,kind,data,barrier,queue)) for kind,data in operations]
    for process in workers:process.start()
    try:
        results=[queue.get(timeout=45) for _ in workers]
        for process in workers:process.join(10);assert process.exitcode==0
        assert all(item[0]!='error' for item in results),results
        return results
    finally:
        for process in workers:
            if process.is_alive():process.terminate();process.join(5)


def test_same_request_double_click_creates_one_frozen_case(episodes):
    import tenant_dialog_lifecycle as lifecycle
    accepted=lifecycle.mutate(PROFILE,payload())['lifecycle'];seed_members(episodes,accepted['id'],3)
    request=create_data(accepted['id'])
    values=race(episodes,[('create',request),('create',request)])
    assert all(row[0]=='ok' for row in values),values
    assert values[0][2]==values[1][2]
    assert episodes.execute('SELECT count(*) AS n FROM saas_support_tickets')[0]['n']==1
    assert episodes.execute('SELECT count(*) AS n FROM saas_support_ticket_messages')[0]['n']==3
    assert episodes.execute('SELECT state FROM saas_dialog_workflows')[0]['state']=='accepted'
    episodes.commit()


@pytest.mark.parametrize('operation',['close','transfer'])
def test_close_or_transfer_serializes_against_creation(episodes,operation):
    import tenant_dialog_lifecycle as lifecycle
    accepted=lifecycle.mutate(PROFILE,payload())['lifecycle'];seed_members(episodes,accepted['id'],3)
    mutation=payload(operation,1,**({'target_employee_id':33} if operation=='transfer' else {}))
    values=race(episodes,[('create',create_data(accepted['id'])),('mutate',mutation)])
    assert next(row for row in values if row[1]=='mutate')[0]=='ok',values
    created=next(row for row in values if row[1]=='create')
    assert created[0] in {'ok','denied'},values
    rows=episodes.execute('SELECT dialog_episode_id::text,history_frozen_at FROM saas_support_tickets')
    assert len(rows)==(1 if created[0]=='ok' else 0)
    if rows:
        assert rows[0]['dialog_episode_id']==accepted['id'] and rows[0]['history_frozen_at']
        assert episodes.execute('SELECT count(*) AS n FROM saas_support_ticket_messages')[0]['n']==3
    current=episodes.execute('SELECT state,operator_id,revision FROM saas_dialog_workflows')[0]
    assert current['revision']==2
    assert current['state']==('closed' if operation=='close' else 'accepted')
    assert current['operator_id']==(11 if operation=='close' else 33)
    assert not episodes.execute('SELECT * FROM test_external_sends')
    episodes.commit()
