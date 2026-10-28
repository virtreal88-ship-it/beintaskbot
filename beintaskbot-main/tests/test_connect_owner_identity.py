"""Owner assertions are independent facts, exercised on disposable PostgreSQL."""
import json
import multiprocessing
import uuid

import pytest

from test_connect_channel_multiprocess import database
from test_tenant_support_inbox import db,A,B,CA,CB,PROFILE
from test_connect_dialog_equivalence import prepare,cfg,snapshot_rows,native_dependencies,candidate,command,apply
from test_connect_dependent_reconcile import JID,LID,LEADS
from tenant_linear_policy import TenantLinearError
import tenant_connect_owner_identity as owner
import tenant_connect_phone_evidence as phones
import tenant_ai_dialogue_merge as merge


def setup(db,monkeypatch):
    prepare(db,monkeypatch)
    db.execute("UPDATE saas_crm_messages SET raw=raw-'directIdentity' WHERE tenant_id=%s::uuid",(A,))
    db.commit()


def args():
    return {'canonical_lead_id':8101,'source_lead_id':8102,'phone':'+'+JID.split('@')[0],
            'evidence_ref':'test:owner-exact-confirmation-1'}


def preview(db,arguments=None,config=None):
    with db.cursor() as cur:result=owner.preview(cur,config or cfg(),arguments or args())
    db.commit()
    return result


def payload(db):
    return {**args(),'precondition':preview(db)['precondition'],'request_id':str(uuid.uuid4()),'confirm':True}


def execute(db,data,config=None):
    with db.cursor() as cur:result=owner.execute(cur,config or cfg(),data)
    db.commit()
    return result


def test_exact_owner_merge_preserves_all_sources_and_rolls_back_without_provider_proof(db,monkeypatch):
    import tenant_operator_dialogs as dialogs
    import tenant_support_inbox_store as support
    setup(db,monkeypatch);native_dependencies(db)
    monkeypatch.setattr(dialogs,'_connect',lambda:db)
    monkeypatch.setattr(support,'_connect',lambda:db)
    tables=['saas_crm_messages','saas_connect_conversations','saas_connect_direct_identities',
            'saas_crm_native_contact_conversations','saas_crm_native_contacts','saas_crm_native_deals',
            'saas_crm_native_tasks','saas_support_tickets','saas_ai_agent_jobs','saas_ai_agent_drafts','saas_operator_dialog_reads']
    before={t:snapshot_rows(db,t) for t in tables}
    item=preview(db)
    assert item['eligible'] and not item['source_provider_phone_proven']
    assert item['canonical_messages']==51 and item['source_messages']==34
    data=payload(db);result=execute(db,data)
    assert result['identity_source']=='owner_assertion' and result['combined_messages']==85
    assert result['verification']['original_messages_preserved']
    assert result['verification']['old_link_redirect_preserved']
    assert result['verification']['reference_counts_before']==result['verification']['reference_counts_current']
    assert {t:snapshot_rows(db,t) for t in tables}==before
    with db.cursor() as cur:
        assert phones.collect(cur,A,CA)['phones_by_identity'][LID]==set()
        diagnostic=merge.preview(cur,cfg(),{'search':LID})['routes'][0]
        assert not diagnostic['provider_phone_proven']
    db.commit()
    assert execute(db,data)['idempotent_replay']
    assert len([d for d in dialogs.listing(PROFILE)['dialogs'] if d['lead_id'] in LEADS])==1
    first=dialogs.history(PROFILE,'connect',8102,CA,LID)
    second=dialogs.history(PROFILE,'connect',8101,CA,JID,before=first['next_before'])
    assert len(first['messages']+second['messages'])==85
    assert {m['source_lead_id'] for m in first['messages']+second['messages']}==set(LEADS)
    case=db.execute('SELECT id FROM saas_support_tickets WHERE tenant_id=%s::uuid AND conversation_id=%s',(A,LID))[0]['id']
    db.commit()
    assert len(support.detail(PROFILE,str(case))['messages'])==34
    # Listing may initialize unrelated read markers; rollback must preserve them.
    before['saas_operator_dialog_reads']=snapshot_rows(db,'saas_operator_dialog_reads')
    apply(db,command(candidate(db),'rollback'))
    assert {t:snapshot_rows(db,t) for t in tables}==before
    receipt=db.execute('SELECT * FROM saas_connect_owner_identity_assertions WHERE tenant_id=%s::uuid',(A,))[0]
    assert receipt['source']=='owner_assertion' and receipt['owner_id']==receipt['revoked_by']==11
    assert receipt['evidence_ref']==args()['evidence_ref'] and receipt['created_at'] and receipt['revoked_at']
    assert len([d for d in dialogs.listing(PROFILE)['dialogs'] if d['lead_id'] in LEADS])==2
    db.commit()


