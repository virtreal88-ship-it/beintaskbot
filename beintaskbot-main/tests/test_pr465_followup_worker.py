"""Real SQL admission/claim/worker/send; only provider boundaries are synthetic."""
import json
import uuid
from unittest.mock import Mock
import pytest
from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db,A,CA,AGENT,PROFILE
from test_integrated_connect_release import flow
import tenant_ai_agent_connect as connect
import tenant_dialog_consultations as consultations
import tenant_dialog_consultations_worker as dispatch
import tenant_support_inbox_store as tickets
import tenant_connect_transport as transport
import tenant_ai_agent_run_store as runs
import tenant_episode_media as episode
import tenant_knowledge_media as media


@pytest.fixture
def case_flow(flow,monkeypatch):
    f=flow;local=f['db']
    for module in (consultations,dispatch):monkeypatch.setattr(module,'_connect',lambda:local)
    consultations.schema(local)
    destinations=[]
    for index in (1,2):
        conversation='synthetic-developer-'+str(index)
        local.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name) VALUES(%s::uuid,%s::uuid,%s,%s,%s)",(A,CA,conversation,90+index,'Developer '+str(index)))
        local.commit()
        consultations.developer_marker(PROFILE,{'channel_id':CA,'conversation_id':conversation,'enabled':True})
        destinations.append({'channel_id':CA,'conversation_id':conversation})
    policy={'auto_case_dispatch_enabled':True,'auto_case_dispatch_owner_id':11,'developer_destinations':destinations}
    local.execute("UPDATE saas_ai_agents SET response_policy=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",(json.dumps(policy),A,AGENT));local.commit()
    developer_sends=Mock(side_effect=lambda route,*_args:{'message_id':'queue-'+route['conversation_id'],'external_message_id':'PROVIDER_'+route['conversation_id']})
    monkeypatch.setattr(transport,'send_consultation_with_receipt',developer_sends)
    def create_case(message_ids):
        source=message_ids[-1]
        with local.cursor() as cur:
            case=tickets.handoff_ticket(cur,{'tenant_id':A,'provider':'connect','channel_id':CA,'conversation_id':'client',
                'lead_id':41,'agent_id':AGENT,'event_fingerprint':'fresh-case-'+source,'source_message_id':source,
                'case_message_ids':message_ids},'Synthetic developer review')
        local.commit()
        for row in local.execute('SELECT id::text FROM saas_ai_case_developer_dispatches WHERE tenant_id=%s::uuid AND case_id=%s::uuid ORDER BY destination_key',(A,case['id'])):
            assert dispatch.dispatch_auto_case(row['id']) is None
        assert developer_sends.call_count==2,local.execute('SELECT state,reason FROM saas_ai_case_developer_dispatches')
        return case['id']
    def reply(number,text,*,fresh=True,enqueue=True):
        target=destinations[number-1]['conversation_id'];mid=str(uuid.uuid4());quote='PROVIDER_'+target
        raw={'id':mid,'quotedExternalMessageId':quote}
        local.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,body,raw,happened_at) VALUES(%s::uuid,%s::uuid,%s,%s,'incoming',%s,%s,%s::jsonb,clock_timestamp())",(uuid.uuid4(),A,90+number,'connect:'+mid,'connect:'+CA,text,json.dumps(raw)))
        with local.cursor() as cur:
            if fresh:consultations.admit_case_followup_intent(cur,A,CA,target,'connect:'+mid)
            ident=consultations.record_reply(cur,A,CA,target,mid,text,quote,'Developer '+str(number),source_external_id='connect:'+mid)
            admitted=connect.enqueue_case_developer_followup(cur,A,ident,source_external_id='connect:'+mid,quoted_message_id=quote) if enqueue else False
        local.commit()
        return ident,admitted
    f.update(create_case=create_case,reply=reply,developer_sends=developer_sends)
    return f


