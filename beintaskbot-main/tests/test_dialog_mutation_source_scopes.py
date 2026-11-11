"""Real isolated SQL regressions for AI-managed dialog mutations.

Independent AI administration never overrides a configured dialog action scope.
No provider, external account or real company is contacted.
"""
import json
import uuid
import pytest
from test_dialog_source_scopes import scoped, grants, D1, D2, C2
from test_tenant_support_inbox import database, db, A, B, CA, CB, PROFILE
from test_tenant_operator_workspace import operator_db, employee
import tenant_ai_group_coordination as groups
import tenant_operator_assignment as assignment
from tenant_linear_policy import TenantLinearError

GROUP = '120363123456@g.us'
JID = '994501234567@s.whatsapp.net'


def rights(db, view, update):
    grants(db, 33, view, update)
    db.execute("UPDATE saas_tenant_workflow_policies SET value=jsonb_set(value,'{33,ai_agents_manage}','true'::jsonb) WHERE tenant_id=%s::uuid AND policy_key='members'", (A,))
    db.commit()


@pytest.fixture
def admin(scoped, monkeypatch):
    db, person = scoped
    monkeypatch.setattr(groups, '_connect', lambda: db)
    monkeypatch.setattr(assignment, '_connect', lambda: db)
    db.execute("UPDATE saas_tenant_members SET role='admin' WHERE tenant_id=%s::uuid AND telegram_id=33", (A,))
    rights(db, 'department', 'none')
    return db, person


def group(db, channels=(C2,)):
    for i, channel in enumerate(channels):
        db.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id,conversation_kind) VALUES(%s::uuid,%s::uuid,%s,%s,'group')", (A, channel, GROUP, 54+i))
    db.execute("INSERT INTO saas_ai_group_coordination(tenant_id,group_jid,state) VALUES(%s::uuid,%s,'active')", (A, GROUP))
    db.commit()
    return {'conversation_id': GROUP, 'assignments': [], 'expected_revision': 1}


def staff(db, channel=C2):
    raw = {'fromMe': True, 'source': 'linked_phone', 'participantJid': JID}
    db.execute("INSERT INTO saas_crm_messages(id,tenant_id,kommo_lead_id,external_id,channel,direction,body,raw) VALUES(%s::uuid,%s::uuid,43,%s,%s,'outgoing','synthetic',%s::jsonb)", (str(uuid.uuid4()), A, 'staff:'+channel, 'connect:'+channel, json.dumps(raw)))
    db.commit()
    return {'channel_id': channel, 'jid': JID, 'member_id': 33}


def direct(channel=CA, lead=41):
    return {'provider': 'connect', 'lead_id': lead, 'channel_id': channel, 'conversation_id': 'client',
            'request_id': str(uuid.uuid4()), 'expected_revision': 0, 'agent_id': None}


@pytest.mark.parametrize('scope', ['none', 'self', 'department'])
def test_hidden_group_cannot_be_stopped_or_reconfigured(admin, scope):
    db, person = admin
    rights(db, 'department', scope)
    data = group(db)
    with pytest.raises(TenantLinearError) as denied:
        groups.configure(person, data)
    assert denied.value.status == 403
    row = db.execute('SELECT state,revision,assignments FROM saas_ai_group_coordination WHERE tenant_id=%s::uuid AND group_jid=%s', (A, GROUP))[0]
    assert row == {'state': 'active', 'revision': 1, 'assignments': []}


@pytest.mark.parametrize('channels', [(CA,), (CA, C2)])
def test_group_mutation_requires_every_source_not_just_visible_copy(admin, channels):
    db, person = admin
    rights(db, 'none', 'department')
    data = group(db, channels)
    if C2 in channels:
        with pytest.raises(TenantLinearError):
            groups.configure(person, data)
    else:
        assert groups.configure(person, data)['state'] == 'paused'


def test_group_removal_cannot_drop_hidden_old_assignment(admin):
    db, person = admin
    rights(db, 'department', 'department')
    data = group(db, (CA,))
    old = [{'channel_id': C2, 'agent_id': str(uuid.UUID(int=999)), 'priority': 0}]
    db.execute('UPDATE saas_ai_group_coordination SET assignments=%s::jsonb WHERE tenant_id=%s::uuid AND group_jid=%s', (json.dumps(old), A, GROUP))
    db.commit()
    with pytest.raises(TenantLinearError):
        groups.configure(person, data)
    assert db.execute('SELECT assignments FROM saas_ai_group_coordination WHERE tenant_id=%s::uuid AND group_jid=%s', (A, GROUP))[0]['assignments'] == old


