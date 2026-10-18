import pytest
from test_tenant_support_inbox import database,db,A,B,CA,CB,AGENT,PROFILE
from test_integrated_connect_release import flow
from test_connect_channel_isolation import channels
from tenant_linear_policy import TenantLinearError
import tenant_ai_draft_review as review
import tenant_ai_agents_store as store
import tenant_connect_transport as transport

@pytest.fixture
def draft(channels,monkeypatch):
    f=channels;d=f['db'];monkeypatch.setattr(review,'_connect',lambda:d)
    d.execute("UPDATE saas_ai_agents SET mode='manual'");d.commit()
    mid,_=f['incoming'](CA);f['process']()
    row=d.execute('SELECT id FROM saas_ai_agent_drafts ORDER BY created_at DESC LIMIT 1')[0];d.commit()
    f.update(draft_id=str(row['id']),mid=mid)
    return f

def approval(view,**kw):
    return dict(expected_version=view['version'],expected_context_token=view['context_token'],**kw)

def test_context_edit_original_and_cas(draft):
    f=draft;v=review.read(PROFILE,f['draft_id']);assert v['source_known'] and len(v['source_messages'])==1
    assert v['dialog_route']['channel_id']==CA and not v['has_new_messages']
    assert v['reason']['code']=='unknown'
    changed=review.edit(PROFILE,v['id'],dict(draft='Edited safely',expected_version=v['version']))
    assert changed['version']==2 and changed['original_draft']==v['draft'] and changed['edited_by']==11
    with pytest.raises(TenantLinearError):review.edit(PROFILE,v['id'],dict(draft='Conflict',expected_version=1))
    reserved=store.reserve_draft_send(PROFILE,v['id'],approval(changed));assert reserved['draft']=='Edited safely'
    with pytest.raises(TenantLinearError):review.edit(PROFILE,v['id'],dict(draft='Too late',expected_version=2))
    with pytest.raises(TenantLinearError):store.reserve_draft_send(PROFILE,v['id'],approval(changed))
    f['send'].assert_not_called()

@pytest.mark.parametrize('change',['human','automatic','other_tenant','wrong_version','wrong_context'])
def test_send_guards(draft,change):
    f=draft;v=review.read(PROFILE,f['draft_id']);payload=approval(v);profile=PROFILE;d=f['db']
    if change=='human':store.set_conversation_state(PROFILE,'client','human','manual','connect',CA)
    elif change=='automatic':d.execute("UPDATE saas_ai_agents SET mode='automatic' WHERE id=%s::uuid",(AGENT,));d.commit()
    elif change=='other_tenant':profile={'tenant_id':B,'telegram_id':22}
    elif change=='wrong_version':payload['expected_version']=99
    else:payload['expected_context_token']='fake'
    with pytest.raises(TenantLinearError):store.reserve_draft_send(profile,v['id'],payload)
    f['send'].assert_not_called()

def test_stale_requires_new_snapshot_confirmation_and_no_competing_work(draft):
    f=draft;d=f['db'];v=review.read(PROFILE,f['draft_id'])
    f['incoming'](CA)
    with pytest.raises(TenantLinearError):store.reserve_draft_send(PROFILE,v['id'],approval(v))
    new=review.read(PROFILE,v['id']);assert new['has_new_messages']
    with pytest.raises(TenantLinearError):store.reserve_draft_send(PROFILE,v['id'],approval(new,confirm_new_messages=True))
    d.execute("UPDATE saas_ai_agent_jobs SET state='skipped'");d.commit()
    new=review.read(PROFILE,v['id'])
    with pytest.raises(TenantLinearError):store.reserve_draft_send(PROFILE,v['id'],approval(new))
    reserved=store.reserve_draft_send(PROFILE,v['id'],approval(new,confirm_new_messages=True))
    assert reserved['draft']==v['draft'];f['send'].assert_not_called()

def test_second_channel_and_webhook_do_not_stale_personal_draft(draft):
    f=draft;v=review.read(PROFILE,f['draft_id']);f['incoming'](CB)
    assert f['incoming'](CA,mid=f['mid'])[1]==0
    new=review.read(PROFILE,v['id']);assert new['context_token']==v['context_token'] and not new['has_new_messages']
    assert new['dialog_route']['channel_id']==CA