def current_job(f):return f['db'].execute("SELECT state,error_code,event_fingerprint FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND provider='connect' AND conversation_id='client'",(A,))[0]


@pytest.mark.parametrize('receipt',['complete','partial','missing','stale'])
def test_actual_developer_worker_reuses_exact_saved_photo_voice_or_blocks(case_flow,monkeypatch,receipt):
    f=case_flow;local=f['db']
    monkeypatch.setattr(media,'supports_images',lambda _:True)
    sources=[f['inbound']('voice','')[0],f['inbound']('image','')[0],f['inbound']('text','Please explain the screen and recorded amount')[0]]
    assert f['process']();assert f['send'].call_count==1
    case=f['create_case'](sources)
    f['send'].reset_mock();f['generate'].reset_mock();f['speech'].reset_mock();f['observation'].reset_mock();media.download_media.reset_mock()
    if receipt=='partial':
        local.execute("UPDATE saas_media_evidence_receipts SET status='partial',coverage='{}'::jsonb WHERE tenant_id=%s::uuid AND media_kind='image'",(A,))
    elif receipt=='missing':local.execute("DELETE FROM saas_media_evidence_receipts WHERE tenant_id=%s::uuid AND media_kind='image'",(A,))
    elif receipt=='stale':local.execute("UPDATE saas_crm_messages SET raw=raw||'{\"mediaId\":\"replacement\"}'::jsonb WHERE tenant_id=%s::uuid AND external_id=%s",(A,'connect:'+sources[1]))
    local.commit()
    reply,admitted=f['reply'](1,'Accepted. I will review the reported amount tomorrow.')
    assert reply and admitted
    assert f['process']()
    if receipt=='complete':
        assert f['generate'].call_count==1,(current_job(f),local.execute('SELECT state,error_code FROM saas_ai_agent_runs'));assert f['send'].call_count==1,(current_job(f),local.execute('SELECT state,error_code FROM saas_ai_agent_runs'))
        lines=str(f['generate'].call_args.args[3]);assert 'Salam, qiymet nedir?' in lines and 'Visible test observation' in lines
        assert f['generate'].call_args.kwargs['extra_context']['verified_developer_case_updates'][0]['message'].endswith('tomorrow.')
        assert current_job(f)['state']=='sent'
    else:
        f['generate'].assert_not_called();f['send'].assert_not_called()
        assert current_job(f)['state'] in {'skipped','superseded'},current_job(f)
    f['speech'].assert_not_called();f['observation'].assert_not_called();media.download_media.assert_not_called()
    assert local.execute('SELECT status FROM saas_support_tickets WHERE id=%s::uuid',(case,))[0]['status']!='resolved'


def test_actual_worker_coalesces_two_recipients_and_later_customer_question(case_flow):
    f=case_flow;source=f['inbound']('text','The amount is wrong')[0];assert f['process']()
    f['create_case']([source]);f['send'].reset_mock();f['generate'].reset_mock()
    first,admitted=f['reply'](1,'I accepted this report and will review it tomorrow.');assert admitted
    second,admitted=f['reply'](2,'Please use version 2.1 for the next verification.');assert admitted
    assert f['process']();assert f['send'].call_count==1
    updates=f['generate'].call_args.kwargs['extra_context']['verified_developer_case_updates']
    assert [item['source_id'] for item in updates]==['case-reply:'+first,'case-reply:'+second]
    assert f['process']() is None
    assert not f['db'].execute("SELECT fingerprint FROM saas_tenant_webhook_events WHERE event_type='developer_case_reply' AND state IN ('queued','processing')")
    f['generate'].reset_mock();f['send'].reset_mock()
    f['inbound']('text','When will the developer review this, and which version should I check?')
    assert f['process']();assert f['send'].call_count==1
    updates=f['generate'].call_args.kwargs['extra_context']['verified_developer_case_updates']
    assert {item['source_id'] for item in updates}=={'case-reply:'+first,'case-reply:'+second}


