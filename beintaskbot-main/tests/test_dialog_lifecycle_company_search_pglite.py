"""Exact contextual company matches by the real dialogue listing SQL."""
import json
import uuid

import tenant_dialog_company_binding as binding
import tenant_operator_dialogs as dialogs
from test_dialog_lifecycle_company_completion import STAFF, companies
from test_dialog_lifecycle_pglite import command, episodes
from test_tenant_support_inbox import A, B, CA, CB, PROFILE, database, db


def _connect_scope(db, monkeypatch):
    monkeypatch.setattr(dialogs, '_connect', lambda: db)
    monkeypatch.setattr(binding, '_connect', lambda: db)
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'true')
    grants = {'33': {'access_scopes': {'dialogs': {'view': 'self'}, 'companies': {'view': 'none'}}}}
    db.execute("""INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value)
      VALUES(%s::uuid,'members',%s::jsonb) ON CONFLICT(tenant_id,policy_key)
      DO UPDATE SET value=EXCLUDED.value""", (A,json.dumps(grants)))
    db.execute("""INSERT INTO saas_dialog_source_members(tenant_id,provider,source_id,employee_id,updated_by)
      VALUES(%s::uuid,'connect',%s::uuid,33,11)""", (A,CA))
    db.commit()


def _links(db, tenant, channel, conversation, company, actor=11):
    db.execute(f"""INSERT INTO {binding.CONNECT}(tenant_id,channel_id,conversation_id,company_id,created_by,updated_by)
      VALUES(%s::uuid,%s::uuid,%s,%s::uuid,%s,%s)""", (tenant,channel,conversation,company,actor,actor))


def _search(profile, term, **options):
    return dialogs.listing(profile, search=term, lifecycle='all', **options)


def test_company_only_matches_contextual_name_but_not_passive_or_archived_rows(companies, monkeypatch):
    db, own, _, foreign, _ = companies
    for company, name in ((own,'Synthetic Aurora %_ Studio'),(foreign,'Foreign Galaxy Studio')):
        db.execute("UPDATE saas_crm_native_companies SET name=%s WHERE id=%s::uuid", (name,company))
    _links(db,A,CA,'client',own)
    _links(db,B,CB,'client',foreign,actor=22)
    _connect_scope(db, monkeypatch)

    for profile in (PROFILE,STAFF):
        found = _search(profile,'aURORA %_')
        assert found['total_count'] == 1
        card, = found['dialogs']
        assert card['lead_id'] == 41 and card['dialog_company_name'] == 'Synthetic Aurora %_ Studio'
        assert 'aurora' not in card['customer_name'].casefold()
        assert own not in str(card)  # names only; no directory IDs.
        assert _search(profile,'Foreign Galaxy')['dialogs'] == []
        assert _search(profile,'Aurora __')['dialogs'] == []
    assert _search({'tenant_id':B,'telegram_id':22},'Foreign Galaxy')['dialogs'] == []

    command()  # Accepted by someone other than STAFF.
    existing = _search(STAFF,'Client',search_overview=True)['dialogs']
    assert any(row['lead_id']==41 and row['search_overview_read_only'] for row in existing)
    assert _search(STAFF,'Aurora',search_overview=True)['dialogs'] == []
    assert [row['lead_id'] for row in _search(PROFILE,'Aurora')['dialogs']] == [41]
    command('close',1)
    closed = _search(STAFF,'Client',search_overview=True)['dialogs']
    assert any(row['lead_id']==41 and row['archive_read_only'] and 'dialog_company_name' not in row for row in closed)
    assert _search(STAFF,'Aurora',search_overview=True)['dialogs'] == []
    db.execute("UPDATE saas_crm_native_companies SET archived_at=now() WHERE id=%s::uuid", (own,))
    db.commit()
    assert _search(PROFILE,'Aurora')['dialogs'] == []
    assert [row['lead_id'] for row in _search(PROFILE,'Client')['dialogs']] == [41]


