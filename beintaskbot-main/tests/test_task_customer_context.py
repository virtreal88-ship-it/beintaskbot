"""Bounded customer enrichment never treats task visibility as contact authority."""
from unittest.mock import Mock

import tenant_task_flow as flow

TENANT = '00000000-0000-0000-0000-000000000001'


def profile(**extra):
    return dict(tenant_id=TENANT, telegram_id=10, active=True, role='operator',
                permissions=['tasks'], modules={'tasks': True, 'customers': True}, **extra)


def test_empty_customer_references_do_not_query():
    cur = Mock()
    rows = [{'id': '1', 'contact_name': 'Unverified legacy name'}]
    flow._task_customers(cur, profile(), 'native', rows)
    assert rows == [{'id': '1', 'contact_name': '', 'phone': ''}]
    cur.execute.assert_not_called()


def test_native_customer_query_is_bounded_scoped_and_archival_safe():
    cur = Mock()
    cur.fetchall.return_value = []
    rows = [{'id': str(i), 'contact_id': TENANT} for i in range(100)]
    flow._task_customers(cur, profile(), 'native', rows)
    sql, args = cur.execute.call_args.args
    assert 'c.tenant_id=%s::uuid' in sql and 'c.archived_at IS NULL' in sql
    assert 'c.id=ANY(%s::uuid[])' in sql and 'FALSE' in sql
    assert args == [TENANT, [TENANT]]
    cur.execute.assert_called_once()
    assert all(row['contact_name'] == row['phone'] == '' for row in rows)


def test_kommo_task_marker_is_not_customer_access():
    cur = Mock()
    cur.fetchall.return_value = []
    rows = [{'id': '1', 'kommo_lead_id': '500'}]
    flow._task_customers(cur, profile(), 'kommo', rows)
    sql, args = cur.execute.call_args.args
    assert 'tenant_id = %s::uuid' in sql and 'deleted_at IS NULL' in sql
    assert 'FALSE' in sql and 'kommo_lead_id=ANY(%s::bigint[])' in sql
    assert args == [TENANT, ['500']]
    assert rows[0]['phone'] == rows[0]['contact_name'] == ''
    assert 'deal_title' not in rows[0]


def test_authorized_cached_customer_is_mapped_by_exact_link():
    cur = Mock()
    cur.fetchall.return_value = [{'id': 500, 'contact_name': 'Synthetic client',
                                 'phone': '+994000000000', 'deal_title': 'Test deal'}]
    rows = [{'id': '1', 'kommo_lead_id': '500'}, {'id': '2', 'kommo_lead_id': '501'}]
    owner = profile(); owner['role'] = 'owner'
    flow._task_customers(cur, owner, 'kommo', rows)
    assert rows[0]['contact_name'] == 'Synthetic client'
    assert rows[0]['phone'] == '+994000000000' and rows[0]['deal_title'] == 'Test deal'
    assert rows[1]['phone'] == rows[1]['contact_name'] == ''
    cur.execute.assert_called_once()


def test_kommo_customer_filter_reuses_pipeline_stage_and_creator_scope():
    cur = Mock(); cur.fetchall.return_value = []
    member = profile(workflow={
        'policies': {'members': {'10': {'pipeline_ids': ['20'], 'kommo_user_id': 777}}},
        'pipelines': [{'pipeline_id': '20'}, {'pipeline_id': '30'}],
        'stages': [{'pipeline_id': '20', 'stage_id': '201'},
                   {'pipeline_id': '20', 'stage_id': '202', 'settings': {'visible': False}}],
    })
    flow._task_customers(cur, member, 'kommo', [{'id': '1', 'kommo_lead_id': '500'}])
    sql, args = cur.execute.call_args.args
    assert 'pipeline_id = %s AND status_id = ANY(%s)' in sql
    assert "raw->>'created_by_id'=%s" in sql and "raw->>'created_by'=%s" in sql
    assert args == [TENANT, 20, [201], '777', '777', ['500']]


def test_native_authorized_customer_name_and_phone_use_exact_contact_id():
    cur = Mock()
    cur.fetchall.return_value = [{'id': TENANT, 'contact_name': 'Allowed native', 'phone': '+994000000004'}]
    owner = profile(); owner['role'] = 'owner'
    rows = [{'id': '1', 'contact_id': TENANT}]
    flow._task_customers(cur, owner, 'native', rows)
    assert rows[0]['contact_name'] == 'Allowed native' and rows[0]['phone'] == '+994000000004'
    sql, args = cur.execute.call_args.args
    assert 'FALSE' not in sql and args[0] == TENANT and args[1] == [TENANT]
