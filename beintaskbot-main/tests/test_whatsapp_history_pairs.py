import json
import uuid

import pytest

from test_contact_phone import active, setup, staged_source, managed, gateway, pilot, database, db
from test_tenant_support_inbox import A, B, PROFILE
import tenant_contact_phone
import tenant_dialog_lifecycle as lifecycle
import tenant_operator_dialogs as dialogues
import tenant_platform
import tenant_whatsapp_history_pairs as pairs
from tenant_linear_policy import TenantLinearError

PN = '994100000102@s.whatsapp.net'
LID = '111111111111111@lid'
EMPLOYEE = {'tenant_id': A, 'telegram_id': 33}


@pytest.fixture
def split(active, monkeypatch):
    database, channel, _, _, _ = active
    monkeypatch.setenv('BEINSYSTEMS_OPERATOR_TENANT_ID', A)
    monkeypatch.setattr(tenant_platform, '_connect', lambda: database)
    monkeypatch.setattr(dialogues, '_connect', lambda: database)
    database.execute("""UPDATE saas_connect_conversations SET connect_channel_id=%s,conversation_id=%s,
        conversation_kind='contact' WHERE tenant_id=%s AND lead_id=41""",(channel,PN,A))
    database.execute('UPDATE saas_crm_messages SET channel=%s WHERE tenant_id=%s AND kommo_lead_id=41',
                     ('connect:'+channel,A))
    database.execute("""INSERT INTO saas_connect_conversations
        (tenant_id,connect_channel_id,lead_id,conversation_id,conversation_kind) VALUES(%s,%s,50,%s,'contact')""",
        (A,channel,LID))
    database.execute("""INSERT INTO saas_crm_messages
        (id,tenant_id,kommo_lead_id,external_id,channel,direction,body,happened_at)
        SELECT gen_random_uuid(),%s,50,'synthetic-'||n,%s,'outgoing','Synthetic message',
          '2026-11-12 09:00+00'::timestamptz - n * interval '1 second' FROM generate_series(1,52) n""",
        (A,'connect:'+channel))
    database.execute("""INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name,permissions)
        VALUES(%s,33,'operator','Employee','["dialogs","support"]'::jsonb)""",(A,))
    with database.cursor() as cur:
        lifecycle.migrate(cur)
        from tenant_dialog_rollout import transition
        transition(cur,A,mode='active',admission_enabled=True,expected_generation=0)
        cur.execute("""INSERT INTO saas_dialog_workflows
          (tenant_id,channel_id,conversation_id,id,state,operator_id,revision,closed_at)
          VALUES(%s,%s,%s,%s,'dismissed',33,1,now())""",(A,channel,PN,str(uuid.uuid4())))
    database.commit()

    async def device(route):
        assert route['conversation_id']==LID
        assert route['native']['scope']['source']==channel
        return {'phone':'+994100000102','display_name':'Synthetic'}

    monkeypatch.setattr(tenant_contact_phone,'fetch_contact',device)
    return database,channel


@pytest.mark.parametrize('outcome',['closed','dismissed'])
def test_both_routes_required_and_original_history_pages_are_preserved(split,outcome):
    database,channel=split
    database.execute("UPDATE saas_dialog_workflows SET state=%s WHERE tenant_id=%s AND conversation_id=%s",
                     (outcome,A,PN))
    database.commit()
    pairs.confirm(PROFILE,channel,41,50)
    pairs.confirm(PROFILE,channel,41,50)
    rows=dialogues.listing(EMPLOYEE,lifecycle='all',combine_history=True)['dialogs']
    assert [row['lead_id'] for row in rows]==[41]
    assert rows[0]['lifecycle']['state']==outcome
    assert len(dialogues.listing(EMPLOYEE,lifecycle='completed',combine_history=True)['dialogs'])==1
    assert dialogues.listing(EMPLOYEE,combine_history=True)['dialogs']==[]
    assert [row['lead_id'] for row in dialogues.listing(PROFILE,lifecycle='all',combine_history=True)['dialogs']]==[50]
    archive=dialogues.history(PROFILE,'connect',41,channel,PN,combine_history=True,redirect_paired_history=True)
    assert archive['dialog']['archive_read_only'] and not archive['dialog']['can_send']
    assert archive['dialog']['lifecycle']['state']==outcome
    assert 'history_pair_id' not in archive['dialog'] and 'combined_dialog' not in archive
    assert archive['read_through_message_id'] is None
    assert [(m['source_lead_id'],m['source_channel_id'],m['source_conversation_id'])
            for m in archive['messages']]==[(41,channel,PN)]
    with pytest.raises(TenantLinearError):
        dialogues.history(PROFILE,'connect',41,str(uuid.uuid4()),PN,combine_history=True)
    with pytest.raises(TenantLinearError):
        dialogues.history({'tenant_id':B,'telegram_id':22},'connect',41,channel,PN,combine_history=True)
    page=dialogues.history(EMPLOYEE,'connect',41,channel,PN,combine_history=True)
    earlier=dialogues.history(EMPLOYEE,'connect',41,channel,PN,before=page['next_before'],combine_history=True)
    assert page['dialog']['history_pair_id']==earlier['dialog']['history_pair_id']
    assert earlier['next_before'] is None
    messages=earlier['messages']+page['messages']
    assert len({m['id'] for m in messages})==53
    assert {(m['direction'],m['source_lead_id'],m['source_conversation_id']) for m in messages}=={
        ('incoming',41,PN),('outgoing',50,LID)}
    assert page['read_through_message_id']==next(m['id'] for m in messages if m['direction']=='incoming')
    assert page['dialog']['can_send'] is False
    assert len(dialogues.history(EMPLOYEE,'connect',41,channel,PN)['messages'])==1


