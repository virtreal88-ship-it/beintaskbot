"""Native PostgreSQL process races. Only disposable localhost database fixtures.
No real messages are sent: the guarded dispatch records a synthetic SQL marker.
"""
import json
import multiprocessing
import os
import uuid
import pytest
import psycopg
from psycopg.rows import dict_row
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db, A, CA, PROFILE
from test_dialog_lifecycle_pglite import episodes, payload


def worker(dsn, actor, request, barrier, output, operation='mutate'):
    import tenant_platform
    import tenant_dialog_lifecycle as lifecycle
    from tenant_linear_policy import TenantLinearError
    os.environ['TENANT_DIALOG_LIFECYCLE_ENABLED']='true'
    os.environ['TENANT_DIALOG_ACCEPT_ENABLED']='true'
    tenant_platform._connect=lambda:psycopg.connect(dsn,row_factory=dict_row)
    try:
        barrier.wait(20)
        if operation=='mutate':
            result=lifecycle.mutate({'tenant_id':A,'telegram_id':actor},request)
        elif operation=='incoming':
            from tenant_ai_dialog_scope import lock_dialog
            with psycopg.connect(dsn,row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    lock_dialog(cur,A,'connect',request['channel_id'],request['conversation_id'])
                    message_id=str(uuid.uuid4())
                    cur.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,created_at,happened_at) VALUES(%s::uuid,%s::uuid,%s,%s,%s,'incoming','New actual message',clock_timestamp(),clock_timestamp()) RETURNING happened_at",(message_id,A,request['lead_id'],message_id,'connect:'+request['channel_id']))
                    event_at=cur.fetchone()['happened_at']
                    lifecycle.note_inbound(cur,A,request['channel_id'],request['conversation_id'],message_id,event_at)
                    lifecycle.record_message(cur,A,request['channel_id'],request['conversation_id'],message_id)
                    result={'incoming_id':message_id}
        elif operation=='revoke_source':
            with psycopg.connect(dsn,row_factory=dict_row) as conn:
                conn.execute('DELETE FROM saas_dialog_source_members WHERE tenant_id=%s::uuid AND source_id=%s::uuid AND employee_id=%s',(A,request['channel_id'],actor))
                result={'revoked':actor}
        else:
            with psycopg.connect(dsn,row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    from tenant_operator_access import access, assert_route
                    tenant,checked_actor,policy=access(cur,{'tenant_id':A,'telegram_id':actor},action='update')
                    assert_route(cur,tenant,policy,'connect',request['conversation_id'],request['channel_id'],request['lead_id'],action='update')
                    lifecycle.require_access(cur,A,checked_actor,'connect',request['channel_id'],request['conversation_id'],'update')
                    row=lifecycle._row(cur,A,request['channel_id'],request['conversation_id'])
                    cur.execute('INSERT INTO test_external_sends(channel,run_id) VALUES(%s,%s)',(request['channel_id'],str(row['revision'])))
                    result={'revision':row['revision'],'operator_id':row['operator_id']}
        output.put(('ok',result))
    except TenantLinearError as error:output.put(('denied',error.status))
    except BaseException as error:output.put(('error',type(error).__name__,str(error)))


def race(db, operations):
    ctx=multiprocessing.get_context('spawn');barrier=ctx.Barrier(len(operations));out=ctx.Queue()
    processes=[ctx.Process(target=worker,args=(db.dsn,actor,request,barrier,out,operation)) for actor,request,operation in operations]
    for process in processes:process.start()
    try:
        values=[out.get(timeout=35) for _ in processes]
        for process in processes:process.join(timeout=10);assert process.exitcode==0
        assert all(value[0]!='error' for value in values),values
        return values
    finally:
        for process in processes:
            if process.is_alive():process.terminate();process.join(timeout=5)


def test_native_simultaneous_accept_has_exactly_one_winner(episodes):
    request_a,request_b=payload(),payload()
    values=race(episodes,[(11,request_a,'mutate'),(33,request_b,'mutate')])
    successes=[value[1] for value in values if value[0]=='ok']
    assert len(successes)==1
    assert [value for value in values if value[0]=='denied']==[('denied',409)]
    rows=episodes.execute('SELECT operator_id,revision FROM saas_dialog_workflows WHERE tenant_id=%s::uuid',(A,))
    assert rows==[{'operator_id':successes[0]['lifecycle']['operator_id'],'revision':1}]
    assert episodes.execute('SELECT count(*) AS n FROM saas_dialog_episodes')[0]['n']==1
    assert episodes.execute("SELECT count(*) AS n FROM saas_tenant_audit_events WHERE action='dialog_accept'")[0]['n']==1
    assert not episodes.execute('SELECT * FROM test_external_sends')
    episodes.commit()


def test_native_same_request_id_retry_has_one_episode_and_one_receipt(episodes):
    request=payload()
    values=race(episodes,[(11,request,'mutate'),(11,request,'mutate')])
    assert values[0][0]==values[1][0]=='ok'
    assert values[0][1]==values[1][1]
    assert episodes.execute('SELECT count(*) AS n FROM saas_dialog_episodes')[0]['n']==1
    assert episodes.execute('SELECT count(*) AS n FROM saas_dialog_lifecycle_receipts')[0]['n']==1
    episodes.commit()


def test_native_simultaneous_start_has_one_new_episode_and_preserves_owner(episodes):
    import tenant_dialog_lifecycle as lifecycle
    lifecycle.mutate(PROFILE,payload())
    lifecycle.mutate(PROFILE,payload('close',1))
    start_a,start_b=payload('start',2),payload('start',2)
    values=race(episodes,[(11,start_a,'mutate'),(33,start_b,'mutate')])
    assert len([value for value in values if value[0]=='ok'])==1
    assert len([value for value in values if value[0]=='denied'])==1
    row=episodes.execute('SELECT state,operator_id,revision FROM saas_dialog_workflows WHERE tenant_id=%s::uuid',(A,))[0]
    assert row=={'state':'accepted','operator_id':11,'revision':3}
    assert episodes.execute('SELECT count(*) AS n FROM saas_dialog_episodes WHERE tenant_id=%s::uuid',(A,))[0]['n']==2
    assert episodes.execute('SELECT count(*) AS n FROM saas_dialog_lifecycle_receipts WHERE tenant_id=%s::uuid',(A,))[0]['n']==3
    episodes.commit()


def test_native_start_racing_fresh_inbound_never_loses_the_inbound(episodes):
    import tenant_dialog_lifecycle as lifecycle
    lifecycle.mutate(PROFILE,payload())
    lifecycle.mutate(PROFILE,payload('close',1))
    start=payload('start',2)
    values=race(episodes,[(11,start,'mutate'),(11,start,'incoming')])
    incoming=next(value[1]['incoming_id'] for value in values if value[0]=='ok' and 'incoming_id' in value[1])
    row=episodes.execute('SELECT state,revision,last_inbound_id FROM saas_dialog_workflows WHERE tenant_id=%s::uuid',(A,))[0]
    assert row['state'] in {'waiting','accepted'},values
    assert row['last_inbound_id']==incoming
    if row['state']=='accepted':
        assert any(value[0]=='ok' and value[1].get('lifecycle',{}).get('state')=='accepted' for value in values)
        assert row['revision']==4
        assert episodes.execute('SELECT count(*) AS n FROM saas_dialog_episodes WHERE tenant_id=%s::uuid',(A,))[0]['n']==2
    else:
        assert ('denied',409) in values
        assert row['revision']==3
        assert episodes.execute('SELECT count(*) AS n FROM saas_dialog_episodes WHERE tenant_id=%s::uuid',(A,))[0]['n']==1
    assert episodes.execute('SELECT direction FROM saas_crm_messages WHERE id=%s::uuid',(incoming,))[0]['direction']=='incoming'
    episodes.commit()


def test_native_cross_source_claims_do_not_share_owner(episodes):
    channel=str(uuid.uuid4())
    episodes.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s::uuid,%s::uuid,'Second source')",(A,channel))
    episodes.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id) VALUES(%s::uuid,%s::uuid,'client',43)",(A,channel));episodes.commit()
    other={**payload(),'channel_id':channel,'lead_id':43}
    values=race(episodes,[(11,payload(),'mutate'),(33,other,'mutate')])
    assert all(value[0]=='ok' for value in values),values
    rows=episodes.execute('SELECT channel_id::text,operator_id FROM saas_dialog_workflows WHERE tenant_id=%s::uuid',(A,));episodes.commit()
    assert {row['channel_id']:row['operator_id'] for row in rows}=={CA:11,channel:33}


