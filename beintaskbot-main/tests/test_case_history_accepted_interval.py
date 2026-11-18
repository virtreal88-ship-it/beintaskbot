"""Synthetic case UI/share interval and recipient fanout, no live sends."""
import uuid
import pytest
from test_dialog_consultations import database, db, ready, A, B, CA, CB, PROFILE, flow, TARGET
from test_consultation_episode import case_for
from tenant_linear_policy import TenantLinearError
from tenant_support_case_context import load_scope, history_projection, messages, selection
import tenant_support_inbox_store as store


def seed_interval(ready, *, episode=True, total=55):
    local, data, sender = ready
    local.execute('DELETE FROM saas_crm_messages WHERE tenant_id=%s::uuid', (A,))
    keys = [str(uuid.UUID(int=5000+i)) for i in range(total)]
    for i, key in enumerate(keys):
        local.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,
          direction,channel,body,message_type,happened_at,created_at)
          VALUES(%s::uuid,%s::uuid,41,%s,'incoming',%s,%s,'text',
            '2026-11-01T12:00:00Z'::timestamptz+%s*interval '1 second',
            '2026-11-01T12:00:00Z'::timestamptz+%s*interval '1 second')''',
          (key,A,'connect:INTERVAL_'+str(i),'connect:'+CA,'Message '+str(i),i,i))
    data.update(message_ids=keys,media_ids=[])
    case = case_for(local, data)
    local.execute("UPDATE saas_support_tickets SET created_at='2026-11-01T12:00:52Z' WHERE id=%s::uuid", (case,))
    if episode:
        from tenant_dialog_lifecycle import migrate
        with local.cursor() as cur:migrate(cur)
        local.execute('''INSERT INTO saas_dialog_episodes(tenant_id,id,channel_id,conversation_id,
          lead_id,accepted_by,operator_id,accepted_at)
          VALUES(%s::uuid,%s::uuid,%s::uuid,'client',41,11,11,'2026-11-01T12:00:03Z')''',
          (A,uuid.uuid4(),CA))
    local.commit()
    return case, keys


def test_exact_acceptance_to_creation_matches_preview_and_case_detail(ready):
    local,data,sender=ready;case,keys=seed_interval(ready)
    # A later resync with old provider time must not expand a frozen case.
    local.execute("UPDATE saas_crm_messages SET created_at='2026-11-02' WHERE id=%s::uuid",(keys[20],));local.commit()
    detail=store.detail(PROFILE,case)
    assert detail['history_context']['boundary_status']=='accepted_interval'
    expected=keys[3:20]+keys[21:53]
    assert [m['id'] for m in detail['messages']]==expected
    preview=flow.preview(PROFILE,{'channel_id':CA,'conversation_id':'client','case_id':case})
    assert [m['id'] for m in preview['messages']]==expected
    assert preview['scope']['history_context']==detail['history_context']
    with local.cursor() as cur:
        assert [r['id'] for r in selection(cur,A,case,CA,'client',expected)]==expected
        with pytest.raises(TenantLinearError):selection(cur,A,case,CA,'client',keys[:2])
    local.rollback()
    assert not sender.called


def test_missing_acceptance_is_explicit_and_never_adds_future_messages(ready):
    local,data,sender=ready;case,keys=seed_interval(ready,episode=False)
    detail=store.detail(PROFILE,case)
    assert detail['history_context']['boundary_status']=='boundary_unknown'
    assert detail['history_context']['accepted_at'] is None
    # 53 saved rows before creation: first page is the newest 50.
    assert [m['id'] for m in detail['messages']]==keys[3:53]
    earlier=store.detail(PROFILE,case,before=detail['next_before'])
    assert [m['id'] for m in earlier['messages']]==keys[:3]
    with pytest.raises(TenantLinearError):store.detail({'tenant_id':B,'telegram_id':22},case)
    assert not sender.called


def test_fifty_public_messages_allowed_fifty_one_rejected(ready):
    local,data,sender=ready;case,keys=seed_interval(ready)
    chosen=keys[3:53]
    request={**data,'case_id':case,'message_ids':chosen}
    first=flow.prepare(PROFILE,request,'https://crm.test')
    assert first['created']
    assert flow.prepare(PROFILE,request,'https://crm.test')['id']==first['id']
    with pytest.raises(TenantLinearError):
        flow.prepare(PROFILE,{**request,'request_id':str(uuid.uuid4()),'message_ids':keys[2:53]},'https://crm.test')
    assert not sender.called


def test_two_explicit_case_recipients_are_independent_but_same_target_is_fenced(ready):
    local,data,sender=ready;case=case_for(local,data)
    other='120363499999999999@g.us'
    local.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,contact_name) VALUES(%s::uuid,%s::uuid,%s,199,'Other developer')",(A,CA,other));local.commit()
    flow.developer_marker(PROFILE,{'channel_id':CA,'conversation_id':other,'enabled':True})
    a={**data,'case_id':case};first=flow.prepare(PROFILE,a,'https://crm.test')
    local.execute("UPDATE saas_consultation_group_deliveries SET state='unknown' WHERE consultation_id=%s::uuid",(first['id'],));local.commit()
    b={**a,'request_id':str(uuid.uuid4()),'destination_conversation_id':other}
    second=flow.prepare(PROFILE,b,'https://crm.test')
    assert second['id']!=first['id']
    assert flow.prepare(PROFILE,b,'https://crm.test')['id']==second['id']
    with pytest.raises(TenantLinearError):
        flow.prepare(PROFILE,{**a,'request_id':str(uuid.uuid4())},'https://crm.test')
    assert local.execute('SELECT count(*) AS n FROM saas_internal_consultations')[0]['n']==2
    assert not sender.called


def test_new_case_pins_full_accepted_interval_not_last_fifty(ready):
    local,data,sender=ready;old,keys=seed_interval(ready,total=75)
    local.execute('DELETE FROM saas_support_tickets WHERE tenant_id=%s::uuid AND id=%s::uuid',(A,old))
    with local.cursor() as cur:
        receipt=store.create_in_transaction(cur,A,CA,'client','Synthetic issue','manual:synthetic-interval',
                                            actor=11,source='manual',description='Synthetic')
    local.commit()
    rows=local.execute('SELECT message_id::text FROM saas_support_ticket_messages WHERE tenant_id=%s::uuid AND ticket_id=%s::uuid',(A,receipt['id']))
    assert {row['message_id'] for row in rows}==set(keys[3:])
    local.commit()
    page=store.detail(PROFILE,receipt['id']);older=store.detail(PROFILE,receipt['id'],before=page['next_before'])
    assert [m['id'] for m in older['messages']+page['messages']]==keys[3:]
    assert not sender.called


@pytest.mark.parametrize('problem',['closed_before_creation','overlapping_episodes'])
def test_old_or_ambiguous_episode_cannot_redefine_case_history(ready,problem):
    local,data,sender=ready;case,keys=seed_interval(ready)
    if problem=='closed_before_creation':
        local.execute("UPDATE saas_dialog_episodes SET closed_at='2026-11-01T12:00:40Z' WHERE tenant_id=%s::uuid",(A,))
    else:
        local.execute('''INSERT INTO saas_dialog_episodes(tenant_id,id,channel_id,conversation_id,
          lead_id,accepted_by,operator_id,accepted_at)
          VALUES(%s::uuid,%s::uuid,%s::uuid,'client',41,11,11,'2026-11-01T12:00:04Z')''',
          (A,uuid.uuid4(),CA))
    local.commit()
    detail=store.detail(PROFILE,case)
    assert detail['history_context']['boundary_status']=='boundary_unknown'
    assert detail['history_context']['accepted_at'] is None
    assert [m['id'] for m in detail['messages']]==keys[3:53]
    assert not sender.called


def test_historical_selected_membership_cannot_expand_from_acceptance(ready):
    local,data,sender=ready;case,keys=seed_interval(ready)
    local.execute('DELETE FROM saas_support_ticket_messages WHERE tenant_id=%s::uuid AND ticket_id=%s::uuid AND message_id<>%s::uuid',(A,case,keys[8]));local.commit()
    detail=store.detail(PROFILE,case)
    assert detail['history_context']['boundary_status']=='accepted_interval'
    assert [m['id'] for m in detail['messages']]==[keys[8]]
    assert [m['id'] for m in flow.preview(PROFILE,{'channel_id':CA,'conversation_id':'client','case_id':case})['messages']]==[keys[8]]
    assert not sender.called


def automatic_request(ready,case):
    _,data,_=ready
    shown=flow.preview(PROFILE,{'channel_id':CA,'conversation_id':'client','case_id':case})
    return {key:value for key,value in {**data,'case_id':case,'automatic_case_history':True,
        'case_history_hash':shown['case_history_hash'],'public_header_version':1,
        'public_header_hash':shown['public_header_hash'],'public_dialog_version':1,
        'include_customer_identity':False}.items() if key not in {'message_ids','media_ids'}},shown


@pytest.mark.parametrize('total',[75,255])
def test_automatic_case_snapshot_has_all_rows_beyond_preview_and_manual_limits(ready,total):
    local,data,sender=ready;case,keys=seed_interval(ready,total=total)
    local.execute("UPDATE saas_support_tickets SET created_at='2026-11-01T12:00:00Z'::timestamptz+%s*interval '1 second' WHERE id=%s::uuid",(total,case))
    local.execute("UPDATE saas_crm_messages SET message_type='image' WHERE id=%s::uuid",(keys[10],));local.commit()
    request,shown=automatic_request(ready,case)
    assert shown['scope']['message_count']==total-3
    assert len(shown['messages'])==min(total-3,80)
    assert 'message_ids' not in request
    made=flow.prepare(PROFILE,request,'https://crm.test')
    share=local.execute('SELECT selected_message_ids,selected_media_ids,public_messages FROM saas_public_case_shares WHERE consultation_id=%s::uuid',(made['id'],))[0]
    assert share['selected_message_ids']==keys[3:]
    assert [row['id'] for row in share['public_messages']]==keys[3:]
    assert share['selected_media_ids']==[keys[10]]
    local.commit()
    assert flow.prepare(PROFILE,request,'https://crm.test')['id']==made['id']
    assert not sender.called


@pytest.mark.parametrize('change',['body','author_name'])
def test_hidden_preview_rows_are_bound_into_automatic_hash(ready,change):
    local,data,sender=ready;case,keys=seed_interval(ready,total=255)
    local.execute("UPDATE saas_support_tickets SET created_at='2026-11-01T12:05:00Z' WHERE id=%s::uuid",(case,));local.commit()
    request,shown=automatic_request(ready,case)
    assert keys[10] not in [row['id'] for row in shown['messages']]
    local.execute('UPDATE saas_crm_messages SET '+change+'=%s WHERE id=%s::uuid',('Changed hidden source',keys[10]));local.commit()
    with pytest.raises(TenantLinearError,match='История обращения изменилась'):
        flow.prepare(PROFILE,request,'https://crm.test')
    assert not sender.called


def test_automatic_case_requires_exact_case_hash_and_disallows_client_range(ready):
    local,data,sender=ready;case,keys=seed_interval(ready)
    request,_=automatic_request(ready,case)
    for changed in ({'case_id':None},{'message_ids':[keys[4]]},{'case_history_hash':'0'*64},
                    {'media_ids':[keys[4]]},{'public_header_hash':'0'*64}):
        with pytest.raises(TenantLinearError):flow.prepare(PROFILE,{**request,**changed},'https://crm.test')
    with pytest.raises(TenantLinearError):flow.prepare({'tenant_id':B,'telegram_id':22},request,'https://crm.test')
    assert not sender.called


def test_unknown_legacy_boundary_uses_local_admission_not_provider_clock(ready):
    local,data,sender=ready;case,keys=seed_interval(ready,episode=False)
    local.execute("UPDATE saas_crm_messages SET happened_at='2030-01-01' WHERE id=%s::uuid",(keys[8],))
    local.commit()
    detail=store.detail(PROFILE,case)
    assert keys[8] in {row['id'] for row in detail['messages']}
    assert not set(keys[53:]) & {row['id'] for row in detail['messages']}
    request,shown=automatic_request(ready,case)
    made=flow.prepare(PROFILE,request,'https://crm.test')
    shared=local.execute('SELECT selected_message_ids FROM saas_public_case_shares WHERE consultation_id=%s::uuid',(made['id'],))[0]
    assert set(shared['selected_message_ids'])==set(keys[:53])
    assert not sender.called
