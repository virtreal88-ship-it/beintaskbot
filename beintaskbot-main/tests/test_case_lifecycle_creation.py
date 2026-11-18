"""Explicit support creation in an accepted episode; synthetic DB only."""
import uuid
from unittest.mock import Mock
import pytest
from test_tenant_support_inbox import database, db, A, B, CA, CB, PROFILE
from test_dialog_lifecycle_pglite import episodes, command, payload, state
import tenant_support_inbox_store as store
import tenant_dialog_lifecycle as lifecycle
import tenant_dialog_consultations as consultation
from tenant_linear_policy import TenantLinearError


def create_data(episode, revision=1, **extra):
    return {'request_id':str(uuid.uuid4()),'channel_id':CA,'conversation_id':'client','lead_id':41,
            'episode_id':episode,'expected_revision':revision,'description':'Synthetic request',**extra}


def seed_members(db,episode,total=255):
    ids=[]
    for i in range(total):
        key=str(uuid.uuid4());ids.append(key)
        db.execute('''INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,created_at,happened_at)
          VALUES(%s::uuid,%s::uuid,41,%s,%s,'incoming',%s,clock_timestamp(),clock_timestamp())''',
          (key,A,'connect:SYNTHETIC_EPISODE_'+key,'connect:'+CA,'Cycle message '+str(i)))
        db.execute('''INSERT INTO saas_dialog_episode_messages(tenant_id,episode_id,message_id)
          VALUES(%s::uuid,%s::uuid,%s::uuid)''',(A,episode,key))
    db.commit();return ids


def test_create_freezes_all_episode_members_and_preserves_accepted_state(episodes,monkeypatch):
    accepted=command()['lifecycle'];ids=seed_members(episodes,accepted['id'])
    paid=Mock(side_effect=AssertionError('Must not analyze automatically'))
    monkeypatch.setattr(store,'summarize_created',paid)
    request=create_data(accepted['id'])
    result=store.create(PROFILE,request);ticket=result['ticket']
    assert ticket['dialog_episode_id']==accepted['id']
    assert state(episodes)['state']=='accepted'
    assert state(episodes)['revision']==1
    detail=store.detail(PROFILE,ticket['id'])
    assert detail['scope']['message_count']==255
    assert detail['scope']['initial_limit'] is None
    assert detail['history_context']['episode_id']==accepted['id']
    all_rows=list(detail['messages'])
    while detail['next_before']:
        detail=store.detail(PROFILE,ticket['id'],before=detail['next_before'])
        all_rows=detail['messages']+all_rows
    assert len(all_rows)==len(ids) and {row['id'] for row in all_rows}==set(ids)
    # Later members of the still-open cycle never expand this saved request.
    late=seed_members(episodes,accepted['id'],1)
    assert store.detail(PROFILE,ticket['id'])['scope']['message_count']==255
    assert store.create(PROFILE,request)['ticket']['id']==ticket['id']
    command('close',1)
    assert store.create(PROFILE,request)['ticket']['id']==ticket['id']
    assert not paid.called


@pytest.mark.parametrize('wrong',['stale_revision','wrong_episode','wrong_operator','wrong_tenant','manual_ids','closed'])
def test_creation_revalidates_episode_owner_scope_and_cas(episodes,wrong):
    accepted=command()['lifecycle'];seed_members(episodes,accepted['id'],2)
    request=create_data(accepted['id']);profile=PROFILE
    if wrong=='stale_revision':request['expected_revision']=0
    if wrong=='wrong_episode':request['episode_id']=str(uuid.uuid4())
    if wrong=='wrong_operator':profile={**PROFILE,'telegram_id':33}
    if wrong=='wrong_tenant':profile={'tenant_id':B,'telegram_id':22}
    if wrong=='manual_ids':request['message_ids']=[str(uuid.uuid4())]
    if wrong=='closed':command('close',1)
    with pytest.raises(TenantLinearError):store.create(profile,request)
    assert episodes.execute('SELECT count(*) AS n FROM saas_support_tickets')[0]['n']==0
    episodes.commit()


def test_old_open_case_is_not_reused_for_new_cycle(episodes):
    first=command()['lifecycle'];seed_members(episodes,first['id'],2)
    case=store.create(PROFILE,create_data(first['id']))['ticket']['id']
    command('close',1);second=command('start',2)['lifecycle']
    with pytest.raises(TenantLinearError,match='другого цикла'):
        store.create(PROFILE,create_data(second['id'],3))
    assert store.detail(PROFILE,case)['ticket']['dialog_episode_id']==first['id']
    assert state(episodes)['state']=='accepted'


def test_binding_schema_upgrade_is_additive_and_reads_fail_closed(db,monkeypatch):
    import tenant_ai_agents_schema as schema
    from tenant_support_case_context import load_scope
    with db.cursor() as cur:
        made=store.create_in_transaction(cur,A,CA,'client','Old case','legacy-upgrade',actor=11,source='manual')
    db.commit()
    before=db.execute('SELECT id::text,history_scope,created_at FROM saas_support_tickets');db.commit()
    for name in ('dialog_episode_id','history_started_at','history_frozen_at'):
        db.execute('ALTER TABLE saas_support_tickets DROP COLUMN '+name)
    db.commit()
    with db.cursor() as cur:
        with pytest.raises(TenantLinearError) as blocked:load_scope(cur,A,made['id'])
        assert blocked.value.status==503
    db.commit()
    monkeypatch.setattr(schema,'_ensure_platform_schema',Mock(side_effect=AssertionError('Full bootstrap must not run')))
    schema.ensure_schema(db)
    after=db.execute('SELECT id::text,history_scope,created_at FROM saas_support_tickets')
    assert after==before
    assert db.execute('SELECT dialog_episode_id,history_started_at,history_frozen_at FROM saas_support_tickets')[0]=={
        'dialog_episode_id':None,'history_started_at':None,'history_frozen_at':None}
    db.commit()
    schema.ensure_schema(db)
    assert not schema._ensure_platform_schema.called


def test_legacy_material_addition_cannot_expand_frozen_lifecycle_snapshot(episodes):
    accepted=command()['lifecycle'];ids=seed_members(episodes,accepted['id'],2)
    ticket=store.create(PROFILE,create_data(accepted['id']))['ticket']['id']
    late=seed_members(episodes,accepted['id'],1)[0]
    # Even a persisted legacy addition event cannot redefine a new episode.
    episodes.execute('''INSERT INTO saas_support_ticket_messages(tenant_id,ticket_id,message_id,event_key)
      VALUES(%s::uuid,%s::uuid,%s::uuid,'support-material-picker:legacy')''',(A,ticket,late))
    episodes.commit()
    detail=store.detail(PROFILE,ticket)
    assert {row['id'] for row in detail['messages']}==set(ids)
    assert detail['scope']['message_count']==2