@pytest.mark.parametrize('new_customer',[False,True])
def test_second_reply_during_generation_supersedes_without_lost_work(case_flow,new_customer):
    f=case_flow;source=f['inbound']('text','Original report')[0];assert f['process']()
    f['create_case']([source]);f['send'].reset_mock();f['generate'].reset_mock()
    first,admitted=f['reply'](1,'I will review tomorrow.');assert admitted
    second=[]
    def generation(*_args,**_kwargs):
        if not second:
            ident,admitted=f['reply'](2,'Please verify version 2.1.');assert admitted;second.append(ident)
            if new_customer:f['inbound']('text','Which version should I verify tomorrow?')
        return 'Accepted. Review is planned tomorrow.',{'total_tokens':2}
    f['generate'].side_effect=generation
    assert f['process']();f['send'].assert_not_called()
    assert current_job(f)['state']=='queued'
    assert f['process']();f['send'].assert_called_once()
    updates=f['generate'].call_args.kwargs['extra_context']['verified_developer_case_updates']
    assert {item['source_id'] for item in updates}=={'case-reply:'+first,'case-reply:'+second[0]}


def test_old_linked_and_unlinked_history_never_admits_paid_backfill(case_flow):
    f=case_flow;source=f['inbound']('text','Report')[0];assert f['process']()
    f['create_case']([source]);f['generate'].reset_mock();f['send'].reset_mock()
    linked,admitted=f['reply'](1,'Historical linked answer',fresh=False);assert linked and not admitted
    target='synthetic-developer-2';mid=str(uuid.uuid4())
    f['db'].execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,body,raw) VALUES(%s::uuid,%s::uuid,92,%s,'incoming',%s,'Historical unlinked answer',%s::jsonb)",(uuid.uuid4(),A,'connect:'+mid,'connect:'+CA,json.dumps({'id':mid,'quotedExternalMessageId':'PROVIDER_'+target})));f['db'].commit()
    dispatch.reconcile();dispatch.reconcile()
    assert f['db'].execute('SELECT count(*) AS n FROM saas_dialog_consultation_replies')[0]['n']==2
    assert not f['db'].execute("SELECT fingerprint FROM saas_tenant_webhook_events WHERE event_type='developer_case_reply'")
    assert f['process']() is None;f['generate'].assert_not_called();f['send'].assert_not_called()


@pytest.mark.parametrize('change',['close','reassign','marker','channel','owner'])
def test_actual_worker_rechecks_authority_after_generation(case_flow,change):
    f=case_flow;local=f['db'];source=f['inbound']('text','Original report')[0];assert f['process']()
    case=f['create_case']([source]);f['send'].reset_mock();f['generate'].reset_mock()
    assert f['reply'](1,'We will review tomorrow.')[1]
    def generate(*_args,**_kwargs):
        if change=='close':tickets.update(PROFILE,case,{'status':'resolved'})
        elif change=='reassign':tickets.update(PROFILE,case,{'assigned_developer_channel_id':CA,'assigned_developer_conversation_id':'synthetic-developer-2'})
        elif change=='marker':consultations.developer_marker(PROFILE,{'channel_id':CA,'conversation_id':'synthetic-developer-1','enabled':False})
        elif change=='channel':local.execute('UPDATE saas_connect_channels SET active=FALSE WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid',(A,CA));local.commit()
        elif change=='owner':local.execute('UPDATE saas_tenants SET owner_telegram_id=22 WHERE id=%s::uuid',(A,));local.commit()
        return 'Review is planned tomorrow.',{'total_tokens':2}
    f['generate'].side_effect=generate
    assert f['process']();f['generate'].assert_called_once();f['send'].assert_not_called()