@pytest.mark.parametrize('reason', ['generation','alternate_owner','contradiction'])
def test_unsafe_overlay_is_removed_from_both_reads(split,reason):
    database,channel=split
    pairs.confirm(PROFILE,channel,41,50)
    if reason=='generation':
        database.execute("UPDATE saas_whatsapp_history_pairs SET generation='stale'",())
    elif reason=='alternate_owner':
        database.execute("""INSERT INTO saas_dialog_workflows
          (tenant_id,channel_id,conversation_id,id,state,operator_id,revision,closed_at)
          VALUES(%s,%s,%s,%s,'dismissed',11,1,now())""",(A,channel,LID,str(uuid.uuid4())))
    else:
        database.execute("""INSERT INTO saas_connect_identity_conflict_events
          (tenant_id,connect_channel_id,conversation_id,event_id,evidence) VALUES(%s,%s,%s,'synthetic','{}')""",
          (A,channel,LID))
    database.commit()
    page=dialogues.history(EMPLOYEE,'connect',41,channel,PN,combine_history=True)
    assert 'history_pair_id' not in page['dialog']
    assert [(m['source_lead_id'],m['direction']) for m in page['messages']]==[(41,'incoming')]


def test_proof_mismatch_or_different_source_cannot_register(split,monkeypatch):
    database,channel=split
    async def wrong_device(_):
        return {'phone': '+994100000103'}

    monkeypatch.setattr(tenant_contact_phone,'fetch_contact',wrong_device)
    with pytest.raises(TenantLinearError,match='WeCall'):
        pairs.confirm(PROFILE,channel,41,50)
    with pytest.raises(TenantLinearError):
        pairs.confirm(PROFILE,channel,41,42)
    assert database.execute('SELECT * FROM saas_whatsapp_history_pairs')==[]


def test_archive_attachment_requires_explicit_chat_scope_and_current_lifecycle(split,monkeypatch):
    import tenant_chat_attachment_store as attachments
    import tenant_connect_transport

    database,channel=split
    message='connect:'+str(uuid.uuid4())
    database.execute("""INSERT INTO saas_crm_messages
      (id,tenant_id,kommo_lead_id,external_id,channel,direction,message_type,body)
      VALUES(gen_random_uuid(),%s,41,%s,%s,'incoming','document','Synthetic attachment')""",
      (A,message,'connect:'+channel))
    database.commit()
    monkeypatch.setattr(attachments,'_connect',lambda:database)
    monkeypatch.setattr(tenant_connect_transport,'connect_route',
        lambda *args,**kwargs:{'channel_id':channel,'conversation_id':PN,'api_key':'synthetic'})
    exact={'channel_id':channel,'conversation_id':PN}
    with pytest.raises(TenantLinearError):
        attachments.load(PROFILE,41,message,**exact)
    for wrong in ({}, {**exact,'channel_id':str(uuid.uuid4())}, {**exact,'conversation_id':LID}):
        with pytest.raises(TenantLinearError):
            attachments.load(PROFILE,41,message,allow_closed_archive=True,**wrong)
    item=attachments.load(PROFILE,41,message,allow_closed_archive=True,**exact)
    assert item['row']['external_id']==message
    database.execute("""UPDATE saas_dialog_workflows SET state='accepted'
      WHERE tenant_id=%s AND channel_id=%s AND conversation_id=%s""",(A,channel,PN))
    database.commit()
    with pytest.raises(TenantLinearError):
        attachments.load(PROFILE,41,message,allow_closed_archive=True,**exact)


def test_connect_upgrade_installs_pair_table_in_an_existing_schema(split):
    from tenant_connect_transport import _schema

    database,channel=split
    database.execute('DROP TABLE saas_whatsapp_history_pairs')
    database.commit()
    _schema(database)
    database.commit()
    pairs.confirm(PROFILE,channel,41,50)
    assert len(database.execute('SELECT id FROM saas_whatsapp_history_pairs'))==1
    database.execute('ALTER TABLE saas_whatsapp_history_pairs DROP COLUMN lid_is_primary')
    database.commit()
    _schema(database)
    database.commit()
    assert database.execute('SELECT lid_is_primary FROM saas_whatsapp_history_pairs')==[{'lid_is_primary':False}]


