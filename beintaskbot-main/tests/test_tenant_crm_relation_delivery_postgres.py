"""Isolated PostgreSQL/WASM journal tests with conditional provider mock only.

The mock's atomic compare/apply is deliberately stronger than Kommo HTTP. These
checks do NOT prove that production Kommo supports conditional relationship I/O.
"""
import json
from datetime import datetime
import uuid
import pytest

from test_tenant_support_inbox import database, db, A, B
from tenant_crm_relation_contract import BoundIdentity, RecordRef, ProviderExpectation
from tenant_crm_relation_identity import current_binding, bind_identity
from tenant_crm_relation_kommo import CONTRACT_VERSION, digest
from tenant_crm_relation_store import change_relation
from tenant_crm_relation_delivery import drive
from tenant_crm_native_access import CRMAccessError, require_local_relation


@pytest.fixture
def graph(db, monkeypatch):
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'true')
    # The WASM bridge returns ISO timestamps; mirror psycopg's native values.
    original = getattr(db, 'query', None)
    def query(sql, params=()):
        rows = original(sql, params)
        for row in rows:
            for key in ('updated_at', 'connected_at'):
                if isinstance(row.get(key), str):
                    row[key] = datetime.fromisoformat(row[key].replace('Z', '+00:00'))
        return rows
    if original:
        monkeypatch.setattr(db, 'query', query)
    db.execute("UPDATE saas_tenants SET modules='{\"customers\":true,\"deals\":true}' WHERE id=%s::uuid", (A,))
    db.execute('''INSERT INTO saas_tenant_members(tenant_id,telegram_id,role,permissions)
      VALUES(%s::uuid,81,'admin','["customers","deals"]')''', (A,))
    matrix = {kind: {action: ('self' if action == 'update' else 'none')
        for action in ('view', 'create', 'update', 'delete')} for kind in ('customers', 'deals')}
    db.execute('''INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key,value)
      VALUES(%s::uuid,'members',%s::jsonb)''', (A, json.dumps({'81': {'access_scopes': matrix}})))
    db.execute('''INSERT INTO saas_tenant_integrations(tenant_id,provider,status,account_domain,connected_at)
      VALUES(%s::uuid,'kommo','connected','synthetic.kommo.com',clock_timestamp())''', (A,))
    db.execute('''INSERT INTO saas_crm_provider_settings(tenant_id,entity_type,provider,mode,primary_provider,
      sync_direction,allowed_fields) VALUES(%s::uuid,'deal','kommo','beintaskbot','beintaskbot',
      'to_external','["contact_id"]')''', (A,))
    db.execute("INSERT INTO saas_tenant_pipelines(tenant_id,pipeline_id,name) VALUES(%s::uuid,'p','Pipeline')", (A,))
    db.execute("INSERT INTO saas_tenant_pipeline_stages(tenant_id,pipeline_id,stage_id,name) VALUES(%s::uuid,'p','s','Stage')", (A,))
    contacts = [RecordRef('contact', str(uuid.uuid4())) for _ in range(3)]
    deal = RecordRef('deal', str(uuid.uuid4()))
    for contact in contacts:
        db.execute('''INSERT INTO saas_crm_native_contacts(id,tenant_id,display_name,access_responsible_id)
          VALUES(%s::uuid,%s::uuid,'Synthetic',81)''', (contact.internal_id, A))
    db.execute('''INSERT INTO saas_crm_native_deals(id,tenant_id,title,pipeline_id,stage_id,contact_id,access_responsible_id)
      VALUES(%s::uuid,%s::uuid,'Synthetic','p','s',%s::uuid,81)''', (deal.internal_id, A, contacts[0].internal_id))
    with db.cursor() as cur:
        binding = current_binding(cur, A)
        for record, external in [(deal, '10'), *zip(contacts, ('1','2','3'))]:
            bind_identity(cur, BoundIdentity(record, external, binding), provenance='imported', evidence_id='synthetic-import')
    db.commit()
    return {'db': db, 'binding': binding, 'deal': deal, 'contacts': contacts}


def enqueue(graph, *, source=None, before=None, after=None):
    db, deal = graph['db'], graph['deal']
    initial, desired = {'1': True, '2': False}, {'2': False, '3': True}
    revision = db.execute('SELECT updated_at FROM saas_crm_native_deals WHERE id=%s::uuid', (deal.internal_id,))[0]['updated_at']
    with db.cursor() as cur:
        return change_relation(cur, tenant=A, actor=81, source=source or deal, changed=deal,
            relation='deal_contact', before=before or graph['contacts'][0], after=after or graph['contacts'][2],
            expected_revision=revision,
            expectation=ProviderExpectation(CONTRACT_VERSION, digest(initial, 'deal_contact'), digest(desired, 'deal_contact')))


