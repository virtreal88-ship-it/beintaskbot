"""Employee operation rights derive navigation, isolated PostgreSQL only."""
import json
from concurrent.futures import ThreadPoolExecutor
import pytest
import tenant_member_store as members
from tenant_member_access import access_snapshot
from test_employee_settings_postgres import get, apply, seed, policy
from test_employee_card_consistency_postgres import cards
from test_company_employees_navigation_postgres import A, B, database, db, staff


def access(row, changes):
    return {'access_settings': {'expected_version': row['access_settings']['version'], 'changes': changes}}


def test_four_crm_pages_match_member_session_from_effective_permissions(cards):
    from tenant_policy import TenantPolicy
    grants = ['tasks', 'deals', 'customers']
    with cards['factory']() as conn:
        conn.execute("UPDATE saas_tenant_members SET role='manager',permissions=%s::jsonb WHERE tenant_id=%s AND telegram_id=61", (json.dumps(grants), A))
        conn.execute("UPDATE saas_tenants SET modules=%s::jsonb WHERE id=%s", (json.dumps({key: True for key in grants}), A))
    before = get(cards)
    snapshot = before['access_settings']
    session = TenantPolicy(members.member(A, 61)).public_capabilities()
    assert 'page_visibility' not in snapshot and 'page_visibility' not in session
    assert 'page_catalog' not in snapshot and 'page_prerequisites' not in snapshot
    assert snapshot['pages'] == session['pages']
    assert all(snapshot['pages'][page] for page in ('tasks', 'customers', 'companies', 'deals'))
    for module in grants:
        assert all(snapshot['actions'][module].values())
    assert snapshot['pages']['finance'] is False
    assert get(cards)['card_version'] == before['card_version']
    apply(cards, before, {'display_name': 'Unrelated local alias'})
    fresh = get(cards)
    assert fresh['access_settings'] == snapshot
    assert fresh['explicit_permissions'] == grants


def test_retired_visibility_is_ignored_without_data_or_grant_migration(cards):
    seed(cards)
    with cards['factory']() as conn:
        conn.execute("UPDATE saas_tenant_workflow_policies SET value=jsonb_set(value,'{61,page_visibility}','{\"tasks\":false,\"finance\":true}'::jsonb) WHERE tenant_id=%s AND policy_key='members'", (A,))
    row=get(cards);before=policy(cards);permissions=row['explicit_permissions']
    apply(cards,row,access(row,{'actions':{'tasks':{'view':True,'update':False}}}))
    fresh=get(cards)
    assert fresh['explicit_permissions']==permissions
    assert fresh['access_settings']['pages']['tasks'] is True
    assert policy(cards)['61']['page_visibility']==before['61']['page_visibility']
    assert policy(cards)['61']['pipeline_ids']==before['61']['pipeline_ids']
    assert policy(cards)['62']==before['62']


def test_native_view_visible_without_legacy_kommo_module_and_crud_separate(cards):
    with cards['factory']() as conn:
        conn.execute("UPDATE saas_tenant_members SET role='operator',permissions='[\"tasks:view\"]'::jsonb WHERE tenant_id=%s AND telegram_id=61",(A,))
    row=get(cards)
    assert row['access_settings']['pages']['tasks'] is True
    assert row['access_settings']['actions']['tasks']=={'view':True,'create':False,'update':False,'delete':False}
    apply(cards,row,access(row,{'actions':{'tasks':{'update':True}}}))
    fresh=get(cards)
    assert set(fresh['explicit_permissions'])=={'tasks:view'}
    assert policy(cards)['61']['native_crm_actions']['tasks']['update'] is True
    assert 'page_visibility' not in fresh['access_settings']
    assert fresh['access_settings']['actions']['tasks']['delete'] is False


def test_combined_access_and_work_settings_preserve_both(cards):
    seed(cards);row=get(cards)
    changes=access(row,{'actions':{'tasks':{'delete':False}}})
    changes['work_settings']={'expected_version':row['work_settings']['version'],'changes':{'ai_enabled':True}}
    apply(cards,row,changes)
    assert policy(cards)['61']['native_crm_actions']['tasks']['delete'] is False
    assert policy(cards)['61']['ai_enabled'] is True
    assert policy(cards)['61']['pipeline_ids']==['10']


