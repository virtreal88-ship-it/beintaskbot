"""Minimal display values never grant AI document/operation access."""
import json
import pytest
from tenant_linear_policy import TenantLinearError
from test_ai_scoped_documents import scoped, grants, ACTOR
from test_tenant_support_inbox import db, database, A, B, CA, CB
import tenant_operator_dialogs as dialogs


def enable(db, enabled=True):
    grants(db, {'view': 'self', 'update': 'none'}, dialogs='self')
    db.execute("UPDATE saas_tenant_workflow_policies SET value=jsonb_set(value,'{31,ai_enabled}',%s::jsonb) WHERE tenant_id=%s AND policy_key='members'", (json.dumps(enabled), A))
    db.execute("INSERT INTO saas_dialog_source_members(tenant_id,provider,source_id,employee_id,updated_by) VALUES(%s,'connect',%s,31,11) ON CONFLICT DO NOTHING", (A, CA))
    db.execute("UPDATE saas_tenants SET modules='{\"deals\":true}'::jsonb WHERE id=%s",(A,))
    db.execute("UPDATE saas_tenant_members SET permissions='[\"deals\"]'::jsonb WHERE tenant_id=%s AND telegram_id=31",(A,))
    db.commit()


def test_visible_direct_name_not_document_or_operation(scoped, monkeypatch):
    db, ids = scoped; enable(db)
    monkeypatch.setattr(dialogs, '_connect', lambda: db)
    db.execute("INSERT INTO saas_ai_conversation_state(tenant_id,provider,channel_id,conversation_id,agent_id,state) VALUES(%s,'connect',%s,'client',%s,'paused')", (A, CA, ids['other'])); db.commit()
    for result in [dialogs.listing(ACTOR)['dialogs'][0], dialogs.history(ACTOR, 'connect', 41, CA, 'client')['dialog']]:
        assert result['assignment_display'] == {'assigned': True, 'name': 'other'}
        assert 'agent_id' not in result and 'agent_name' not in result
        assert ids['other'] not in json.dumps(result)
        assert result['controls_allowed'] is False and result['available_actions'] == []
    with pytest.raises(TenantLinearError):
        dialogs.listing(ACTOR,agent='unassigned')
    db.execute("DELETE FROM saas_dialog_source_members WHERE tenant_id=%s",(A,));db.commit()
    assert dialogs.listing(ACTOR)['dialogs'] == []
    enable(db, False)
    assert 'assignment_display' not in dialogs.history(ACTOR, 'connect', 41, CA, 'client')['dialog']


def test_group_source_and_tenant_boundaries(scoped, monkeypatch):
    db, ids = scoped; enable(db)
    monkeypatch.setattr(dialogs, '_connect', lambda: db)
    from tenant_scoped_access_store import read_access_profile
    from tenant_policy import TenantPolicy
    with db.cursor() as cur:
        policy = TenantPolicy(read_access_profile(cur, A, 31))
        for tenant, channel, agent in [(A, CA, ids['other']), (B, CB, ids['foreign'])]:
            cur.execute("INSERT INTO saas_ai_group_coordination(tenant_id,provider,group_jid,assignments,state) VALUES(%s,'connect','120363000001@g.us',%s::jsonb,'human')", (tenant, json.dumps([{'agent_id':agent,'channel_id':channel}])))
        cur.execute("INSERT INTO saas_connect_conversations(tenant_id,connect_channel_id,conversation_id,lead_id) VALUES(%s,%s,'120363000001@g.us',77)",(A,CA))
        cur.execute("UPDATE saas_ai_agents SET enabled=true,scopes=%s::jsonb WHERE tenant_id=%s",(json.dumps([{'provider':'connect','channel_id':CA,'allow_group_messages':True}]),A))
        rows = [{'provider': 'connect', 'channel_id': CA, 'conversation_id': '120363000001@g.us'}]
        dialogs._assignment_display(cur, A, policy, rows)
        assert rows[0]['assignment_display'] == {'assigned': True, 'name': 'other'}
        other = [{'provider': 'connect', 'channel_id': CB, 'conversation_id': '120363000001@g.us'}]
        dialogs._assignment_display(cur, B, policy, other)
        assert 'assignment_display' not in other[0]
        cur.execute("UPDATE saas_ai_group_coordination SET assignments=%s::jsonb WHERE tenant_id=%s", (json.dumps([{'agent_id':ids['other'],'channel_id':CB}]), A))
        rows[0].pop('assignment_display')
        dialogs._assignment_display(cur, A, policy, rows)
        assert 'assignment_display' not in rows[0]
        direct = [{'provider': 'connect', 'channel_id': CB, 'conversation_id': 'client', 'agent_name': 'private'}]
        dialogs._assignment_display(cur, A, policy, direct)
        assert 'assignment_display' not in direct[0]


def test_inactive_policy_never_gets_display(scoped):
    db, ids = scoped; enable(db)
    from tenant_scoped_access_store import read_access_profile
    from tenant_policy import TenantPolicy
    with db.cursor() as cur:
        profile=read_access_profile(cur,A,31)
        policy=TenantPolicy({**profile,'active':False})
        rows=[{'provider':'connect','channel_id':CA,'conversation_id':'client','agent_name':'other'}]
        dialogs._assignment_display(cur,A,policy,rows)
        assert 'assignment_display' not in rows[0]
