"""Scoped card changes and departments, disposable localhost PostgreSQL only."""
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
import tenant_department_store as departments
import tenant_member_store as members
from test_employee_settings_postgres import get, apply, seed, policy
from test_employee_card_consistency_postgres import cards
from test_company_employees_navigation_postgres import A, B, database, db, staff


@pytest.fixture
def scoped(cards, monkeypatch):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'true')
    monkeypatch.setattr(departments, '_connect', cards['factory'])
    return cards


def create(f, name='Support', tenant=A, owner=11):
    return departments.upsert_department(tenant_id=tenant, owner_id=owner, name=name)


def patch(row, scopes):
    return {'access_settings': {'expected_version': row['access_settings']['version'], 'changes': {'scopes': scopes}}}


def test_scoped_matrix_department_and_work_settings_save_atomically(scoped):
    seed(scoped)
    dept=create(scoped); row=get(scoped); before=policy(scoped)
    assert row['department_id'] is None
    assert dept in row['department_catalog']
    changes=patch(row, {'tasks': {'create':'self','view':'department','update':'self','delete':'none'}})
    changes['department_id']=dept['id']
    changes['work_settings']={'expected_version':row['work_settings']['version'],'changes':{'ai_enabled':True}}
    apply(scoped,row,changes)
    after=get(scoped); settings=policy(scoped)
    assert after['department_id']==dept['id']
    assert after['explicit_permissions']==row['explicit_permissions']
    assert after['access_settings']['scoped_actions']['tasks']==changes['access_settings']['changes']['scopes']['tasks']
    assert settings['61']['pipeline_ids']==before['61']['pipeline_ids']
    assert settings['61']['notifications']==before['61']['notifications']
    assert settings['61']['ai_enabled'] is True
    assert settings['62']==before['62']
    assert members.member(A,61)['department_id']==dept['id']


def test_legacy_grant_remains_unconverted_on_unrelated_save(scoped):
    row=get(scoped)
    assert row['access_settings']['scoped_actions']['tasks']['view'] is None
    apply(scoped,row,{'display_name':'Updated name only'})
    assert get(scoped)['access_settings']['scoped_actions']['tasks']['view'] is None


@pytest.mark.parametrize('bad',[True,12,{},'',str(uuid.UUID(int=9999))])
def test_invalid_or_unavailable_department_rolls_back_all_card_changes(scoped,bad):
    row=get(scoped)
    with pytest.raises(members.MemberCardError):
        apply(scoped,row,{'department_id':bad,'display_name':'Must not persist',**patch(row,{'tasks':{'view':'none'}})})
    assert get(scoped)['card_version']==row['card_version']
    assert get(scoped)['display_name']==row['display_name']


def test_foreign_department_and_non_owner_or_self_assignment_denied(scoped):
    dept=create(scoped,tenant=B,owner=22); row=get(scoped)
    with pytest.raises(members.MemberCardError):apply(scoped,row,{'department_id':dept['id']})
    own=create(scoped)
    with pytest.raises(members.MemberCardError):apply(scoped,row,{'department_id':own['id']},owner=31)
    own_row=get(scoped,31)
    with pytest.raises(members.MemberCardError):apply(scoped,own_row,{'department_id':own['id']},owner=31)
    with pytest.raises(members.MemberCardError):apply(scoped,own_row,patch(own_row,{'tasks':{'view':'all'}}),owner=31)


def test_department_rename_version_and_archive_preserve_memberships(scoped):
    dept=create(scoped);row=get(scoped);apply(scoped,row,{'department_id':dept['id']})
    renamed=departments.upsert_department(tenant_id=A,owner_id=11,department_id=dept['id'],expected_version=dept['version'],name='Renamed')
    with pytest.raises(members.MemberCardError) as stale:
        departments.upsert_department(tenant_id=A,owner_id=11,department_id=dept['id'],expected_version=dept['version'],name='Stale')
    assert stale.value.code=='department_conflict'
    with pytest.raises(members.MemberCardError) as used:
        departments.upsert_department(tenant_id=A,owner_id=11,department_id=dept['id'],expected_version=renamed['version'],active=False)
    assert used.value.code=='department_in_use'
    assert get(scoped)['department_id']==dept['id']
    fresh=get(scoped);apply(scoped,fresh,{'department_id':None})
    archived=departments.upsert_department(tenant_id=A,owner_id=11,department_id=dept['id'],expected_version=renamed['version'],active=False)
    assert archived['active'] is False
    with pytest.raises(members.MemberCardError):apply(scoped,get(scoped),{'department_id':dept['id']})