def test_stale_operation_policy_rejects_entire_card_write(cards):
    seed(cards);row=get(cards)
    with cards['factory']() as conn:
        conn.execute("UPDATE saas_tenant_workflow_policies SET value=jsonb_set(value,'{61,native_crm_actions}','{\"tasks\":{\"update\":false}}'::jsonb) WHERE tenant_id=%s AND policy_key='members'",(A,))
    with pytest.raises(members.MemberCardError) as exc:
        apply(cards,row,{'display_name':'Do not persist',**access(row,{'actions':{'tasks':{'view':True}}})})
    assert exc.value.code=='member_settings_conflict'
    assert get(cards)['display_name']==row['display_name']


@pytest.mark.parametrize('change',[{'pages':{'tasks':True}}, {'pages':{'tasks':False}},
    {'pages':{'tasks':None}}, {'pages':{'profile':False}}, {'pages':{}},
    {'actions':{'finance':{'delete':True}}}, {'actions':{'tasks':{'manage':True}}},
    {'actions':{'tasks':{'view':1}}}, {'actions':{}},
    {'pages':{'tasks':True},'actions':{'tasks':{'view':True}}}])
def test_removed_or_invalid_access_change_does_not_mutate(cards,change):
    row=get(cards)
    with pytest.raises(members.MemberCardError):apply(cards,row,access(row,change))
    assert get(cards)['card_version']==row['card_version']


def test_only_owner_can_change_access_and_owner_is_protected(cards):
    row=get(cards)
    with pytest.raises(members.MemberCardError):apply(cards,row,access(row,{'actions':{'tasks':{'update':False}}}),owner=12)
    assert get(cards)['card_version']==row['card_version']
    owner=get(cards,11)
    assert 'access_settings' not in owner['editable_fields']
    with pytest.raises(members.MemberCardError):apply(cards,owner,access(owner,{'actions':{'tasks':{'update':False}}}))


def test_unrelated_account_profile_change_keeps_access_version(cards):
    row=get(cards);snap=access_snapshot({**row,'updated_at':'later','first_name':'New','email':'other@example.test'})
    assert snap['version']==row['access_settings']['version']


def test_access_target_never_mutates_foreign_company(cards):
    row=get(cards)
    with cards['factory']() as conn:
        before=conn.execute('SELECT * FROM saas_tenant_members WHERE tenant_id=%s',(B,)).fetchall()
    apply(cards,row,access(row,{'actions':{'tasks':{'update':False}}}))
    with cards['factory']() as conn:
        assert conn.execute('SELECT * FROM saas_tenant_members WHERE tenant_id=%s',(B,)).fetchall()==before
        assert not conn.execute("SELECT value FROM saas_tenant_workflow_policies WHERE tenant_id=%s AND policy_key='members'",(B,)).fetchone()


def test_concurrent_different_access_rows_preserved(cards):
    rows=[get(cards,61),get(cards,62)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda row: apply(cards,row,access(row,{'actions':{'tasks':{'update':False}}})),rows))
    assert policy(cards)['61']['native_crm_actions']=={'tasks':{'update':False}}
    assert policy(cards)['62']['native_crm_actions']=={'tasks':{'update':False}}


def test_operation_changes_remain_sparse(cards):
    row=get(cards);apply(cards,row,access(row,{'actions':{'tasks':{'delete':False},'deals':{'view':True}}}))
    fresh=get(cards);apply(cards,fresh,access(fresh,{'actions':{'tasks':{'update':True}}}))
    assert policy(cards)['61']['native_crm_actions']=={'tasks':{'delete':False,'update':True},'deals':{'view':True}}


def test_coarse_non_native_grant_and_native_operations_compose(cards):
    row=get(cards)
    apply(cards,row,{**access(row,{'actions':{'tasks':{'update':True}}}),
                     'permission_changes':{'finance':False,'support':True}})
    fresh=get(cards)
    assert 'support' in fresh['explicit_permissions']
    assert 'finance' not in fresh['explicit_permissions']
    assert fresh['access_settings']['pages']['support'] is True
    assert fresh['access_settings']['actions']['tasks']['update'] is True
    assert 'legacy:retired' in fresh['explicit_permissions']


def test_coarse_and_native_actions_cannot_target_same_module(cards):
    row=get(cards)
    with pytest.raises(members.MemberCardError):
        apply(cards,row,{**access(row,{'actions':{'tasks':{'view':True}}}),
                         'permission_changes':{'tasks':True}})
    assert get(cards)['card_version']==row['card_version']
