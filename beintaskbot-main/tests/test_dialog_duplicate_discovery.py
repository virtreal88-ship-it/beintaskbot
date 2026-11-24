import pytest

import tenant_dialog_duplicates as duplicates
from tenant_linear_policy import TenantLinearError
from test_tenant_support_inbox import A, B, CA, CB, PROFILE, database, db


@pytest.fixture
def exact(db,monkeypatch):
    monkeypatch.setattr(duplicates,'_connect',lambda:db)
    db.execute("INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name) VALUES (%s,%s,'Fixture separate source')",(A,CB))
    rows=[
        (A,CA,501,'441121234567@s.whatsapp.net','Fixture Name','+44 112 1234567','contact'),
        (A,CA,502,'191234567@s.whatsapp.net','Fixture  NAME','+1 91 234567','contact'),
        (A,CA,503,'9991234567@lid','Fixture Name','9991234567','contact'),
        (A,CA,504,'1234567@g.us','Fixture Name','+441234567','group'),
        (B,CB,505,'441121234567@s.whatsapp.net','Fixture Name','+441121234567','contact'),
        (A,CA,506,'3331234567@lid','Different fixture','','contact'),
        (A,CB,507,'441121234567@s.whatsapp.net','Fixture Name','+441121234567','contact'),
    ]
    for tenant,ch,lead,jid,name,phone,kind in rows:
        db.execute("""INSERT INTO saas_connect_conversations
            (tenant_id,connect_channel_id,lead_id,conversation_id,contact_name,phone,conversation_kind,last_message_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,now())""",(tenant,ch,lead,jid,name,phone,kind))
    db.execute("""INSERT INTO saas_connect_direct_identities
        (tenant_id,connect_channel_id,identity_key,canonical_conversation_id) VALUES (%s,%s,%s,%s)""",
        (A,CA,'521121234567@c.us','3331234567@lid'))
    db.commit()
    return db


def ids(**params):
    return {d['lead_id'] for d in duplicates.discover(PROFILE,params)['dialogs']}


def test_default_phone_suffix_is_discovery_only_and_both_uses_and(exact):
    assert ids()=={501,502,506}
    assert ids(mode='name')=={501,502,503}
    assert ids(mode='both')=={501,502}
    assert ids(mode='missing')=={503}
    with pytest.raises(TenantLinearError):ids(mode='arbitrary')


def test_owner_only_and_conflicted_lid_has_no_phone(exact):
    exact.execute("""INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name,permissions)
        VALUES (%s,33,'operator','Operator','["dialogs"]')""",(A,))
    exact.execute("""INSERT INTO saas_connect_identity_conflicts
      (tenant_id,connect_channel_id,conversation_id,event_id,evidence) VALUES (%s,%s,%s,'fixture','{}')""",
      (A,CA,'3331234567@lid'))
    exact.commit()
    assert ids()=={501,502}
    assert ids(mode='missing')=={503,506}
    with pytest.raises(TenantLinearError) as error:
        duplicates.discover({'tenant_id':A,'telegram_id':33},{'mode':'missing'})
    assert error.value.status==403


def test_result_pages_emit_each_route_once_not_cartesian_pairs(exact,monkeypatch):
    monkeypatch.setattr(duplicates,'PAGE_SIZE',2)
    first=duplicates.discover(PROFILE,{'mode':'name'})
    second=duplicates.discover(PROFILE,{'mode':'name','offset':first['next_offset']})
    assert len(first['dialogs'])==2 and len(second['dialogs'])==1
    all_rows=first['dialogs']+second['dialogs']
    assert {r['lead_id'] for r in all_rows}=={501,502,503}
    assert len({r['candidate_group'] for r in all_rows})==1
    assert all(r['group_size']==3 for r in all_rows)
    assert second['next_offset'] is None


def test_manual_number_is_a_separate_discovery_hint_and_exits_missing_queue(exact):
    exact.execute("""CREATE TABLE IF NOT EXISTS saas_connect_dialog_display_phones
        (tenant_id uuid,channel_id uuid,conversation_id text,lead_id bigint,phone text)""")
    exact.execute("""INSERT INTO saas_connect_dialog_display_phones
        (tenant_id,channel_id,conversation_id,lead_id,phone) VALUES (%s,%s,%s,%s,%s)""",
        (A,CA,'9991234567@lid',503,'+441091234567'))
    try:
        exact.commit()
        row=next(x for x in duplicates.discover(PROFILE,{})['dialogs'] if x['lead_id']==503)
        assert row['phone']=='' and row['manual_display_phone']=='+441091234567'
        assert 503 not in ids(mode='missing')
    finally:
        exact.rollback()
        exact.execute("DELETE FROM saas_connect_dialog_display_phones WHERE tenant_id=%s AND lead_id=503",(A,))
        exact.commit()