def test_final_check_blocks_context_or_mode_change(draft):
    f=draft;v=review.read(PROFILE,f['draft_id']);r=store.reserve_draft_send(PROFILE,v['id'],approval(v))
    f['incoming'](CA)
    assert transport.send_ai_reply_if_active(A,41,CA,'client',r['draft'],r['id'],r['expected_revision'],draft_review={'profile':PROFILE,**approval(v)}) is None
    f['send'].assert_not_called()

def test_rejected_not_editable_and_review_version(draft):
    f=draft;v=review.read(PROFILE,f['draft_id'])
    with pytest.raises(TenantLinearError):store.review_draft(PROFILE,v['id'],'rejected',99)
    store.review_draft(PROFILE,v['id'],'rejected',v['version'])
    with pytest.raises(TenantLinearError):review.edit(PROFILE,v['id'],dict(draft='No',expected_version=v['version']))


@pytest.mark.parametrize('kind',['image','audio','document'])
def test_source_media_stays_on_verified_channel(draft,kind):
    f=draft;d=f['db'];d.execute("UPDATE saas_crm_messages SET message_type=%s WHERE external_id=%s AND tenant_id=%s::uuid",(kind,'connect:'+f['mid'],A));d.commit()
    v=review.read(PROFILE,f['draft_id']);m=v['source_messages'][0]
    assert m['media_available'] and m['source_channel_id']==CA
    assert 'media_url' not in m and v['dialog_route']['source_message_id']==m['id']


def test_unknown_source_is_honest_and_cannot_send(draft):
    f=draft;d=f['db'];d.execute("UPDATE saas_ai_agent_drafts SET source_message_id='unverified'");d.commit()
    v=review.read(PROFILE,f['draft_id']);assert not v['source_known'] and v['source_messages']==[]
    with pytest.raises(TenantLinearError):store.reserve_draft_send(PROFILE,v['id'],approval(v,confirm_new_messages=True))


def test_employee_without_ai_management_cannot_review(draft):
    from test_tenant_operator_workspace import employee
    profile=employee(draft['db'],['dialogs','support'])
    with pytest.raises(TenantLinearError):review.read(profile,draft['draft_id'])


def test_draft_list_enriched(draft):
    rows=store.list_drafts(PROFILE)
    assert len(rows)==1 and rows[0]['version']==1 and rows[0]['source_known']


from test_group_coordination import group,config,incoming,G
import tenant_ai_group_coordination as groups


def test_group_review_cross_channel_context_and_human_fence(group,monkeypatch):
    f=group;d=f['db'];monkeypatch.setattr(review,'_connect',lambda:d)
    d.execute("UPDATE saas_ai_agents SET mode='manual'");d.execute('DELETE FROM saas_ai_agent_jobs');d.commit()
    groups.configure(PROFILE,config());store.set_conversation_state(PROFILE,G,'active',provider='connect',channel_id=CA)
    assert incoming(f,CA,'DRAFT_GROUP_FIRST')==1;f['process']()
    draft_id=str(d.execute('SELECT id FROM saas_ai_agent_drafts')[0]['id']);d.commit()
    v=review.read(PROFILE,draft_id);assert v['dialog_route']['conversation_id']==G
    assert incoming(f,CB,'DRAFT_GROUP_SECOND')==0
    latest=review.read(PROFILE,draft_id);assert latest['has_new_messages'] and latest['context_token']!=v['context_token']
    store.set_conversation_state(PROFILE,G,'human',provider='connect',channel_id=CB)
    assert review.read(PROFILE,draft_id)['send_allowed'] is False
    with pytest.raises(TenantLinearError):store.reserve_draft_send(PROFILE,draft_id,approval(latest,confirm_new_messages=True))
    f['send'].assert_not_called()