@pytest.mark.parametrize('change',['close','reassign','marker','channel','owner'])
def test_postgres_final_send_holds_current_authority_rows_until_receipt(case_flow,monkeypatch,change):
    import threading
    import time
    import psycopg
    from psycopg.rows import dict_row
    f=case_flow;local=f['db'];source=f['inbound']('text','Original report')[0];assert f['process']()
    case=f['create_case']([source]);f['send'].reset_mock();f['generate'].reset_mock()
    assert f['reply'](1,'We will review tomorrow.')[1]
    dsn=local.dsn;started=threading.Event();finished=threading.Event();pids=[];errors=[];threads=[]
    def factory():return psycopg.connect(dsn,row_factory=dict_row)
    # Separate physical connections are essential: a reused fixture connection
    # would make this test incapable of observing real PostgreSQL row locks.
    monkeypatch.setattr(transport,'_connect',factory)
    def writer():
        try:
            with factory() as connection:
                pids.append(connection.info.backend_pid);started.set()
                if change=='close':connection.execute("UPDATE saas_support_tickets SET status='resolved' WHERE tenant_id=%s::uuid AND id=%s::uuid",(A,case))
                elif change=='reassign':connection.execute("UPDATE saas_support_tickets SET assigned_developer_channel_id=%s::uuid,assigned_developer_conversation_id='synthetic-developer-2' WHERE tenant_id=%s::uuid AND id=%s::uuid",(CA,A,case))
                elif change=='marker':connection.execute("UPDATE saas_dialog_developer_markers SET enabled=FALSE WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id='synthetic-developer-1'",(A,CA))
                elif change=='channel':connection.execute('UPDATE saas_connect_channels SET active=FALSE WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid',(A,CA))
                else:connection.execute('UPDATE saas_tenants SET owner_telegram_id=22 WHERE id=%s::uuid',(A,))
        except Exception as error:errors.append(error)
        finally:finished.set()
    def provider_send(*_args):
        thread=threading.Thread(target=writer);threads.append(thread);thread.start();assert started.wait(5)
        observed=False;deadline=time.monotonic()+5
        with factory() as observer:
            while time.monotonic()<deadline:
                row=observer.execute('SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s',(pids[0],)).fetchone()
                if row and row['wait_event_type']=='Lock':observed=True;break
                observer.commit();time.sleep(.01)
        assert observed and not finished.is_set(),'authority mutation was not fenced at the final provider boundary'
        return 'synthetic-accepted-'+str(uuid.uuid4())
    f['send'].side_effect=provider_send
    assert f['process']()
    for thread in threads:thread.join(10);assert not thread.is_alive()
    assert not errors,errors
    f['send'].assert_called_once();assert finished.is_set()
    assert current_job(f)['state']=='sent'