@pytest.mark.parametrize('changed', ['phone','evidence_ref','canonical_lead_id','source_lead_id','confirm','actor','tenant','channel','new_message','new_conflict','audit'])
def test_exact_confirmation_stale_scope_conflicts_and_audit_fail_atomically(db,monkeypatch,changed):
    setup(db,monkeypatch);data=payload(db);config=cfg()
    if changed=='phone':data['phone']='+994700000009'
    if changed=='evidence_ref':data['evidence_ref']='test:a-different-owner-confirmation'
    if changed=='canonical_lead_id':data['canonical_lead_id']=8102
    if changed=='source_lead_id':data['source_lead_id']=8101
    if changed=='confirm':data['confirm']=False
    if changed=='actor':config=cfg(actor=22)
    if changed=='tenant':config=cfg(tenant=B)
    if changed=='channel':config=cfg(channel=CB)
    if changed=='new_message':
        db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel) VALUES(%s::uuid,%s::uuid,8102,'connect:new-owner-message',%s)",(str(uuid.uuid4()),A,'connect:'+CA));db.commit()
    if changed=='new_conflict':
        from tenant_connect_direct_identity import record_conflict
        with db.cursor() as cur:record_conflict(cur,A,CA,LID,LID,{},'new-owner-conflict')
        db.commit()
    if changed=='audit':
        def fail(*a,**k):raise RuntimeError('synthetic unavailable audit')
        monkeypatch.setattr(merge,'_audit',fail)
    before=snapshot_rows(db,'saas_crm_messages')
    with pytest.raises((TenantLinearError,RuntimeError)):execute(db,data,config)
    db.rollback()
    assert snapshot_rows(db,'saas_crm_messages')==before
    assert not db.execute('SELECT * FROM saas_connect_owner_identity_assertions')
    assert not db.execute('SELECT * FROM saas_connect_dialog_links WHERE active')
    assert not db.execute('SELECT * FROM saas_ai_dialogue_merge_requests')
    db.commit()


@pytest.mark.parametrize('proof',['different_phone','historical_split','source_only_flag'])
def test_owner_assertion_cannot_override_provider_facts(db,monkeypatch,proof):
    setup(db,monkeypatch)
    from tenant_connect_direct_identity import record_conflict
    if proof=='different_phone':
        db.execute("UPDATE saas_crm_messages SET raw=raw||%s::jsonb WHERE tenant_id=%s::uuid AND kommo_lead_id=8102",
          (json.dumps({'directIdentity':{'jid':'994700000009@s.whatsapp.net','lid':LID}}),A))
    else:
        with db.cursor() as cur:record_conflict(cur,A,CA,LID,LID,
          {'directIdentity':{'jid':JID,'lid':LID}} if proof=='historical_split' else {},'synthetic-owner-flag')
    db.commit()
    item=preview(db)
    assert not item['eligible'] and item['conflicts']
    with pytest.raises(TenantLinearError):execute(db,payload(db))
    db.rollback()


def test_replay_does_not_accept_modified_assertion_or_reactivate_rolled_back_merge(db,monkeypatch):
    setup(db,monkeypatch);data=payload(db);execute(db,data)
    with pytest.raises(TenantLinearError):execute(db,{**data,'evidence_ref':'test:other-confirmation'})
    db.rollback()
    apply(db,command(candidate(db),'rollback'))
    assert execute(db,data)['idempotent_replay']
    assert not db.execute('SELECT 1 FROM saas_connect_dialog_links WHERE active')
    assert not db.execute('SELECT 1 FROM saas_connect_owner_identity_assertions WHERE revoked_at IS NULL')
    db.commit()


def test_source_only_provider_conflict_remains_visible_on_canonical_after_merge(db,monkeypatch):
    from tenant_connect_identity import conflicted
    setup(db,monkeypatch);execute(db,payload(db))
    with db.cursor() as cur:
        assert not conflicted(cur,A,CA,JID)
        assert conflicted(cur,A,CA,LID)
        from tenant_connect_direct_identity import record_conflict
        record_conflict(cur,A,CA,LID,LID,{},'later-owner-source-conflict')
        assert conflicted(cur,A,CA,JID)
        assert not conflicted(cur,A,CB,JID)
        assert not conflicted(cur,B,CA,JID)
    db.commit()


