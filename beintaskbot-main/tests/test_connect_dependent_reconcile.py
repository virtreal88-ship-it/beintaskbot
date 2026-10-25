"""Issue 248: real SQL, fictional customer data, no provider/send calls."""
import json
import uuid
import pytest
from test_tenant_support_inbox import A, B, CA, CB, AGENT, PROFILE, db, database, seed_job
from tenant_linear_policy import TenantLinearError
import tenant_connect_direct_reconcile as reconcile
import tenant_connect_reconcile_dependencies as deps

JID='994700000008@s.whatsapp.net'
LID='80000000000008@lid'
LEADS=[8101,8102]


def seed(db, monkeypatch):
    monkeypatch.setattr(reconcile,'_connect',lambda:db)
    reconcile.ensure_schema(db)
    seed_job(db)
    for identity,lead,state,reason in ((JID,8101,'human','manager_replied'),(LID,8102,'paused','operator_pause')):
        db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,conversation_kind) VALUES(%s::uuid,%s::uuid,%s,%s,'contact')",(A,CA,identity,lead))
        db.execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,channel_id,conversation_id,agent_id,state,reason) VALUES(%s::uuid,'connect',%s,%s,%s::uuid,%s,%s)",(A,CA,identity,AGENT,state,reason))
        db.execute("INSERT INTO saas_operator_dialog_reads(tenant_id,telegram_id,provider,channel_id,conversation_id,lead_id,last_read_at) VALUES(%s::uuid,11,'connect',%s,%s,%s,%s::timestamptz)",(A,CA,identity,lead,'2026-10-20T10:00:00Z' if lead==8102 else '2026-10-21T10:00:00Z'))
    db.execute("UPDATE saas_ai_agent_jobs SET conversation_id=%s,chat_id=%s,lead_id=8102,state='drafted' WHERE tenant_id=%s::uuid",(LID,LID,A))
    for n in range(85):
        lead=8101 if n<51 else 8102
        db.execute("""INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,message_type,media_url,raw)
          VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",(str(uuid.uuid4()),A,lead,'connect:synthetic248-'+str(n),'connect:'+CA,'outgoing' if n<51 else 'incoming','Synthetic '+str(n),'image' if n<14 else 'text','private-media' if n<14 else '',json.dumps({'conversationId':JID if lead==8101 else LID,'directIdentity':{'jid':JID,'lid':LID}})))
    for n in range(3):
        run=str(uuid.uuid4())
        db.execute("INSERT INTO saas_ai_agent_runs(tenant_id,id,agent_id,event_fingerprint,revision,model,state) VALUES(%s::uuid,%s::uuid,%s::uuid,'evt',%s,'mock','completed')",(A,run,AGENT,n+1))
        db.execute("""INSERT INTO saas_ai_agent_drafts(tenant_id,id,agent_id,run_id,lead_id,talk_id,chat_id,source_message_id,source_fingerprint,draft,state)
          VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,8102,'',%s,'source','evt','Synthetic draft',%s)""",(A,str(uuid.uuid4()),AGENT,run,LID,['pending','rejected','superseded'][n]))
    db.commit()
    return snapshot(db)


def snapshot(db):
    with db.cursor() as cur: result=deps.capture(cur,A,CA,[JID,LID],LEADS)
    db.commit();return result


def candidate():
    return next(x for x in reconcile.preview(PROFILE,CA)['items'] if x['pair_id']==reconcile._id(A,CA,JID,LID))


def request(operation='merge'):
    item=candidate()
    return {'operation':operation,'pair_id':item['pair_id'],'precondition':item['precondition'],'request_id':str(uuid.uuid4()),'confirm':True}


def test_full_transfer_retry_and_exact_rollback(db,monkeypatch):
    before=seed(db,monkeypatch);payload=request()
    assert candidate()['dependency_counts']=={'states':2,'jobs':1,'drafts':3,'reads':2}
    result=reconcile.apply(PROFILE,payload)
    assert result['state']=='merged' and result['moved_messages']==34
    after=snapshot(db)
    assert len(after['routes'])==1 and len(after['messages'])==85
    assert len({r['id'] for r in after['messages']})==85
    assert sum(bool(r['media_url']) for r in after['messages'])==14
    assert all(r['kommo_lead_id']==8101 for r in after['messages'])
    assert [{k:v for k,v in r.items() if k!='kommo_lead_id'} for r in sorted(before['messages'],key=lambda r:r['id'])]==[{k:v for k,v in r.items() if k!='kommo_lead_id'} for r in sorted(after['messages'],key=lambda r:r['id'])]
    assert after['states'][0]['state']=='human' and after['states'][0]['reason']=='manager_replied'
    assert after['states'][0]['resumed_at'] is None
    assert after['jobs'][0]['state']=='drafted' and after['jobs'][0]['attempts']==0
    assert all(r['chat_id']==JID and r['lead_id']==8101 for r in after['drafts'])
    assert len(after['reads'])==1 and after['reads'][0]['last_read_at'].startswith('2026-10-20')
    assert reconcile.apply(PROFILE,payload)['idempotent_replay'] is True
    assert snapshot(db)==after
    with pytest.raises(TenantLinearError,match='request_key_conflict'):
        reconcile.apply(PROFILE,{**payload,'operation':'rollback'})
    rollback=request('rollback')
    assert reconcile.apply(PROFILE,rollback)['state']=='rolled_back'
    assert snapshot(db)==before
    assert reconcile.apply(PROFILE,rollback)['idempotent_replay'] is True
    assert db.execute("SELECT count(*) AS n FROM saas_tenant_audit_events WHERE tenant_id=%s::uuid AND action LIKE 'connect_direct_merge_%%'",(A,))[0]['n']==2


@pytest.mark.parametrize('phase',['merge','rollback'])
def test_audit_error_rolls_back_every_write(db,monkeypatch,phase):
    seed(db,monkeypatch)
    if phase=='rollback': reconcile.apply(PROFILE,request())
    before=snapshot(db);payload=request(phase)
    def fail(*a,**k):raise RuntimeError('synthetic audit unavailable')
    monkeypatch.setattr(reconcile,'_audit',fail)
    with pytest.raises(RuntimeError,match='audit unavailable'):reconcile.apply(PROFILE,payload)
    assert snapshot(db)==before
    assert db.execute('SELECT count(*) AS n FROM saas_connect_direct_merge_requests WHERE tenant_id=%s::uuid AND request_id=%s::uuid',(A,payload['request_id']))[0]['n']==0


@pytest.mark.parametrize('sql,reason',[
    ("UPDATE saas_ai_agent_jobs SET state='queued' WHERE lead_id=8102",'job_not_terminal'),
    ("UPDATE saas_ai_agent_jobs SET state='unknown' WHERE lead_id=8102",'job_not_terminal'),
    ("UPDATE saas_ai_agent_drafts SET state='sending' WHERE lead_id=8102",'draft_send_in_flight'),
    ("UPDATE saas_ai_conversation_state SET state='active' WHERE conversation_id='"+LID+"'",'conversation_not_stopped'),
    ("UPDATE saas_ai_conversation_state SET assigned_to=99 WHERE conversation_id='"+LID+"';",None),
])
def test_in_flight_or_changed_dependencies_are_not_overwritten(db,monkeypatch,sql,reason):
    seed(db,monkeypatch);payload=request()
    db.execute(sql);db.commit();before=snapshot(db)
    if reason: assert reason in candidate()['conflicts']
    with pytest.raises(TenantLinearError):reconcile.apply(PROFILE,payload)
    assert snapshot(db)==before


def test_rollback_rejects_new_messages_and_modified_drafts(db,monkeypatch):
    seed(db,monkeypatch);reconcile.apply(PROFILE,request())
    payload=request('rollback')
    db.execute("UPDATE saas_ai_agent_drafts SET draft='Changed by employee' WHERE lead_id=8101");db.commit()
    after=snapshot(db)
    with pytest.raises(TenantLinearError,match='rollback_state_changed'):reconcile.apply(PROFILE,payload)
    assert snapshot(db)==after


def test_new_message_and_stale_preview_block_rollback(db,monkeypatch):
    seed(db,monkeypatch);reconcile.apply(PROFILE,request());payload=request('rollback')
    db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,body) VALUES(%s::uuid,%s::uuid,8101,'connect:new248',%s,'Synthetic new inbound')",(str(uuid.uuid4()),A,'connect:'+CA));db.commit()
    after=snapshot(db)
    payload=request('rollback')
    with pytest.raises(TenantLinearError,match='rollback_state_changed'):reconcile.apply(PROFILE,payload)
    assert snapshot(db)==after


def test_old_links_resolve_only_exact_owner_tenant_channel_and_lead(db,monkeypatch):
    from tenant_operator_access import access,assert_route
    seed(db,monkeypatch);reconcile.apply(PROFILE,request())
    with db.cursor() as cur:
        tenant,_,policy=access(cur,PROFILE,'dialogs')
        assert assert_route(cur,tenant,policy,'connect',LID,CA,8102,allow_merged=True)['lead_id']==8101
        with pytest.raises(TenantLinearError):assert_route(cur,tenant,policy,'connect',LID,CA,8102)
        for tenant,channel,lead in ((B,CA,8102),(A,CB,8102),(A,CA,9999)):
            with pytest.raises(TenantLinearError):assert_route(cur,tenant,policy,'connect',LID,channel,lead,allow_merged=True)
    db.commit()
    reconcile.apply(PROFILE,request('rollback'))
    with db.cursor() as cur:
        assert assert_route(cur,A,policy,'connect',LID,CA,8102)['lead_id']==8102


def test_wrong_owner_and_new_dependency_stop_without_partial_changes(db,monkeypatch):
    before=seed(db,monkeypatch);payload=request()
    with pytest.raises(TenantLinearError):reconcile.apply({**PROFILE,'telegram_id':22},payload)
    assert snapshot(db)==before
    db.execute("INSERT INTO saas_connect_participant_display_names(tenant_id,connect_channel_id,conversation_id,participant_jid,display_name,updated_by) VALUES(%s::uuid,%s::uuid,%s,'x@lid','Synthetic',11)",(A,CA,LID));db.commit()
    with pytest.raises(TenantLinearError):reconcile.apply(PROFILE,payload)
    assert snapshot(db)==before


def test_history_paginates_all_messages_and_old_case_anchor_after_merge(db,monkeypatch):
    import tenant_operator_dialogs as dialogs
    before=seed(db,monkeypatch);reconcile.apply(PROFILE,request())
    monkeypatch.setattr(dialogs,'_connect',lambda:db)
    first=dialogs.history(PROFILE,'connect',8102,CA,LID)
    second=dialogs.history(PROFILE,'connect',8102,CA,LID,before=first['next_before'])
    messages=first['messages']+second['messages']
    assert len(messages)==85 and len({m['id'] for m in messages})==85
    assert {m['id'] for m in messages}=={m['id'] for m in before['messages']}
    source=next(m['id'] for m in before['messages'] if m['kommo_lead_id']==8102)
    anchored=dialogs.history(PROFILE,'connect',8102,CA,LID,anchor=source)
    assert anchored['anchor_message_id']==source
