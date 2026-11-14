"""Real isolated PostgreSQL/WASM stage mutation contracts (no provider/network).

Uses the same real scoped scalar store and schema as the existing CRM tests.
Does not claim to test native PostgreSQL concurrency from a single WASM session.
"""
import json
import pytest
from test_tenant_support_inbox import database, db, A, B
from test_tenant_crm_relation_delivery_postgres import graph
from test_crm_scoped_scalar_postgres import scalar
import tenant_crm_native_deals as deals
from tenant_crm_native_access import CRMAccessError


@pytest.fixture
def stage_move(scalar):
    db = scalar['db']
    db.execute("INSERT INTO saas_tenant_pipeline_stages(tenant_id,pipeline_id,stage_id,name) VALUES(%s::uuid,'p','next','Next')", (A,))
    db.execute("INSERT INTO saas_tenant_pipelines(tenant_id,pipeline_id,name) VALUES(%s::uuid,'other','Other')", (A,))
    db.execute("INSERT INTO saas_tenant_pipeline_stages(tenant_id,pipeline_id,stage_id,name) VALUES(%s::uuid,'other','foreign','Other stage')", (A,))
    db.execute("UPDATE saas_crm_native_deals SET description='Keep description',responsible_id=11 WHERE id=%s::uuid", (scalar['deal'].internal_id,))
    db.execute("UPDATE saas_crm_provider_settings SET allowed_fields='[\"title\",\"pipeline_id\",\"stage_id\"]' WHERE tenant_id=%s::uuid AND entity_type='deal'", (A,))
    db.commit()
    return scalar


def row(f):
    return f['db'].execute('SELECT * FROM saas_crm_native_deals WHERE tenant_id=%s::uuid AND id=%s::uuid', (A, f['deal'].internal_id))[0]


def move(f, stage='next', *, revision=None, actor=81, tenant=A):
    revision = revision or row(f)['updated_at'].isoformat()
    f['db'].commit()
    return deals.move_deal_stage(tenant, f['deal'].internal_id, stage, expected_updated_at=revision, actor_id=actor)


def test_only_stage_and_revision_change_and_scalar_receipt_is_narrow(stage_move):
    f = stage_move; before = row(f)
    result = move(f)
    after = row(f)
    assert result['stage_id'] == 'next' and after['updated_at'] > before['updated_at']
    assert {k:v for k,v in before.items() if k not in {'stage_id','updated_at'}} == {k:v for k,v in after.items() if k not in {'stage_id','updated_at'}}
    event = f['db'].execute('SELECT * FROM saas_crm_scalar_outbox WHERE id=%s::uuid', (result['sync']['event_id'],))[0]
    assert event['actor_id'] == 81 and event['field_mask'] == ['stage_id']
    assert event['before_values'] == {'stage_id':'s'} and event['values'] == {'stage_id':'next'}
    assert event['state'] == 'needs_verification' and event['error_code'] == 'scalar_catalog_mapping_unverified'
    assert f['db'].execute('SELECT id FROM saas_crm_native_tasks') == []
    assert f['db'].execute('SELECT id FROM saas_crm_sync_outbox') == []


def test_stale_cas_does_not_overwrite_newer_fields_or_repeat_automation(stage_move):
    f = stage_move; old = row(f)['updated_at'].isoformat()
    f['db'].execute("UPDATE saas_crm_native_deals SET title='Newer title',updated_at=clock_timestamp() WHERE id=%s::uuid", (f['deal'].internal_id,)); f['db'].commit()
    before = row(f); f['db'].commit()
    with pytest.raises(CRMAccessError) as caught: move(f, revision=old)
    assert caught.value.status == 409
    assert row(f) == before
    assert f['db'].execute('SELECT id FROM saas_crm_scalar_outbox') == []
    assert f['db'].execute('SELECT id FROM saas_crm_native_tasks') == []


@pytest.mark.parametrize('denial', ['wrong_owner','inactive_actor','revoked_update','foreign_tenant','archived','other_pipeline','inactive_pipeline'])
def test_current_actor_tenant_and_pipeline_denials_preserve_rows(stage_move, denial):
    f = stage_move; db = f['db']
    if denial == 'wrong_owner': db.execute('UPDATE saas_crm_native_deals SET access_responsible_id=11')
    if denial == 'inactive_actor': db.execute('UPDATE saas_tenant_members SET active=false WHERE telegram_id=81')
    if denial == 'revoked_update': db.execute("UPDATE saas_tenant_workflow_policies SET value=jsonb_set(value,'{81,access_scopes,deals,update}','\"none\"') WHERE policy_key='members'")
    if denial == 'archived': db.execute('UPDATE saas_crm_native_deals SET archived_at=clock_timestamp()')
    if denial == 'inactive_pipeline': db.execute("UPDATE saas_tenant_pipelines SET active=false WHERE pipeline_id='p'")
    db.commit(); before = row(f); db.commit()
    try:
        result = move(f, stage='foreign' if denial == 'other_pipeline' else 'next', tenant=B if denial == 'foreign_tenant' else A)
        assert result is None
    except (CRMAccessError, ValueError):
        pass
    assert row(f) == before
    assert db.execute('SELECT id FROM saas_crm_scalar_outbox') == []
    assert db.execute('SELECT id FROM saas_crm_native_tasks') == []


def test_stage_entry_automation_runs_once_and_noop_keeps_revision(stage_move):
    f = stage_move; db = f['db']
    db.execute("UPDATE saas_tenants SET modules=modules||'{\"tasks\":true}'::jsonb WHERE id=%s::uuid", (A,))
    trigger = {'native_task': {'enabled':True, 'text':'Synthetic stage followup', 'responsible_id':11, 'due_days':1}}
    db.execute("UPDATE saas_tenant_pipeline_stages SET settings=%s::jsonb WHERE tenant_id=%s::uuid AND pipeline_id='p' AND stage_id='next'", (json.dumps(trigger),A)); db.commit()
    first = move(f)
    tasks = db.execute('SELECT * FROM saas_crm_native_tasks')
    assert len(tasks) == 1 and tasks[0]['text'] == 'Synthetic stage followup'
    assert str(tasks[0]['deal_id']) == f['deal'].internal_id
    second = move(f, revision=first['updated_at'].isoformat())
    assert second['updated_at'] == first['updated_at']
    assert db.execute('SELECT * FROM saas_crm_native_tasks') == tasks
    assert len(db.execute('SELECT id FROM saas_crm_scalar_outbox')) == 1
