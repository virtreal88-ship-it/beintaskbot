"""Synthetic browser merge and display metadata against the real SQL paths."""
import uuid
import pytest

import tenant_ai_dialogue_merge as core
import tenant_dialog_duplicate_actions as actions
import tenant_operator_dialogs as dialogs
from tenant_linear_policy import TenantLinearError
from test_connect_dependent_reconcile import seed, JID, LID
from test_tenant_support_inbox import db, database, A, B, CA, CB, PROFILE


def route(lead, identity, channel=CA):
    return {'provider': 'connect', 'channel_id': channel, 'lead_id': lead, 'conversation_id': identity}


def setup(db, monkeypatch):
    monkeypatch.setattr(actions, '_connect', lambda: db)
    monkeypatch.setattr(dialogs, '_connect', lambda: db)
    monkeypatch.setenv('TENANT_ENCRYPTION_KEY', 'synthetic-merge-key')
    monkeypatch.delenv('AI_LEARNING_MCP_CURSOR_SECRET', raising=False)
    actions.prepare_schema(db)
    db.commit()
    def no_request_ddl(*_args):
        pytest.fail('browser endpoint attempted startup-only schema initialization')
    monkeypatch.setattr(actions,'provision_tables',no_request_ddl)


def activate_lifecycle(db):
    from tenant_dialog_lifecycle import migrate
    from tenant_dialog_rollout import transition
    with db.cursor() as cur:
        migrate(cur)
        transition(cur,A,mode='active',admission_enabled=True,expected_generation=0)
    db.commit()


def preview_command():
    return {'left': route(8101, JID), 'right': route(8102, LID)}


def merge_command(item):
    return dict(channel_id=CA, pair_id=item['pair_id'], precondition=item['precondition'],
                request_id=str(uuid.uuid4()), confirm=True, name_lead_id=8102, phone_lead_id=8101)


def test_browser_reuses_provider_merge_and_exact_rollback(db, monkeypatch):
    setup(db, monkeypatch)
    initial = seed(db, monkeypatch)
    db.execute("UPDATE saas_connect_conversations SET manual_display_name='Prior name' WHERE tenant_id=%s AND lead_id=8101", (A,))
    db.execute("UPDATE saas_connect_conversations SET display_name='Chosen name' WHERE tenant_id=%s AND lead_id=8102", (A,))
    db.commit()
    activate_lifecycle(db)
    stale = actions.preview(PROFILE, preview_command())
    assert stale['eligible'] and stale['phone'] == '+' + JID.split('@')[0]
    db.execute("UPDATE saas_connect_conversations SET display_name='Updated name' WHERE tenant_id=%s AND lead_id=8102", (A,))
    db.commit()
    with pytest.raises(TenantLinearError) as error:
        actions.merge(PROFILE, merge_command(stale))
    assert error.value.status == 409
    item = actions.preview(PROFILE, preview_command())
    assert {x['display_name'] for x in item['name_choices']} == {'Prior name', 'Updated name'}
    request = merge_command(item)
    with pytest.raises(TenantLinearError):
        actions.merge(PROFILE, {**request, 'phone_lead_id': 42})
    merged = actions.merge(PROFILE, request)
    assert merged['combined_messages'] == 85 and merged['retained_route_merge']
    assert actions.merge(PROFILE, request)['idempotent_replay']
    with pytest.raises(TenantLinearError):
        actions.merge(PROFILE, {**request, 'name_lead_id': 8101})
    assert db.execute("SELECT manual_display_name FROM saas_connect_conversations WHERE lead_id=8101")[0] == {'manual_display_name': 'Updated name'}
    old_messages = {r['id']: r['kommo_lead_id'] for r in initial['messages']}
    assert {str(r['id']): r['kommo_lead_id'] for r in db.execute("SELECT id,kommo_lead_id FROM saas_crm_messages WHERE kommo_lead_id IN (8101,8102)")} == old_messages
    cfg = actions._config(A, 11, CA)
    with db.cursor() as cur:
        item = core.preview(cur, cfg, {'pair_id': request['pair_id']})['items'][0]
        core.execute(cur, cfg, {'operation': 'rollback', 'pair_id': item['pair_id'], 'precondition': item['precondition'],
                                'request_id': str(uuid.uuid4())})
    db.commit()
    assert db.execute("SELECT manual_display_name FROM saas_connect_conversations WHERE lead_id=8101")[0] == {'manual_display_name': 'Prior name'}