def test_group_company_match_never_uses_an_inaccessible_sibling_source(companies, monkeypatch):
    db, first, second, *_ = companies
    _connect_scope(db, monkeypatch)
    hidden = str(uuid.uuid4())
    group = '120000000000000041@g.us'
    db.execute("""INSERT INTO saas_connect_channels(tenant_id,connect_channel_id,name)
      VALUES(%s::uuid,%s::uuid,'Synthetic private source')""", (A,hidden))
    for lead, channel, company, title in (
            (51,CA,first,'Synthetic Marble Workshop'),(52,hidden,second,'Synthetic Slate Studio')):
        db.execute("""INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,
          conversation_kind,lead_id,display_name) VALUES(%s::uuid,%s::uuid,%s,'group',%s,'Synthetic chat')""",
          (A,channel,group,lead))
        db.execute("UPDATE saas_crm_native_companies SET name=%s WHERE id=%s::uuid", (title,company))
        _links(db,A,channel,group,company)
    db.commit()

    staff_hit, = _search(STAFF,'Marble Workshop')['dialogs']
    assert staff_hit['lead_id'] == 51
    assert staff_hit['dialog_company_name'] == 'Synthetic Marble Workshop'
    assert [route['lead_id'] for route in staff_hit['group_sources']] == [51]
    assert _search(STAFF,'Slate Studio')['dialogs'] == []
    owner_hit, = _search(PROFILE,'Slate Studio')['dialogs']
    assert owner_hit['lead_id'] == 52 and owner_hit['dialog_company_name'] == 'Synthetic Slate Studio'
    assert [route['lead_id'] for route in owner_hit['group_sources']] == [52]


def test_employee_archive_shows_exact_operator_label_with_read_grant(companies, monkeypatch):
    db, own, *_ = companies
    _connect_scope(db, monkeypatch)
    _links(db,A,CA,'client',own)
    db.commit()
    command()
    command('close',1)
    monkeypatch.setenv('BEINSYSTEMS_DIALOG_OPERATOR_CHOOSER_ENABLED','true')
    for lead, account in ((41,'synthetic-real'),(42,'wrong-route')):
        db.execute("""INSERT INTO saas_operator_dialog_accounts
          (tenant_id,provider,lead_id,channel_id,conversation_id,id,account,generation,instance_id,updated_by)
          VALUES(%s::uuid,'connect',%s,%s,'client',%s::uuid,%s,'fixture',%s::uuid,11)""",
          (A,lead,CA,str(uuid.uuid4()),account,str(uuid.uuid4())))
    db.commit()

    def archive(mode):
        row, = dialogs.listing(STAFF,lifecycle=mode,channel='connect:'+CA)['dialogs']
        assert row['lead_id']==41 and row['archive_read_only'] and not row['can_send']
        assert row['last_message_at']
        return row

    assert 'dialog_company_name' not in archive('all')
    db.execute("""UPDATE saas_tenant_members SET permissions=permissions||'["operator_data:view"]'::jsonb
      WHERE tenant_id=%s::uuid AND telegram_id=33""",(A,))
    db.commit()
    for mode in ('all','completed'):
        row=archive(mode)
        assert row['dialog_company_name']=='synthetic-real'
        assert 'company_binding' not in row and 'last_message_text' not in row
    assert not dialogs.listing(STAFF,lifecycle='active')['dialogs']
    db.execute("DELETE FROM saas_dialog_source_members WHERE tenant_id=%s::uuid AND employee_id=33",(A,))
    db.commit()
    assert not dialogs.listing(STAFF,lifecycle='all')['dialogs']


def test_kommo_company_lookup_requires_the_full_visible_route_tuple(companies, monkeypatch):
    db, own, misplaced, *_ = companies
    _connect_scope(db, monkeypatch)
    with db.cursor() as cur:
        binding.provision_kommo(cur)
    for company, name in ((own,'Synthetic Quartz Clinic'),(misplaced,'Wrong Route Company')):
        db.execute("UPDATE saas_crm_native_companies SET name=%s WHERE id=%s::uuid", (name,company))
    db.execute("""INSERT INTO saas_crm_deals(tenant_id,kommo_lead_id,name)
      VALUES(%s::uuid,503,'Synthetic deal')""", (A,))
    db.execute("""INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body)
      VALUES(%s::uuid,%s::uuid,503,'synthetic-inbound','email','incoming','Synthetic text')""",
      (str(uuid.uuid4()),A))
    db.commit()
    binding.binding(PROFILE,provider='kommo',lead_id=503,channel_id='email',conversation_id='',company_id=own)
    db.execute(f"""INSERT INTO {binding.KOMMO}(tenant_id,lead_id,channel_id,conversation_id,company_id,created_by,updated_by)
      VALUES(%s::uuid,503,'email','fabricated',%s::uuid,11,11)""", (A,misplaced))
    db.commit()
    hit, = _search(PROFILE,'Quartz Clinic')['dialogs']
    assert (hit['provider'],hit['lead_id'],hit['channel_id'],hit['conversation_id']) == ('kommo',503,'email','')
    assert hit['dialog_company_name'] == 'Synthetic Quartz Clinic'
    assert _search(PROFILE,'Wrong Route')['dialogs'] == []
    assert _search(STAFF,'Quartz Clinic')['dialogs'] == []  # Staff have only their Connect source.