def test_lid_can_preserve_the_workflow_and_include_outgoing_phone_history(split):
    database,channel=split
    database.execute("""UPDATE saas_crm_messages SET kommo_lead_id=CASE kommo_lead_id WHEN 41 THEN 50 ELSE 41 END
        WHERE tenant_id=%s AND kommo_lead_id IN (41,50)""",(A,))
    database.execute("""UPDATE saas_dialog_workflows SET conversation_id=%s
        WHERE tenant_id=%s AND channel_id=%s AND conversation_id=%s""",(LID,A,channel,PN))
    database.commit()
    pairs.confirm(PROFILE,channel,50,41)
    cards=dialogues.listing(EMPLOYEE,lifecycle='all',combine_history=True)['dialogs']
    assert [(c['lead_id'],c['lifecycle']['state']) for c in cards]==[(50,'dismissed')]
    for needle in ('994100000102','0000102',LID):
        found=dialogues.listing(EMPLOYEE,search=needle,lifecycle='completed',combine_history=True)
        assert found['total_count']==1 and [c['lead_id'] for c in found['dialogs']]==[50]
        shared=dialogues.listing(PROFILE,search=needle,lifecycle='completed',combine_history=True)['dialogs']
        if needle==LID:
            assert [(c['lead_id'],c['archive_read_only'],c['lifecycle']['state']) for c in shared]==[(50,True,'dismissed')]
        else:
            assert not shared, 'a companion-only phone search must not select an archive card'
    assert not dialogues.listing(EMPLOYEE,search='0000102',lifecycle='active',combine_history=True)['dialogs']
    assert not dialogues.listing(EMPLOYEE,search='0000102',channel='connect:'+B,lifecycle='all',combine_history=True)['dialogs']
    archived=dialogues.history(PROFILE,'connect',50,channel,LID,combine_history=True)
    assert archived['dialog']['archive_read_only'] and not archived['dialog']['can_send']
    assert 'history_pair_id' not in archived['dialog'] and archived['read_through_message_id'] is None
    assert [(m['direction'],m['source_lead_id'],m['source_conversation_id']) for m in archived['messages']]==[
        ('incoming',50,LID)]
    page=dialogues.history(EMPLOYEE,'connect',50,channel,LID,combine_history=True)
    assert page['dialog']['history_pair_id']
    assert page['dialog']['can_send'] is False
    assert {(m['direction'],m['source_lead_id'],m['source_conversation_id']) for m in page['messages']}=={
        ('incoming',50,LID),('outgoing',41,PN)}
    legacy=dialogues.history(EMPLOYEE,'connect',41,channel,PN,combine_history=True)
    assert 'combined_dialog' not in legacy and {m['direction'] for m in legacy['messages']}=={'outgoing'}
    combined=dialogues.history(EMPLOYEE,'connect',41,channel,PN,combine_history=True,redirect_paired_history=True)
    assert combined == {'combined_dialog':{'provider':'connect','channel_id':channel,
        'conversation_id':LID,'lead_id':50}}
    assert 'combined_dialog' not in dialogues.history(PROFILE,'connect',41,channel,PN,combine_history=True,redirect_paired_history=True)
    assert 'combined_dialog' not in dialogues.history(EMPLOYEE,'connect',41,channel,PN,support_case_only=True,combine_history=True,redirect_paired_history=True)


@pytest.mark.parametrize('surrounding_proof', [True, False])
def test_old_self_only_receipt_needs_matching_provider_history_and_live_device(split,surrounding_proof):
    database,channel=split
    confirmed={
        'originalConversationId':PN, 'canonicalConversationId':PN,
        'directIdentity':{'jid':PN,'lid':LID}, 'pair_matches_conversation':True,
    }
    incomplete={
        'originalConversationId':LID, 'canonicalConversationId':LID,
        'directIdentity':{'jid':'','lid':''}, 'pair_matches_conversation':False,
    }

    def evidence(event, data, age):
        database.execute("""INSERT INTO saas_connect_identity_conflict_events
          (tenant_id,connect_channel_id,conversation_id,event_id,evidence,created_at)
          VALUES(%s,%s,%s,%s,%s::jsonb,now()-%s*interval '1 day')""",
          (A,channel,LID,event,json.dumps(data),age))
        database.commit()

    evidence('before',confirmed,3)
    evidence('incomplete',incomplete,2)
    if not surrounding_proof:
        with pytest.raises(TenantLinearError):
            pairs.confirm(PROFILE,channel,41,50)
        assert not database.execute('SELECT id FROM saas_whatsapp_history_pairs')
        return
    evidence('after',confirmed,1)
    pairs.confirm(PROFILE,channel,41,50)
    page=dialogues.history(EMPLOYEE,'connect',41,channel,PN,combine_history=True)
    assert page['dialog']['history_pair_id']
    assert {m['direction'] for m in page['messages']}=={'incoming','outgoing'}

    evidence('contradiction',{**confirmed,'directIdentity':{'jid':'994100000103@s.whatsapp.net','lid':LID}},1)
    page=dialogues.history(EMPLOYEE,'connect',41,channel,PN,combine_history=True)
    assert 'history_pair_id' not in page['dialog']
    assert {m['direction'] for m in page['messages']}=={'incoming'}