def test_manual_phone_never_changes_route_or_merge_evidence(db, monkeypatch):
    setup(db, monkeypatch)
    lead = 9901
    lid = '992001234567@lid'
    group = '120363123456789@g.us'
    for tenant, channel, lead_id, identity, kind in ((A, CA, lead, lid, 'contact'), (A, CA, 9902, group, 'group'),
                                                    (B, CB, 9903, lid, 'contact')):
        db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,conversation_kind) VALUES(%s::uuid,%s::uuid,%s,%s,%s)",
                   (tenant, channel, identity, lead_id, kind))
    db.execute("""INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body)
       VALUES(%s,%s,%s,'synthetic-manual-phone',%s,'incoming','Synthetic')""",
       (str(uuid.uuid4()), A, lead, 'connect:'+CA))
    db.commit()
    activate_lifecycle(db)
    command = route(lead, lid)
    with pytest.raises(TenantLinearError):
        actions.save_phone(PROFILE, {**command, 'phone': '+992001234567'})
    with pytest.raises(TenantLinearError):
        actions.save_phone(PROFILE, {**route(9902, group), 'phone': '+994700000009'})
    with pytest.raises(TenantLinearError):
        actions.save_phone(PROFILE, {**route(9903, lid, CB), 'phone': '+994700000009'})
    assert actions.save_phone(PROFILE, {**command, 'phone': '+994 (70) 000-00-09'}) == {'display_phone': '+994700000009'}
    listed = next(r for r in dialogs.listing(PROFILE)['dialogs'] if r['lead_id'] == lead)
    assert listed['manual_display_phone'] == '+994700000009' and not listed['phone']
    opened = dialogs.history(PROFILE, 'connect', lead, CA, lid)['dialog']
    assert opened['manual_display_phone'] == '+994700000009' and not opened['phone']
    db.execute("""INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,display_name,permissions)
        VALUES(%s,33,'operator','Synthetic operator','["dialogs"]')""",(A,))
    db.commit()
    member = {'tenant_id': A, 'telegram_id': 33}
    staff_dialog = next(r for r in dialogs.listing(member)['dialogs'] if r['lead_id'] == lead)
    assert not staff_dialog.get('manual_display_phone')
    with pytest.raises(TenantLinearError) as error:
        actions.save_phone(member, {**command, 'phone': '+994700000099'})
    assert error.value.status == 403
    with db.cursor() as cur:
        assert not actions.collect(cur, A, CA, [lid])['phones_by_identity'].get(lid)
    assert actions.save_phone(PROFILE, {**command, 'phone': ''}) == {'display_phone': ''}


    db.execute("""INSERT INTO saas_dialog_workflows(tenant_id,channel_id,conversation_id,id,state,operator_id)
        VALUES(%s,%s,%s,%s,'closed',%s)""",(A,CA,lid,str(uuid.uuid4()),PROFILE['telegram_id']))
    db.commit()
    with pytest.raises(TenantLinearError) as error:
        actions.save_phone(PROFILE, {**command, 'phone': '+994700000009'})
    assert error.value.status == 409


def test_preflight_and_execute_respect_closed_lifecycle_fence(db,monkeypatch):
    setup(db,monkeypatch)
    seed(db,monkeypatch)
    activate_lifecycle(db)
    before=actions.preview(PROFILE,preview_command())
    assert before['eligible']
    db.execute("""INSERT INTO saas_dialog_workflows(tenant_id,channel_id,conversation_id,id,state,operator_id)
        VALUES(%s,%s,%s,%s,'closed',%s)""",(A,CA,LID,str(uuid.uuid4()),PROFILE['telegram_id']))
    db.commit()
    blocked=actions.preview(PROFILE,preview_command())
    assert not blocked['eligible'] and 'dialog_lifecycle_in_use' in blocked['conflicts']
    with pytest.raises(TenantLinearError) as error:
        actions.merge(PROFILE,merge_command(before))
    assert error.value.status==409
    assert not db.execute("SELECT 1 FROM saas_connect_dialog_links WHERE tenant_id=%s AND active",(A,))