class ConditionalMock:
    def __init__(self, binding):
        self.binding, self.state, self.calls = binding, {'1': True, '2': False}, []
        self.timeout, self.crash, self.concurrent = False, False, False
    def observe(self, binding, relation, changed_id):
        assert binding == self.binding and relation == 'deal_contact' and changed_id == '10'
        return dict(self.state)
    def compare_and_apply(self, binding, relation, expected, request):
        assert binding == self.binding
        if self.concurrent:
            self.state = {'1': False, '2': True}
        if self.state != expected:
            return 'conflict'
        method, path, body = request
        assert method == 'POST' and path.startswith('leads/10/') and len(body) == 1
        self.calls.append(request)
        key = str(body[0]['to_entity_id'])
        if path.endswith('/link'):
            assert body[0]['metadata'] == {'is_main': True}
            self.state = {key: False for key in self.state}
            self.state[key] = True
        else:
            assert path.endswith('/unlink') and 'metadata' not in body[0]
            del self.state[key]
        if self.crash: raise KeyboardInterrupt('simulated process loss')
        if self.timeout: raise TimeoutError('simulated timeout after apply')
        return 'applied'


def result(graph, event):
    return graph['db'].execute('SELECT * FROM saas_crm_relation_outbox WHERE id=%s::uuid', (event,))[0]


def test_atomic_change_and_journal_rollback(graph):
    intent = enqueue(graph)
    assert result(graph, intent.event_id)['intent_hash'] == intent.fingerprint()
    graph['db'].rollback()
    assert not graph['db'].execute('SELECT id FROM saas_crm_relation_outbox')
    row = graph['db'].execute('SELECT contact_id FROM saas_crm_native_deals WHERE id=%s::uuid', (graph['deal'].internal_id,))[0]
    assert str(row['contact_id']) == graph['contacts'][0].internal_id


def test_dormant_production_path_and_existing_409(graph):
    intent = enqueue(graph); graph['db'].commit()
    assert drive(lambda: graph['db'], intent.event_id) == 'disabled'
    with graph['db'].cursor() as cur, pytest.raises(CRMAccessError, match='scoped_relation_sync_not_configured'):
        require_local_relation(cur, A, 'deal')


def test_two_steps_preserve_secondary_and_duplicate_delivery_is_noop(graph):
    intent = enqueue(graph); graph['db'].commit()
    adapter = ConditionalMock(graph['binding'])
    assert drive(lambda: graph['db'], intent.event_id, adapter=adapter) == 'pending'
    assert drive(lambda: graph['db'], intent.event_id, adapter=adapter) == 'synced'
    assert adapter.state == {'2': False, '3': True}
    assert len(adapter.calls) == 2
    assert drive(lambda: graph['db'], intent.event_id, adapter=adapter) == 'synced'
    assert len(adapter.calls) == 2


@pytest.mark.parametrize('failure', ['timeout', 'crash'])
def test_uncertain_after_apply_is_never_replayed_or_followed_by_unlink(graph, failure):
    intent = enqueue(graph); graph['db'].commit()
    adapter = ConditionalMock(graph['binding']); setattr(adapter, failure, True)
    if failure == 'crash':
        with pytest.raises(KeyboardInterrupt): drive(lambda: graph['db'], intent.event_id, adapter=adapter)
    else:
        assert drive(lambda: graph['db'], intent.event_id, adapter=adapter) == 'unknown'
    assert drive(lambda: graph['db'], intent.event_id, adapter=adapter) == 'unknown'
    assert len(adapter.calls) == 1
    assert adapter.state == {'1': False, '2': False, '3': True}


@pytest.mark.parametrize('mutation', [
    "UPDATE saas_tenant_members SET active=false WHERE telegram_id=81",
    "UPDATE saas_tenants SET status='disabled'",
    "UPDATE saas_tenant_integrations SET connected_at=connected_at+interval '1 second'",
    "UPDATE saas_tenant_integrations SET account_domain='other.kommo.com'",
    "UPDATE saas_tenant_integrations SET status='disconnected'",
    "UPDATE saas_crm_provider_settings SET sync_direction='off'",
    "UPDATE saas_crm_relation_identities SET external_id='99' WHERE entity_type='deal'",
    "UPDATE saas_crm_native_deals SET access_responsible_id=11",
    "UPDATE saas_crm_native_deals SET updated_at=updated_at+interval '1 second'",
    "UPDATE saas_crm_native_contacts SET archived_at=now()",
])
def test_dispatch_revalidates_every_fence(graph, mutation):
    intent = enqueue(graph); graph['db'].commit()
    graph['db'].execute(mutation); graph['db'].commit()
    adapter = ConditionalMock(graph['binding'])
    assert drive(lambda: graph['db'], intent.event_id, adapter=adapter) in ('disabled', 'superseded')
    assert adapter.calls == []


