from uuid import uuid4

import pytest

import tenant_operator_dialog_removal as removal
import tenant_operator_dialogs as inbox
from tenant_linear_policy import TenantLinearError
from test_tenant_support_inbox import A, B, CA, CB, PROFILE, database, db


def test_remove_is_exact_to_channel_and_new_inbound_restores_the_dialog(db, monkeypatch):
    monkeypatch.setattr(removal, '_connect', lambda: db)
    monkeypatch.setattr(inbox, '_connect', lambda: db)
    secondary = '30000000-0000-4000-8000-000000000001'
    db.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s,%s,'Second')", (A, secondary))
    db.execute("""INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id)
        VALUES(%s,%s,'client',43)""", (A, secondary))
    db.execute("INSERT INTO saas_tenant_members(tenant_id,telegram_id,role) VALUES(%s,33,'admin')", (A,))
    db.commit()
    command = dict(provider='connect', channel_id=CA, conversation_id='client', lead_id=41,
                   confirmed=True, request_id=str(uuid4()))
    visible = lambda profile=PROFILE: {r['lead_id'] for r in inbox.listing(profile, lifecycle='all')['dialogs']}
    assert visible() == {41, 43}

    for actor, payload in (
        ({'tenant_id':A,'telegram_id':33,'role':'owner'}, command),
        ({'tenant_id':B,'telegram_id':22}, command),
        (PROFILE, {**command,'channel_id':secondary}),
        (PROFILE, {**command,'confirmed':False}),
    ):
        with pytest.raises(TenantLinearError):
            removal.remove(actor, payload)

    assert removal.remove(PROFILE, command)['removed'] is True
    assert visible() == {43}
    assert visible({'tenant_id':B, 'telegram_id':22}) == {42}
    assert {r['body'] for r in inbox.history(PROFILE, 'connect', 41, CA, 'client')['messages']} == {'Internet is down'}

    def message(external_id, direction, happened_at):
        db.execute("""INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,happened_at)
            VALUES(%s,%s,41,%s,%s,%s,%s)""", (str(uuid4()), A, external_id, 'connect:'+CA, direction, happened_at))
        db.commit()

    message('outgoing-after-removal', 'outgoing', None)
    message('late-history', 'incoming', '2020-01-01Z')
    assert visible() == {43}
    message('genuine-inbound', 'incoming', None)
    assert visible() == {41, 43}
    assert removal.remove(PROFILE, command)['removed'] is True
    assert visible() == {41, 43}
    assert db.execute("SELECT count(*) AS total FROM saas_tenant_audit_events WHERE action='dialog_removed'") == [{'total':1}]


def test_group_removal_keeps_other_source_in_folded_list(db, monkeypatch):
    monkeypatch.setattr(removal, '_connect', lambda: db)
    monkeypatch.setattr(inbox, '_connect', lambda: db)
    secondary = '30000000-0000-4000-8000-000000000002'
    group = '120363411822115409@g.us'
    db.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES(%s,%s,'Second')", (A, secondary))
    for channel, lead in ((CA, 51), (secondary, 52)):
        db.execute("""INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,
            conversation_id,lead_id,conversation_kind,display_name)
            VALUES(%s,%s,%s,%s,'group','Synthetic group')""", (A, channel, group, lead))
    db.commit()
    removal.remove(PROFILE, dict(provider='connect', channel_id=CA, conversation_id=group, lead_id=51,
                                confirmed=True, request_id=str(uuid4())))
    cards = inbox.listing(PROFILE, search='Synthetic group', lifecycle='all')['dialogs']
    assert [card['channel_id'] for card in cards] == [secondary]
    assert db.execute("SELECT count(*) AS total FROM saas_connect_conversations WHERE conversation_id=%s", (group,)) == [{'total':2}]