def _concurrent(dsn,data,barrier,results):
    import psycopg
    from psycopg.rows import dict_row
    try:
        with psycopg.connect(dsn,row_factory=dict_row,options='-c statement_timeout=20000') as conn:
            barrier.wait(10)
            with conn.cursor() as cur:result=owner.execute(cur,cfg(),data)
        results.put(('ok',result))
    except Exception as error:results.put(('error',type(error).__name__,str(error)))


def test_concurrent_owner_confirmation_commits_once(db,monkeypatch):
    setup(db,monkeypatch);data=payload(db)
    ctx=multiprocessing.get_context('spawn');barrier,results=ctx.Barrier(2),ctx.Queue()
    children=[ctx.Process(target=_concurrent,args=(db.dsn,data,barrier,results)) for _ in range(2)]
    for child in children:child.start()
    try:
        values=[results.get(timeout=30) for _ in children]
        for child in children:child.join(5)
        assert all(v[0]=='ok' for v in values),values
        assert sorted(v[1]['idempotent_replay'] for v in values)==[False,True]
        assert len(db.execute('SELECT 1 FROM saas_connect_owner_identity_assertions'))==1
        assert len(db.execute('SELECT 1 FROM saas_connect_dialog_links WHERE active'))==1
        assert not db.execute('SELECT * FROM test_external_sends')
        db.commit()
    finally:
        for child in children:
            if child.is_alive():child.terminate();child.join(5)


def test_mcp_owner_flow_reuses_existing_scopes_and_rejects_read_only_grant(db,monkeypatch):
    import tenant_ai_learning_mcp as mcp
    setup(db,monkeypatch)
    config={**cfg(),'granted_scopes':[mcp.TOOL_SCOPE]}
    with db.cursor() as cur:
        item=mcp._tools(cur,config,'whatsapp_dialogue_owner_merge_preview',args())
        data={**args(),'precondition':item['precondition'],'request_id':str(uuid.uuid4()),'confirm':True}
        with pytest.raises(TenantLinearError,match='OAuth scope'):
            mcp._tools(cur,config,'whatsapp_dialogue_owner_merge',data)
        result=mcp._tools(cur,{**config,'granted_scopes':[mcp.TOOL_SCOPE,mcp.DIALOGUE_MERGE_SCOPE]},'whatsapp_dialogue_owner_merge',data)
        assert result['state']=='merged'
    db.commit()


def test_authenticated_http_owner_preview_merge_retry_and_rollback(db,monkeypatch):
    import asyncio
    from aiohttp import web
    from aiohttp.test_utils import TestClient,TestServer
    import tenant_ai_learning_mcp as mcp
    from test_ai_learning_mcp import _setup
    setup(db,monkeypatch)
    token=_setup(monkeypatch,db,scope=mcp.TOOL_SCOPE+' '+mcp.DIALOGUE_MERGE_SCOPE)
    async def check():
        app=web.Application();app.router.add_post('/mcp',mcp.mcp)
        async with TestClient(TestServer(app)) as client:
            async def call(name,arguments):
                response=await client.post('/mcp',headers={'Authorization':'Bearer '+token},json={
                    'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':arguments}})
                body=await response.json()
                assert response.status==200 and not body.get('error'),body
                assert not body['result'].get('isError'),body
                return json.loads(body['result']['content'][0]['text'])
            item=await call('whatsapp_dialogue_owner_merge_preview',args())
            data={**args(),'precondition':item['precondition'],'request_id':str(uuid.uuid4()),'confirm':True}
            result=await call('whatsapp_dialogue_owner_merge',data)
            assert result['state']=='merged' and not result['source_provider_phone_proven']
            assert (await call('whatsapp_dialogue_owner_merge',data))['idempotent_replay']
            fresh=(await call('whatsapp_dialogue_duplicate_candidates',{'pair_id':result['pair_id']}))['items'][0]
            assert fresh['verification']['verified_original_messages']==85
            rolled=await call('whatsapp_dialogue_merge',command(fresh,'rollback'))
            assert rolled['state']=='rolled_back'
    asyncio.run(check())
    assert len(db.execute('SELECT 1 FROM saas_connect_owner_identity_assertions WHERE revoked_at IS NOT NULL'))==1
    db.commit()