def test_unknown_send_holds_later_developer_intent_without_retry(case_flow):
    f=case_flow;source=f['inbound']('text','Original report')[0];assert f['process']()
    f['create_case']([source]);f['send'].reset_mock();f['generate'].reset_mock()
    assert f['reply'](1,'Accepted for tomorrow.')[1]
    f['send'].side_effect=TimeoutError('Synthetic ambiguous provider acceptance')
    assert f['process']();f['send'].assert_called_once();assert current_job(f)['state']=='unknown'
    second,admitted=f['reply'](2,'New verification detail');assert second and not admitted
    dispatch.reconcile();dispatch.reconcile()
    assert current_job(f)['state']=='unknown' and f['process']() is None
    f['send'].assert_called_once()
    assert f['db'].execute('SELECT followup_requested_at FROM saas_dialog_consultation_replies WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,second))[0]['followup_requested_at'] is not None


@pytest.mark.parametrize('kind',['new_reply','done','duplicate_done'])
def test_signed_developer_ingress_and_final_client_send_do_not_invert_dialog_locks(case_flow,monkeypatch,kind):
    import asyncio
    import hashlib
    import hmac
    import logging
    import threading
    import psycopg
    from psycopg.rows import dict_row
    import tenant_ai_agent_worker as ai_worker
    import tenant_ai_group_coordination as coordination
    import tenant_internal_consultations as internal
    import tenant_public_case as public
    import tenant_knowledge_live_queue as live_queue
    f=case_flow;local=f['db'];source=f['inbound']('text','Original report')[0];assert f['process']()
    f['create_case']([source]);f['send'].reset_mock();f['generate'].reset_mock()
    reply,admitted=f['reply'](1,'Review tomorrow.');assert admitted
    previous=local.execute('SELECT message_id FROM saas_dialog_consultation_replies WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,reply))[0]['message_id']
    cipher=transport._fernet()
    local.execute("UPDATE saas_tenant_integrations SET secrets=%s WHERE tenant_id=%s::uuid AND provider='connect'",(cipher.encrypt(json.dumps({'api_key':'mock-connect-key','webhook_secret':'race-secret'}).encode()),A));local.commit()
    dsn=local.dsn
    def factory():return psycopg.connect(dsn,row_factory=dict_row)
    for module in (transport,consultations,dispatch,coordination,internal,public,live_queue):monkeypatch.setattr(module,'_connect',factory)
    # Synchronization wraps the real authority operations without replacing
    # their SQL. Each participant has its own physical database connection.
    fenced=threading.Event();developer_updated=threading.Event();finished=threading.Event();errors=[]
    original_lock=transport.lock_dialog;original_upsert=transport._upsert_conversation
    def lock(cur,tenant,provider,channel,conversation):
        value=original_lock(cur,tenant,provider,channel,conversation)
        if conversation=='client' and threading.current_thread().name!='signed-developer':
            fenced.set();assert developer_updated.wait(10)
        return value
    def upsert(cur,tenant,channel,conversation,metadata):
        value=original_upsert(cur,tenant,channel,conversation,metadata)
        if threading.current_thread().name=='signed-developer':developer_updated.set()
        return value
    monkeypatch.setattr(transport,'lock_dialog',lock);monkeypatch.setattr(transport,'_upsert_conversation',upsert)
    def webhook():
        try:
            assert fenced.wait(10)
            data={'id':previous if kind=='duplicate_done' else str(uuid.uuid4()),'channelId':CA,
                'conversationId':'synthetic-developer-1','type':'text','text':'Additional detail' if kind=='new_reply' else '/done',
                'quotedExternalMessageId':'PROVIDER_synthetic-developer-1'}
            event={'id':str(uuid.uuid4()),'type':'message.inbound.received','data':data}
            raw=json.dumps(event).encode();signature='sha256='+hmac.new(b'race-secret',raw,hashlib.sha256).hexdigest()
            assert transport.ingest(raw,signature,event)
        except Exception as error:errors.append(error)
        finally:finished.set()
    thread=threading.Thread(target=webhook,name='signed-developer',daemon=True);thread.start()
    job=runs.claim_next();assert job
    asyncio.run(ai_worker.process_connect_one(job,logging.getLogger('signed-final-race')))
    thread.join(15);assert not thread.is_alive(),'signed ingress deadlocked against final client send'
    assert finished.is_set() and not errors,errors
    assert f['send'].call_count<=1
    # The duplicate receipt never becomes a new /done command or followup.
    if kind=='duplicate_done':assert len(local.execute('SELECT id FROM saas_dialog_consultation_replies'))==1


def test_real_save_agent_edit_during_final_window_does_not_invert_agent_dialog_locks(case_flow,monkeypatch):
    import asyncio
    import logging
    import threading
    import psycopg
    from psycopg.rows import dict_row
    import tenant_ai_agents_store as agents
    import tenant_ai_agent_worker as ai_worker
    f=case_flow;local=f['db'];source=f['inbound']('text','Original report')[0];assert f['process']()
    f['create_case']([source]);f['send'].reset_mock();f['generate'].reset_mock()
    assert f['reply'](1,'Review tomorrow.')[1]
    version=local.execute('SELECT version FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,AGENT))[0]['version'];local.commit()
    def factory():return psycopg.connect(local.dsn,row_factory=dict_row)
    monkeypatch.setattr(agents,'_connect',factory);monkeypatch.setattr(transport,'_connect',factory)
    fenced=threading.Event();editing=threading.Event();finished=threading.Event();errors=[]
    real_lock=transport.lock_dialog;real_reconcile=connect.reconcile_tenant_connect_assignments
    def lock(cur,tenant,provider,channel,conversation):
        result=real_lock(cur,tenant,provider,channel,conversation)
        if conversation=='client':fenced.set();assert editing.wait(10)
        return result
    def reconcile(*args,**kwargs):
        if threading.current_thread().name=='settings-save':editing.set()
        return real_reconcile(*args,**kwargs)
    monkeypatch.setattr(transport,'lock_dialog',lock);monkeypatch.setattr(connect,'reconcile_tenant_connect_assignments',reconcile)
    def save():
        try:
            assert fenced.wait(10)
            agents.save_agent(PROFILE,{'expected_version':version,'name':'Updated while client fence held'},agent_id=AGENT)
        except Exception as error:errors.append(error)
        finally:finished.set()
    thread=threading.Thread(target=save,name='settings-save',daemon=True);thread.start()
    job=runs.claim_next();assert job
    asyncio.run(ai_worker.process_connect_one(job,logging.getLogger('settings-final-race')))
    thread.join(15);assert not thread.is_alive(),'save_agent deadlocked against the final client fence'
    assert finished.is_set() and not errors,errors
    f['send'].assert_not_called()
    assert local.execute('SELECT name FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,AGENT))[0]['name']=='Updated while client fence held'


def test_fresh_reply_while_customer_job_owns_progress_is_consumed_by_its_receipt(case_flow):
    f=case_flow;source=f['inbound']('text','Original report')[0];assert f['process']()
    f['create_case']([source]);f['send'].reset_mock();f['generate'].reset_mock()
    f['inbound']('text','Which version should I test?')
    reply,admitted=f['reply'](1,'Please test version 2.1 tomorrow.');assert reply and not admitted
    assert f['process']();f['send'].assert_called_once()
    assert f['generate'].call_args.kwargs['extra_context']['verified_developer_case_updates'][0]['source_id']=='case-reply:'+reply
    dispatch.reconcile();dispatch.reconcile()
    assert f['process']() is None;f['send'].assert_called_once();f['generate'].assert_called_once()


@pytest.mark.parametrize('change',['revoked','source_quote_missing','source_body_mismatch'])
def test_done_requires_current_recipient_and_exact_saved_command(case_flow,change):
    f=case_flow;local=f['db'];source=f['inbound']('text','Original report')[0];assert f['process']()
    case=f['create_case']([source]);f['send'].reset_mock();f['generate'].reset_mock()
    target='synthetic-developer-1';mid=str(uuid.uuid4());quote='PROVIDER_'+target
    raw={'id':mid,'quotedExternalMessageId':quote}
    if change=='source_quote_missing':raw.pop('quotedExternalMessageId')
    if change=='revoked':consultations.developer_marker(PROFILE,{'channel_id':CA,'conversation_id':target,'enabled':False})
    local.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,direction,channel,body,raw) VALUES(%s::uuid,%s::uuid,91,%s,'incoming',%s,%s,%s::jsonb)",(uuid.uuid4(),A,'connect:'+mid,'connect:'+CA,'I will look tomorrow' if change=='source_body_mismatch' else '/done',json.dumps(raw)))
    with local.cursor() as cur:assert consultations.record_reply(cur,A,CA,target,mid,'/done',quote,source_external_id='connect:'+mid)
    local.commit()
    assert local.execute('SELECT status FROM saas_support_tickets WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,case))[0]['status']!='resolved'
