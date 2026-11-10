"""Sparse owner settings use disposable test PostgreSQL; never live data."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
import tenant_member_store as members
from tenant_member_settings import settings_snapshot
from test_employee_card_consistency_postgres import cards
from test_company_employees_navigation_postgres import A, B, database, db, staff


def get(f, actor=61):
    return next(row for row in members.list_members(tenant_id=A, owner_id=11) if row['telegram_id']==actor)


def apply(f, row, changes, owner=11):
    return members.upsert_member(tenant_id=A, owner_id=owner, telegram_id=row['telegram_id'],
        expected_version=row['card_version'], changes=changes)


def seed(f):
    with f['factory']() as conn:
        conn.execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value) VALUES(%s,'members',%s::jsonb) ON CONFLICT(tenant_id,policy_key) DO UPDATE SET value=EXCLUDED.value", (A,json.dumps({'61':{'pipeline_ids':['10'],'ai_enabled':False,'notifications':{'linear_done':{'push':True}}},'62':{'ai_enabled':True}})))


def policy(f):
    with f['factory']() as conn:
        return conn.execute("SELECT value FROM saas_tenant_workflow_policies WHERE tenant_id=%s AND policy_key='members'", (A,)).fetchone()['value']


def test_owner_edits_one_member_settings_without_replacing_others(cards):
    f=cards;seed(f);row=get(f);before=policy(f)
    patch={'expected_version':row['work_settings']['version'],'changes':{'ai_enabled':True,'completion_requires_admin':True,'notifications':{'task_assigned':{'push':True}}}}
    result=apply(f,row,{'work_settings':patch})
    after=policy(f)
    assert after['62']==before['62']
    assert after['61']['pipeline_ids']==['10']
    assert after['61']['notifications']['linear_done']=={'push':True}
    assert after['61']['notifications']['task_assigned']=={'push':True}
    assert after['61']['ai_enabled'] is True
    assert result['card_version']!=row['card_version']
    fresh=get(f);assert fresh['work_settings']['values']['completion_requires_admin'] is True
    assert 'pipeline_ids' not in fresh['work_settings']['values']
    assert fresh['work_settings']['values']['notifications']['linear_done']=={'push':True}


def test_full_module_toggle_can_remove_existing_hidden_and_action_grants(cards):
    row=get(cards)
    with cards['factory']() as conn:
        conn.execute("UPDATE saas_tenant_members SET permissions=%s::jsonb WHERE tenant_id=%s AND telegram_id=61", (json.dumps(['tasks','finance','finance:manage','legacy:retired','linear']),A))
    row=get(cards)
    apply(cards,row,{'permission_changes':{'finance':False,'employees':True}})
    actual=get(cards)['explicit_permissions']
    assert set(actual)=={'tasks','employees','legacy:retired','linear'}


def test_stale_workflow_settings_cannot_overwrite_newer_values(cards):
    seed(cards);row=get(cards)
    with cards['factory']() as conn:
        conn.execute("UPDATE saas_tenant_workflow_policies SET value=jsonb_set(value,'{61,ai_enabled}','true') WHERE tenant_id=%s AND policy_key='members'", (A,))
    with pytest.raises(members.MemberCardError) as error:
        apply(cards,row,{'display_name':'Must not commit','work_settings':{'expected_version':row['work_settings']['version'],'changes':{'completion_requires_admin':True}}})
    assert error.value.code=='member_settings_conflict'
    assert get(cards)['display_name']==row['display_name']
    assert policy(cards)['61']['ai_enabled'] is True


def test_inherit_removes_only_selected_override(cards):
    seed(cards);row=get(cards)
    apply(cards,row,{'work_settings':{'expected_version':row['work_settings']['version'],'changes':{'ai_enabled':None}}})
    assert 'ai_enabled' not in policy(cards)['61']
    assert policy(cards)['61']['pipeline_ids']==['10']


@pytest.mark.parametrize('changes',[{'permission_changes':{'owner':True}}, {'permission_changes':{'tasks':1}},
    {'permission_changes':{'unknown_provider':True}}, {'permission_changes':{'tasks':False},'permissions':[]},
    {'work_settings':{'expected_version':'x','changes':{'pipeline_ids':['10']}}}])
def test_rejected_extra_scope(cards,changes):
    row=get(cards)
    with pytest.raises(members.MemberCardError):apply(cards,row,changes)
    assert get(cards)['card_version']==row['card_version']


def test_nonowner_cannot_set_work_settings(cards):
    row=get(cards)
    with pytest.raises(members.MemberCardError):
        apply(cards,row,{'work_settings':{'expected_version':row['work_settings']['version'],'changes':{'ai_enabled':True}}},owner=12)
    assert get(cards)['card_version']==row['card_version']


def test_concurrent_different_members_preserve_both_patches(cards):
    rows=[get(cards,61),get(cards,62)]
    def write(row):return apply(cards,row,{'work_settings':{'expected_version':row['work_settings']['version'],'changes':{'ai_enabled':True}}})
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(write,rows))
    assert policy(cards)['61']['ai_enabled'] is True
    assert policy(cards)['62']['ai_enabled'] is True


def test_retained_missing_services_do_not_block_new_valid_services(cards):
    with cards['factory']() as conn:
        conn.execute("INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value) VALUES(%s,'hot_orders',%s::jsonb),(%s,'members',%s::jsonb) ON CONFLICT(tenant_id,policy_key) DO UPDATE SET value=EXCLUDED.value", (A,json.dumps({'services':[{'id':'new','name':'New service','active':True}]}), A,json.dumps({'61':{'hot_order_services':['gone']}})))
    row=get(cards)
    apply(cards,row,{'work_settings':{'expected_version':row['work_settings']['version'],'changes':{'hot_order_services':['gone','new']}}})
    assert policy(cards)['61']['hot_order_services']==['gone','new']
    row=get(cards)
    with pytest.raises(members.MemberCardError):
        apply(cards,row,{'work_settings':{'expected_version':row['work_settings']['version'],'changes':{'hot_order_services':['gone','unknown']}}})


def test_settings_only_affect_selected_tenant(cards):
    row=get(cards)
    with cards['factory']() as conn:
        before=conn.execute('SELECT * FROM saas_tenant_members WHERE tenant_id=%s',(B,)).fetchall()
    apply(cards,row,{'permission_changes':{'finance':True},'work_settings':{'expected_version':row['work_settings']['version'],'changes':{'ai_enabled':True}}})
    with cards['factory']() as conn:
        assert conn.execute('SELECT * FROM saas_tenant_members WHERE tenant_id=%s',(B,)).fetchall()==before
        assert not conn.execute("SELECT value FROM saas_tenant_workflow_policies WHERE tenant_id=%s AND policy_key='members'",(B,)).fetchone()


def test_linear_panel_can_update_only_selected_access_and_channels(cards):
    seed(cards);row=get(cards)
    apply(cards,row,{'permission_changes':{'linear':True}, 'work_settings':{
        'expected_version':row['work_settings']['version'],
        'changes':{'notifications':{'linear_done':{'push':False,'telegram':True}}}}})
    fresh=get(cards)
    assert 'linear' in fresh['explicit_permissions']
    assert 'finance' in fresh['explicit_permissions']
    assert policy(cards)['61']['notifications']['linear_done']=={'push':False,'telegram':True}
    assert policy(cards)['61']['pipeline_ids']==['10']
    assert policy(cards)['62']=={'ai_enabled':True}