def test_native_transfer_and_guarded_send_are_serialized_and_stale_send_denied(episodes):
    import tenant_dialog_lifecycle as lifecycle
    lifecycle.mutate(PROFILE,payload())
    transfer=payload('transfer',1,target_employee_id=33)
    send=payload()
    values=race(episodes,[(11,transfer,'mutate'),(11,send,'guarded_send')])
    assert any(value[0]=='ok' and value[1].get('lifecycle',{}).get('operator_id')==33 for value in values)
    sends=episodes.execute('SELECT run_id FROM test_external_sends');episodes.commit()
    # An already authorized dispatch may finish before transfer obtains its lock.
    # A sender admitted after transfer must never dispatch with old authority.
    assert all(row['run_id']=='1' for row in sends)
    stale=race(episodes,[(11,send,'guarded_send')])
    assert stale==[('denied',404)]
    assert episodes.execute('SELECT run_id FROM test_external_sends')==sends
    episodes.commit()
    allowed=race(episodes,[(33,send,'guarded_send')])
    assert allowed==[('ok',{'revision':2,'operator_id':33})]
    episodes.commit()



def test_native_close_racing_fresh_inbound_never_hides_the_new_message(episodes):
    import tenant_dialog_lifecycle as lifecycle
    lifecycle.mutate(PROFILE,payload())
    values=race(episodes,[(11,payload('close',1),'mutate'),(11,payload(),'incoming')])
    incoming=next(value[1]['incoming_id'] for value in values if value[0]=='ok' and 'incoming_id' in value[1])
    row=episodes.execute('SELECT state,revision,last_inbound_id FROM saas_dialog_workflows WHERE tenant_id=%s::uuid',(A,))[0]
    assert row['state'] in {'waiting','accepted'},values
    assert row['last_inbound_id']==incoming
    if row['state']=='accepted':
        assert ('denied',409) in values, 'arrival-before-close must reject a stale close revision'
        assert row['revision']==2
    else:
        assert row['revision']==3, 'close-before-arrival reopens the waiting queue'
    assert episodes.execute('SELECT body FROM saas_crm_messages WHERE id=%s::uuid',(incoming,))[0]['body']=='New actual message'
    assert not episodes.execute('SELECT * FROM saas_support_tickets')
    episodes.commit()


