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


def test_both_routes_required_and_original_history_pages_are_preserved(split):
    database,channel=split
    pairs.confirm(PROFILE,channel,41,50)
    pairs.confirm(PROFILE,channel,41,50)
    rows=dialogues.listing(EMPLOYEE,lifecycle='all',combine_history=True)['dialogs']
    assert [row['lead_id'] for row in rows]==[41]
    assert rows[0]['lifecycle']['state']=='dismissed'
    assert len(dialogues.listing(EMPLOYEE,lifecycle='completed',combine_history=True)['dialogs'])==1
    assert dialogues.listing(EMPLOYEE,combine_history=True)['dialogs']==[]
    assert [row['lead_id'] for row in dialogues.listing(PROFILE,lifecycle='all',combine_history=True)['dialogs']]==[50]
    with pytest.raises(TenantLinearError) as denied:
        dialogues.history(PROFILE,'connect',41,channel,PN,combine_history=True)
    assert denied.value.status==404
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


def test_connect_upgrade_installs_pair_table_in_an_existing_schema(split):
    from tenant_connect_transport import _schema

    database,channel=split
    database.execute('DROP TABLE saas_whatsapp_history_pairs')
    database.commit()
    _schema(database)
    database.commit()
    pairs.confirm(PROFILE,channel,41,50)
    assert len(database.execute('SELECT id FROM saas_whatsapp_history_pairs'))==1