def test_reviewed_delivery_sends_saved_text_once(draft):
    f=draft;v=review.edit(PROFILE,f['draft_id'],{'draft':'Employee-approved text','expected_version':1})
    r=store.reserve_draft_send(PROFILE,v['id'],approval(v))
    sent=transport.send_ai_reply_if_active(A,41,CA,'client',r['draft'],r['id'],r['expected_revision'],draft_review={'profile':PROFILE,**approval(v)})
    assert sent and f['send'].call_count==1
    assert f['send'].call_args.args[1]=='Employee-approved text'
    store.finish_draft_send(A,v['id'],'accepted')
    with pytest.raises(TenantLinearError):store.reserve_draft_send(PROFILE,v['id'],approval(v))
    assert f['send'].call_count==1


def test_final_check_human_takeover(draft):
    f=draft;v=review.read(PROFILE,f['draft_id']);r=store.reserve_draft_send(PROFILE,v['id'],approval(v))
    store.set_conversation_state(PROFILE,'client','human','manual','connect',CA)
    assert transport.send_ai_reply_if_active(A,41,CA,'client',r['draft'],r['id'],r['expected_revision'],draft_review={'profile':PROFILE,**approval(v)}) is None
    f['send'].assert_not_called()


def test_additive_migration_preserves_pending_rows(draft):
    d=draft['db']
    before=d.execute('SELECT id,draft,state FROM saas_ai_agent_drafts');d.commit()
    # Disposable test database: emulate the production table before this release.
    d.execute('ALTER TABLE saas_ai_agent_drafts DROP COLUMN version,DROP COLUMN original_draft,DROP COLUMN edited_by,DROP COLUMN edited_at')
    with d.cursor() as cur:review.schema(cur);review.schema(cur)
    after=d.execute('SELECT id,draft,state,version,original_draft,edited_by,edited_at FROM saas_ai_agent_drafts');d.commit()
    assert [{k:r[k] for k in ('id','draft','state')} for r in after]==before
    assert all(r['version']==1 and r['original_draft'] is None and r['edited_by'] is None and r['edited_at'] is None for r in after)




def test_http_contract_edit_send_and_reject(draft):
    import asyncio,json
    from types import SimpleNamespace
    from unittest.mock import Mock
    from tenant_ai_agents_api import handler
    f=draft
    def invoke(resource,method='GET',data=None,origin='https://test.local'):
        async def body():return dict(expected_tenant_id=A,expected_user_id=11,**(data or {}))
        request=SimpleNamespace(method=method,query={},match_info={'draft_id':f['draft_id']},headers={'Origin':origin},content_type='application/json',content_length=256,json=body)
        response=asyncio.run(handler(lambda _:PROFILE,'https://test.local',Mock(),resource)(request))
        return response.status,json.loads(response.text)
    status,result=invoke('draft_detail');assert status==200
    v=result['result']
    assert invoke('draft_send','POST')[0]==409
    assert invoke('draft_review','POST',{'state':'rejected'})[0]==409
    assert invoke('draft_edit','POST',{'draft':'Bad origin','expected_version':1},'https://wrong.local')[0]==403
    status,result=invoke('draft_edit','POST',{'draft':'Edited via HTTP','expected_version':1});assert status==200
    v=result['result'];assert v['version']==2
    status,_=invoke('draft_send','POST',approval(v));assert status==200
    assert invoke('draft_send','POST',approval(v))[0]==409
    assert f['send'].call_count==1 and f['send'].call_args.args[1]=='Edited via HTTP'


def test_reviewed_reply_handoff_keeps_one_ticket(draft):
    from test_tenant_support_inbox import store as inbox
    f=draft;d=f['db'];d.execute('UPDATE saas_ai_agent_drafts SET handoff_after_send=TRUE');d.commit()
    v=review.read(PROFILE,f['draft_id']);assert v['reason']['code']=='handoff'
    r=store.reserve_draft_send(PROFILE,v['id'],approval(v))
    assert transport.send_ai_reply_if_active(A,41,CA,'client',r['draft'],r['id'],r['expected_revision'],draft_review={'profile':PROFILE,**approval(v)},handoff_fingerprint=r['source_fingerprint'])
    store.finish_draft_send(A,v['id'],'accepted');store.finish_draft_send(A,v['id'],'accepted')
    assert len(inbox.listing(PROFILE)['tickets'])==1
    assert d.execute("SELECT state FROM saas_ai_conversation_state WHERE channel_id=%s",(CA,))[0]['state']=='human'
    d.commit();assert f['send'].call_count==1