def test_duplicate_name_and_concurrent_card_save_do_not_lose_changes(scoped):
    dept=create(scoped)
    with pytest.raises(members.MemberCardError) as duplicate:create(scoped,' support ')
    assert duplicate.value.code=='department_duplicate'
    row=get(scoped)
    def update(change):
        try:apply(scoped,row,change);return 'saved'
        except members.MemberCardError as error:return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(update,[{'department_id':dept['id']},patch(row,{'tasks':{'delete':'none'}})]))
    assert sorted(results)==['member_card_conflict','saved']


def test_scope_gate_off_rejects_changes_without_mutation(scoped,monkeypatch):
    monkeypatch.delenv('TENANT_SCOPED_ACCESS_ENABLED')
    row=get(scoped)
    assert row['access_settings']['scope_catalog']=={}
    with pytest.raises(members.MemberCardError):apply(scoped,row,patch(row,{'tasks':{'view':'all'}}))
    assert get(scoped)['card_version']==row['card_version']


def test_unmapped_dialog_source_blocks_narrow_scopes_atomically(scoped,monkeypatch):
    import tenant_dialog_access
    monkeypatch.setattr(tenant_dialog_access,'activation_preflight',lambda cur,tenant:{'ready':False,'unassigned_sources':[{'source_id':'test','name':'Test source'}],'kommo_source_mapping_required':False})
    row=get(scoped)
    with pytest.raises(members.MemberCardError) as blocked:
        apply(scoped,row,{'display_name':'Do not persist',**patch(row,{'dialogs':{'view':'department'}})})
    assert blocked.value.code=='dialog_source_mapping_required'
    assert get(scoped)['card_version']==row['card_version']
    assert get(scoped)['display_name']==row['display_name']
    apply(scoped,row,patch(row,{'dialogs':{'view':'none'}}))
    assert get(scoped)['access_settings']['scoped_actions']['dialogs']['view']=='none'


def test_first_scope_policy_activation_waits_at_tenant_fence(scoped):
    import time
    from tenant_scoped_access_store import read_access_profile
    with scoped['factory']() as conn:
        conn.execute("DELETE FROM saas_tenant_workflow_policies WHERE tenant_id=%s AND policy_key='members'",(A,))
    row=get(scoped)
    with scoped['factory']() as reader:
        with reader.cursor() as cur:
            assert read_access_profile(cur,A,61,lock=True)
        reader_pid=reader.info.backend_pid
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(apply,scoped,row,patch(row,{'tasks':{'view':'none'}}))
            blocked=False
            observed_queries=set()
            try:
                with scoped['factory']() as observer:
                    # Activity query text is cached for a transaction, whereas
                    # pg_blocking_pids observes current locks. Poll in separate
                    # transactions so an earlier schema read is not mistaken
                    # for the statement now waiting at the tenant fence.
                    observer.autocommit=True
                    deadline=time.monotonic()+5
                    while time.monotonic()<deadline and not pending.done():
                        result=observer.execute('''SELECT query FROM pg_stat_activity
                          WHERE datname=current_database() AND %s=ANY(pg_blocking_pids(pid))
                            AND wait_event_type='Lock' ''',(reader_pid,)).fetchall()
                        observed_queries.update(r['query'] for r in result)
                        if any('saas_tenants' in r['query'] and 'FOR UPDATE' in r['query'] for r in result):
                            blocked=True
                            break
                        time.sleep(.02)
                assert blocked,('First activation must wait on the tenant before creating its first policy row',
                                sorted(observed_queries))
            finally:
                reader.commit()
            pending.result(timeout=10)
    assert get(scoped)['access_settings']['scoped_actions']['tasks']['view']=='none'