def test_all_update_does_not_require_read_and_still_requires_ai_management(admin):
    db, person = admin
    rights(db, 'none', 'all')
    assert groups.configure(person, group(db, (CA, C2)))['state'] == 'paused'
    db.execute("UPDATE saas_tenant_workflow_policies SET value=jsonb_set(value,'{33,ai_agents_manage}','false'::jsonb) WHERE tenant_id=%s::uuid AND policy_key='members'", (A,))
    db.commit()
    with pytest.raises(TenantLinearError):
        groups.configure(person, {'conversation_id': GROUP, 'assignments': [], 'expected_revision': 2})


@pytest.mark.parametrize('scope,channel,allowed', [('none', CA, False), ('department', CA, True), ('department', C2, False), ('all', C2, True)])
def test_staff_identity_mutation_is_source_and_action_scoped(admin, scope, channel, allowed):
    db, person = admin
    rights(db, 'none', scope)
    data = staff(db, channel)
    if allowed:
        assert groups.register_employee(person, data) == {'verified': True}
    else:
        with pytest.raises(TenantLinearError) as denied:
            groups.register_employee(person, data)
        assert denied.value.status == 403
        assert not db.execute('SELECT * FROM saas_ai_group_staff WHERE tenant_id=%s::uuid', (A,))


@pytest.mark.parametrize('scope,channel,lead,allowed', [('none', CA, 41, False), ('department', CA, 41, True), ('department', C2, 43, False), ('all', C2, 43, True)])
def test_direct_assignment_uses_update_not_view(admin, scope, channel, lead, allowed):
    db, person = admin
    rights(db, 'none', scope)
    if allowed:
        assert assignment.assign(person, direct(channel, lead))['state'] == 'paused'
    else:
        with pytest.raises(TenantLinearError) as denied:
            assignment.assign(person, direct(channel, lead))
        assert denied.value.status == 403
        assert not db.execute('SELECT * FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid', (A,))


@pytest.mark.parametrize('mode', ['gate_off', 'omitted_update'])
def test_legacy_authority_preserved_when_update_scope_is_absent(admin, monkeypatch, mode):
    db, person = admin
    if mode == 'gate_off':
        monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'false')
    else:
        db.execute("UPDATE saas_tenant_workflow_policies SET value=value #- '{33,access_scopes,dialogs,update}' WHERE tenant_id=%s::uuid AND policy_key='members'", (A,))
        db.commit()
    assert groups.configure(person, group(db))['state'] == 'paused'
    assert groups.register_employee(person, staff(db)) == {'verified': True}
    assert assignment.assign(person, direct())['state'] == 'paused'


def test_group_state_transition_cannot_modify_other_source_via_allowed_route(admin, monkeypatch):
    db, person = admin
    import tenant_ai_agents_store as agents
    monkeypatch.setattr(agents, '_connect', lambda: db)
    rights(db, 'none', 'department')
    group(db, (CA, C2))
    with pytest.raises(TenantLinearError):
        agents.set_conversation_state(person, GROUP, 'paused', 'Synthetic test', 'connect', CA)
    row = db.execute('SELECT state,revision FROM saas_ai_group_coordination WHERE tenant_id=%s::uuid AND group_jid=%s', (A, GROUP))[0]
    assert row == {'state': 'active', 'revision': 1}


def test_self_source_grant_and_current_membership_are_rechecked(admin):
    db, person = admin
    rights(db, 'none', 'self')
    data = group(db, (CA,))
    db.execute('INSERT INTO saas_dialog_source_members(tenant_id,source_id,employee_id,updated_by) VALUES(%s::uuid,%s::uuid,33,11)', (A, CA))
    db.commit()
    assert groups.configure(person, data)['state'] == 'paused'
    db.execute('DELETE FROM saas_dialog_source_members WHERE tenant_id=%s::uuid AND source_id=%s::uuid AND employee_id=33', (A, CA))
    db.commit()
    with pytest.raises(TenantLinearError):
        groups.configure(person, {**data, 'expected_revision': 2})