def test_remote_concurrent_edit_is_conditional_conflict_without_overwrite(graph):
    intent = enqueue(graph); graph['db'].commit()
    adapter = ConditionalMock(graph['binding']); adapter.concurrent = True
    assert drive(lambda: graph['db'], intent.event_id, adapter=adapter) == 'conflict'
    assert adapter.calls == [] and adapter.state == {'1': False, '2': True}


def test_reverse_contact_source_requires_only_its_update_not_deal_update(graph):
    graph['db'].execute('UPDATE saas_crm_native_deals SET access_responsible_id=11')
    intent = enqueue(graph, source=graph['contacts'][2]); graph['db'].commit()
    adapter = ConditionalMock(graph['binding'])
    assert drive(lambda: graph['db'], intent.event_id, adapter=adapter) == 'pending'
    graph['db'].execute('UPDATE saas_crm_native_contacts SET access_responsible_id=11'); graph['db'].commit()
    assert drive(lambda: graph['db'], intent.event_id, adapter=adapter) == 'disabled'
    assert len(adapter.calls) == 1  # Revocation between steps prevents unlink.


def test_mapping_never_adopts_legacy_id_after_reconnect(graph):
    graph['db'].execute("UPDATE saas_tenant_integrations SET connected_at=connected_at+interval '1 second'")
    with pytest.raises(CRMAccessError, match='relation_mapping_unverified'): enqueue(graph)


def test_owner_verification_rejects_nonowner_and_cross_tenant_mapping(graph):
    with graph['db'].cursor() as cur, pytest.raises(CRMAccessError, match='relation_identity_owner_required'):
        bind_identity(cur, BoundIdentity(graph['deal'], '10', graph['binding']),
                      provenance='owner_verified', evidence_id='assertion', verified_by=81)
    foreign = RecordRef('contact', str(uuid.uuid4()))
    graph['db'].execute("INSERT INTO saas_crm_native_contacts(id,tenant_id,display_name) VALUES(%s::uuid,%s::uuid,'Foreign')", (foreign.internal_id, B))
    with graph['db'].cursor() as cur, pytest.raises(CRMAccessError, match='relation_resource_unavailable'):
        bind_identity(cur, BoundIdentity(foreign, '99', graph['binding']), provenance='imported', evidence_id='foreign')


def test_deal_card_cannot_bypass_relation_gate_via_full_record_export(graph, monkeypatch):
    import tenant_crm_native_deals as deals
    monkeypatch.setattr(deals, '_connect', lambda: graph['db'])
    with pytest.raises(CRMAccessError, match='scoped_relation_sync_not_configured'):
        deals.update_deal(A, graph['deal'].internal_id, 'Synthetic', 'p', 's',
                          contact_id=graph['contacts'][2].internal_id, actor_id=81)
    row = graph['db'].execute('SELECT contact_id FROM saas_crm_native_deals WHERE id=%s::uuid',
                              (graph['deal'].internal_id,))[0]
    assert str(row['contact_id']) == graph['contacts'][0].internal_id
    assert not graph['db'].execute('SELECT id FROM saas_crm_sync_outbox')


def test_policy_revocation_between_two_successful_steps_prevents_detach(graph):
    intent = enqueue(graph); graph['db'].commit()
    adapter = ConditionalMock(graph['binding'])
    assert drive(lambda: graph['db'], intent.event_id, adapter=adapter) == 'pending'
    graph['db'].execute("""UPDATE saas_tenant_workflow_policies SET value=jsonb_set(value,
      '{81,access_scopes,deals,update}', '\"none\"'::jsonb) WHERE policy_key='members'""")
    graph['db'].commit()
    assert drive(lambda: graph['db'], intent.event_id, adapter=adapter) == 'disabled'
    assert len(adapter.calls) == 1 and '1' in adapter.state


@pytest.mark.parametrize('scoped,change_contact', [(False, False), (False, True), (True, False)])
def test_unrelated_edit_and_gate_off_legacy_relation_behavior_preserved(graph, monkeypatch, scoped, change_contact):
    import tenant_crm_native_deals as deals
    monkeypatch.setenv('TENANT_SCOPED_ACCESS_ENABLED', 'true' if scoped else 'false')
    monkeypatch.setattr(deals, '_connect', lambda: graph['db'])
    contact = graph['contacts'][2 if change_contact else 0].internal_id
    row = deals.update_deal(A, graph['deal'].internal_id, 'Changed scalar', 'p', 's',
                           contact_id=contact, actor_id=81)
    assert row['title'] == 'Changed scalar' and str(row['contact_id']) == contact