def test_native_dismiss_racing_newer_inbound_never_dismisses_the_new_message(episodes):
    latest=episodes.execute('SELECT id FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id=41',(A,))[0]['id'];episodes.commit()
    values=race(episodes,[(11,payload('dismiss',0,message_id=str(latest)),'mutate'),(11,payload(),'incoming')])
    incoming=next(value[1]['incoming_id'] for value in values if value[0]=='ok' and 'incoming_id' in value[1])
    rows=episodes.execute('SELECT state,last_inbound_id FROM saas_dialog_workflows WHERE tenant_id=%s::uuid',(A,))
    assert not rows or rows[0]['state']=='waiting',values
    if rows:assert rows[0]['last_inbound_id']==incoming
    else:assert ('denied',409) in values, 'new message ID invalidates dismissal before a workflow exists'
    assert episodes.execute('SELECT count(*) AS n FROM saas_dialog_episodes')[0]['n']==0
    assert episodes.execute('SELECT count(*) AS n FROM saas_support_tickets')[0]['n']==0
    episodes.commit()


def test_native_source_revocation_serializes_send_and_blocks_future_actions(episodes,monkeypatch):
    import tenant_dialog_lifecycle as lifecycle
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED','true')
    settings={'33':{'access_scopes':{'dialogs':{'view':'self','update':'self'}}}}
    episodes.execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value) VALUES(%s::uuid,'members',%s::jsonb) ON CONFLICT(tenant_id,policy_key) DO UPDATE SET value=EXCLUDED.value",(A,json.dumps(settings)))
    episodes.execute("INSERT INTO saas_dialog_source_members(tenant_id,provider,source_id,employee_id,updated_by) VALUES(%s::uuid,'connect',%s::uuid,33,11)",(A,CA));episodes.commit()
    lifecycle.mutate({'tenant_id':A,'telegram_id':33},payload())
    values=race(episodes,[(33,payload(),'guarded_send'),(33,payload(),'revoke_source')])
    assert ('ok',{'revoked':33}) in values
    assert all(value[0]=='ok' or value==('denied',403) for value in values)
    before=episodes.execute('SELECT count(*) AS n FROM test_external_sends')[0]['n'];episodes.commit()
    assert race(episodes,[(33,payload(),'guarded_send')])==[('denied',403)]
    assert race(episodes,[(33,payload('close',1),'mutate')])==[('denied',403)]
    assert episodes.execute('SELECT count(*) AS n FROM test_external_sends')[0]['n']==before
    episodes.commit()
