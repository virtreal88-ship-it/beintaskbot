import pytest

import tenant_dialog_duplicates as duplicates
from tenant_linear_policy import TenantLinearError
from test_tenant_support_inbox import A, B, CA, CB, PROFILE, database, db


@pytest.fixture
def exact(db,monkeypatch):
    monkeypatch.setattr(duplicates,'_connect',lambda:db)
    rows=[
        (A,CA,501,'441121234567@s.whatsapp.net','Fixture One','+44 112 1234567','contact'),
        (A,CA,502,'191234567@s.whatsapp.net','Fixture Two','+1 91 234567','contact'),
        (A,CA,503,'9991234567@lid','Fixture Missing','9991234567','contact'),
        (A,CA,504,'1234567@g.us','Fixture Group','+441234567','group'),
        (B,CB,505,'441121234567@s.whatsapp.net','Fixture Foreign','+441121234567','contact'),
        (A,CA,506,'3331234567@lid','Fixture Alias','','contact'),
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


def test_phone_suffix_only_uses_allowed_direct_real_phone(exact):
    assert ids(q='+44 (112) 123-4567')=={501,502,506}
    assert ids(mode='name',q='fixture')=={501,502,503,506}
    assert ids(mode='name',q='Fixture_%')==set()
    assert ids(mode='missing')=={503}
    with pytest.raises(TenantLinearError):ids(q='123456')


def test_owner_only_and_conflicted_lid_has_no_phone(exact):
    exact.execute("""INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name,permissions)
        VALUES (%s,33,'operator','Operator','["dialogs"]')""",(A,))
    exact.execute("""INSERT INTO saas_connect_identity_conflicts
      (tenant_id,connect_channel_id,conversation_id,event_id,evidence) VALUES (%s,%s,%s,'fixture','{}')""",
      (A,CA,'3331234567@lid'))
    exact.commit()
    assert ids(q='1234567')=={501,502}
    assert ids(mode='missing')=={503,506}
    with pytest.raises(TenantLinearError) as error:
        duplicates.discover({'tenant_id':A,'telegram_id':33},{'mode':'missing'})
    assert error.value.status==403



def test_result_pages_have_stable_slice_and_bound(exact,monkeypatch):
    monkeypatch.setattr(duplicates,'PAGE_SIZE',2)
    first=duplicates.discover(PROFILE,{'mode':'name','q':'fixture'})
    second=duplicates.discover(PROFILE,{'mode':'name','q':'fixture','offset':first['next_offset']})
    assert len(first['dialogs'])==len(second['dialogs'])==2
    assert {r['lead_id'] for r in first['dialogs']}.isdisjoint(r['lead_id'] for r in second['dialogs'])
    assert second['next_offset'] is None


def test_manual_number_is_a_separate_discovery_hint_and_exits_missing_queue(exact):
    exact.execute("""CREATE TABLE IF NOT EXISTS saas_connect_dialog_display_phones
        (tenant_id uuid,channel_id uuid,conversation_id text,lead_id bigint,phone text)""")
    exact.execute("""INSERT INTO saas_connect_dialog_display_phones
        (tenant_id,channel_id,conversation_id,lead_id,phone) VALUES (%s,%s,%s,%s,%s)""",
        (A,CA,'9991234567@lid',503,'+441091234567'))
    try:
        exact.commit()
        row=next(x for x in duplicates.discover(PROFILE,{'q':'1234567'})['dialogs'] if x['lead_id']==503)
        assert row['phone']=='' and row['manual_display_phone']=='+441091234567'
        assert 503 not in ids(mode='missing')
    finally:
        exact.rollback()
        exact.execute("DELETE FROM saas_connect_dialog_display_phones WHERE tenant_id=%s AND lead_id=503",(A,))
        exact.commit()
