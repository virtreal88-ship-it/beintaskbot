"""Only independently certified avatar timestamp drift may relax exact rollback."""
import asyncio
import pytest
from copy import deepcopy
from test_connect_dependent_reconcile import seed,snapshot,request,A,CA,JID,LEADS,PROFILE,db,database
import tenant_connect_direct_reconcile as r
import tenant_connect_reconcile_dependencies as d
from tenant_linear_policy import TenantLinearError


def merged(db,monkeypatch):
    seed(db,monkeypatch)
    db.execute("UPDATE saas_connect_conversations SET avatar_status='available',avatar_checked_at=now() WHERE tenant_id=%s::uuid AND lead_id=8101",(A,));db.commit()
    before=snapshot(db);r.apply(PROFILE,request());after=snapshot(db)
    return before,after


def touch(db):
    db.execute("UPDATE saas_connect_conversations SET updated_at=updated_at+interval '1 second' WHERE tenant_id=%s::uuid AND lead_id=8101",(A,));db.commit()
    return snapshot(db)


def certify(db,after,current,**overrides):
    old=after['routes'][0];new=current['routes'][0]
    evidence={'source':'authenticated_railway_avatar_http','deployment_id':'00000000-0000-4000-8000-000000000001','request_time':new['updated_at'],'http_status':200,'http_method':'GET','request_path':'/api/platform/ai/dialogs/avatar','channel_id':CA,'lead_id':8101,**overrides}
    with db.cursor() as cur:d.certify_avatar_timestamp_touch(cur,A,11,old,new,evidence)
    db.commit()


def test_verified_touch_dry_run_and_rollback_keep_newer_timestamp(db,monkeypatch):
    before,after=merged(db,monkeypatch);current=touch(db)
    with pytest.raises(TenantLinearError,match='timestamp_unproven'):r.rollback_preflight(PROFILE,request()['pair_id'])
    certify(db,after,current);certify(db,after,current)
    one=r.rollback_preflight(PROFILE,request()['pair_id'])
    assert one==r.rollback_preflight(PROFILE,request()['pair_id']) and one['eligible']
    assert len(one['preserved_timestamp_touches'])==1
    assert snapshot(db)==current
    payload=request('rollback');result=r.apply(PROFILE,payload)
    expected=deepcopy(before)
    next(row for row in expected['routes'] if row['lead_id']==8101)['updated_at']=current['routes'][0]['updated_at']
    assert snapshot(db)==expected
    assert r.apply(PROFILE,payload)['idempotent_replay']
    assert result['preserved_timestamp_touches']==one['preserved_timestamp_touches']


@pytest.mark.parametrize('change',[
    "UPDATE saas_connect_conversations SET manual_hold=true WHERE lead_id=8101",
    "UPDATE saas_connect_conversations SET contact_name='Changed' WHERE lead_id=8101",
    "UPDATE saas_connect_conversations SET ai_enabled=false WHERE lead_id=8101",
    "UPDATE saas_ai_conversation_state SET reason='new reason' WHERE conversation_id='"+JID+"'",
    "UPDATE saas_operator_dialog_reads SET last_read_at=last_read_at+interval '1 minute' WHERE lead_id=8101",
    "UPDATE saas_ai_agent_jobs SET attempts=attempts+1 WHERE lead_id=8101",
    "UPDATE saas_ai_agent_drafts SET draft='New employee edit' WHERE lead_id=8101",
    "UPDATE saas_crm_messages SET body='Edited message' WHERE kommo_lead_id=8101",
])
def test_timestamp_proof_never_masks_other_changes(db,monkeypatch,change):
    _,after=merged(db,monkeypatch);current=touch(db);certify(db,after,current)
    db.execute(change);db.commit();changed=snapshot(db)
    with pytest.raises(TenantLinearError,match='state_changed'):r.rollback_preflight(PROFILE,request()['pair_id'])
    with pytest.raises(TenantLinearError,match='state_changed'):r.apply(PROFILE,request('rollback'))
    assert snapshot(db)==changed


def test_second_unverified_touch_and_bad_provenance_are_blocked(db,monkeypatch):
    _,after=merged(db,monkeypatch);current=touch(db)
    with pytest.raises(TenantLinearError):certify(db,after,current,source='unknown')
    db.rollback()
    certify(db,after,current);touch(db)
    with pytest.raises(TenantLinearError,match='timestamp_unproven'):r.rollback_preflight(PROFILE,request()['pair_id'])


def test_avatar_success_read_does_not_touch_existing_available_route(db,monkeypatch):
    import tenant_connect_avatars as avatars
    merged(db,monkeypatch);before=snapshot(db)
    monkeypatch.setattr(avatars,'_connect',lambda:db)
    monkeypatch.setattr(avatars,'connect_route',lambda *a:{'channel_id':CA,'conversation_id':JID,'api_key':'synthetic'})
    async def fetch(*a):return b'png','image/png'
    monkeypatch.setattr(avatars,'_fetch',fetch)
    assert asyncio.run(avatars.get_avatar(PROFILE,{'provider':'connect','channel_id':CA,'conversation_id':JID,'lead_id':8101}))==(b'png','image/png')
    assert snapshot(db)==before
