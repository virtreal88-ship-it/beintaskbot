"""Independent employee navigation and native CRM actions, isolated PostgreSQL."""
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


def test_pages_only_never_change_grants_or_other_employee_settings(cards):
    seed(cards);row=get(cards);before=policy(cards);permissions=row['explicit_permissions']
    apply(cards,row,access(row,{'pages':{'tasks':False,'finance':False}}))
    fresh=get(cards)
    assert fresh['explicit_permissions']==permissions
    assert fresh['access_settings']['page_visibility']=={'tasks':False,'finance':False}
    assert fresh['access_settings']['pages']['tasks'] is False
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
    assert fresh['access_settings']['page_visibility']=={}
    assert fresh['access_settings']['actions']['tasks']['delete'] is False


def test_combined_access_and_work_settings_preserve_both(cards):
    seed(cards);row=get(cards)
    changes=access(row,{'pages':{'tasks':False},'actions':{'tasks':{'delete':False}}})
    changes['work_settings']={'expected_version':row['work_settings']['version'],'changes':{'ai_enabled':True}}
    apply(cards,row,changes)
    assert policy(cards)['61']['page_visibility']=={'tasks':False}
    assert policy(cards)['61']['ai_enabled'] is True
    assert policy(cards)['61']['pipeline_ids']==['10']


def test_stale_navigation_policy_rejects_entire_card_write(cards):
    seed(cards);row=get(cards)
    with cards['factory']() as conn:
        conn.execute("UPDATE saas_tenant_workflow_policies SET value=jsonb_set(value,'{61,page_visibility}','{\"tasks\":false}'::jsonb) WHERE tenant_id=%s AND policy_key='members'",(A,))
    with pytest.raises(members.MemberCardError) as exc:
        apply(cards,row,{'display_name':'Do not persist',**access(row,{'pages':{'finance':False}})})
    assert exc.value.code=='member_settings_conflict'
    assert get(cards)['display_name']==row['display_name']


@pytest.mark.parametrize('change',[{'pages':{'unknown':True}}, {'pages':{'profile':False}},
    {'pages':{'tasks':1}}, {'actions':{'finance':{'delete':True}}},
    {'actions':{'tasks':{'manage':True}}}, {'actions':{'tasks':{'view':1}}}, {'pages':{}}])
def test_invalid_access_change_does_not_mutate(cards,change):
    row=get(cards)
    with pytest.raises(members.MemberCardError):apply(cards,row,access(row,change))
    assert get(cards)['card_version']==row['card_version']


def test_only_owner_can_change_access_and_owner_is_protected(cards):
    row=get(cards)
    with pytest.raises(members.MemberCardError):apply(cards,row,access(row,{'pages':{'tasks':False}}),owner=12)
    assert get(cards)['card_version']==row['card_version']
    owner=get(cards,11)
    assert 'access_settings' not in owner['editable_fields']
    with pytest.raises(members.MemberCardError):apply(cards,owner,access(owner,{'pages':{'tasks':False}}))


def test_unrelated_account_profile_change_keeps_access_version(cards):
    row=get(cards);snap=access_snapshot({**row,'updated_at':'later','first_name':'New','email':'other@example.test'})
    assert snap['version']==row['access_settings']['version']


def test_access_target_never_mutates_foreign_company(cards):
    row=get(cards)
    with cards['factory']() as conn:
        before=conn.execute('SELECT * FROM saas_tenant_members WHERE tenant_id=%s',(B,)).fetchall()
    apply(cards,row,access(row,{'pages':{'tasks':False}}))
    with cards['factory']() as conn:
        assert conn.execute('SELECT * FROM saas_tenant_members WHERE tenant_id=%s',(B,)).fetchall()==before
        assert not conn.execute("SELECT value FROM saas_tenant_workflow_policies WHERE tenant_id=%s AND policy_key='members'",(B,)).fetchone()


def test_concurrent_different_access_rows_preserved(cards):
    rows=[get(cards,61),get(cards,62)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda row: apply(cards,row,access(row,{'pages':{'tasks':False}})),rows))
    assert policy(cards)['61']['page_visibility']=={'tasks':False}
    assert policy(cards)['62']['page_visibility']=={'tasks':False}


def test_visibility_inheritance_removes_only_selected_override(cards):
    row=get(cards);apply(cards,row,access(row,{'pages':{'tasks':False,'finance':False}}))
    fresh=get(cards);apply(cards,fresh,access(fresh,{'pages':{'tasks':None}}))
    assert policy(cards)['61']['page_visibility']=={'finance':False}


def test_coarse_non_native_grant_and_page_preference_compose(cards):
    row=get(cards)
    apply(cards,row,{**access(row,{'pages':{'support':False},'actions':{'tasks':{'update':True}}}),
                     'permission_changes':{'finance':False,'support':True}})
    fresh=get(cards)
    assert 'support' in fresh['explicit_permissions']
    assert 'finance' not in fresh['explicit_permissions']
    assert fresh['access_settings']['pages']['support'] is False
    assert fresh['access_settings']['actions']['tasks']['update'] is True
    assert 'legacy:retired' in fresh['explicit_permissions']


def test_coarse_and_native_actions_cannot_target_same_module(cards):
    row=get(cards)
    with pytest.raises(members.MemberCardError):
        apply(cards,row,{**access(row,{'actions':{'tasks':{'view':True}}}),
                         'permission_changes':{'tasks':True}})
    assert get(cards)['card_version']==row['card_version']
