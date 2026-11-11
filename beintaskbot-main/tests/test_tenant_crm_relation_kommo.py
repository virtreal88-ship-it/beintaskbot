"""Pure planning tests: no provider network or credentials."""
import pytest
from tenant_crm_relation_kommo import plan, snapshot, digest, RelationConflict


def test_replace_main_preserves_all_secondaries_and_uses_narrow_payloads():
    p = plan('deal_contact', '10', '1', '3', {'1': True, '2': False, '3': False})
    assert p.states == ({'1': True, '2': False, '3': False},
                        {'1': False, '2': False, '3': True}, {'2': False, '3': True})
    assert p.request(0) == ('POST', 'leads/10/link', [
        {'to_entity_id': 3, 'to_entity_type': 'contacts', 'metadata': {'is_main': True}}])
    assert p.request(1) == ('POST', 'leads/10/unlink', [{'to_entity_id': 1, 'to_entity_type': 'contacts'}])
    assert p.read_path() == 'leads/10/links'


def test_unlink_never_deletes_secondary_contacts():
    p = plan('deal_contact', '10', '1', None, {'1': True, '2': False})
    assert p.states[-1] == {'2': False}
    assert len(p.steps) == 1 and p.steps[0].target_id == '1'


def test_company_replacement_is_two_explicit_steps():
    p = plan('contact_company', '7', '1', '2', {'1': False})
    assert p.states == ({'1': False}, {}, {'2': False})
    assert p.request(0) == ('POST', 'contacts/7/unlink', [{'to_entity_id': 1, 'to_entity_type': 'companies'}])
    assert p.request(1) == ('POST', 'contacts/7/link', [{'to_entity_id': 2, 'to_entity_type': 'companies'}])


@pytest.mark.parametrize('payload', [{}, {'_embedded': {'links': None}},
    {'_embedded': {'links': []}, '_links': {'next': {'href': 'next'}}},
    {'_embedded': {'links': []}, '_links': []},
    {'_embedded': {'links': []}, '_total_items': 2},
    {'_embedded': {'links': [{'to_entity_id': 1, 'to_entity_type': 'contacts'}]}},
    {'_embedded': {'links': [{'to_entity_id': 1, 'to_entity_type': 'contacts', 'metadata': {'main_contact': 1}}]}}])
def test_incomplete_or_ambiguous_remote_graph_is_not_empty(payload):
    with pytest.raises(RelationConflict): snapshot(payload, 'deal_contact', '10')


def test_snapshot_main_metadata_is_part_of_full_digest():
    raw = {'_embedded': {'links': [
        {'to_entity_id': 1, 'to_entity_type': 'contacts', 'metadata': {'main_contact': True}},
        {'to_entity_id': 2, 'to_entity_type': 'contacts', 'metadata': {'main_contact': False}}]}}
    assert snapshot(raw, 'deal_contact', '10') == {'1': True, '2': False}
    assert digest({'1': True, '2': False}, 'deal_contact') != digest({'1': False, '2': True}, 'deal_contact')
    assert digest({'1': True}, 'deal_contact') != digest({'1': True, '2': False}, 'deal_contact')


@pytest.mark.parametrize('before,after,initial', [('1', '3', {'2': True}),
    (None, '3', {'2': True}), ('1', '3', {'1': True, '2': True})])
def test_conflicting_main_never_plans_write(before, after, initial):
    with pytest.raises(RelationConflict): plan('deal_contact', '10', before, after, initial)


def test_binding_refresh_stable_reconnect_distinct_and_malformed_generation_closed():
    from datetime import datetime, timezone, timedelta
    from tenant_crm_relation_identity import connection_binding
    from uuid import UUID
    tenant = str(UUID(int=1)); stamp = datetime.now(timezone.utc)
    row = {'provider': 'kommo', 'status': 'connected', 'account_domain': 'synthetic.kommo.com', 'connected_at': stamp}
    first = connection_binding(tenant, row)
    assert connection_binding(tenant, {**row, 'updated_at': stamp + timedelta(seconds=1)}) == first
    assert connection_binding(tenant, {**row, 'connected_at': stamp + timedelta(seconds=1)}) != first
    assert connection_binding(tenant, {**row, 'account_domain': 'other.kommo.com'}) != first
    assert connection_binding(tenant, {**row, 'provider': 'kommo_manual'}) != first
    for changed in ({'connected_at': 'bad'}, {'connected_at': stamp.replace(tzinfo=None)},
                    {'status': 'disconnected'}, {'provider': 'beinsystems'}):
        assert connection_binding(tenant, {**row, **changed}) is None
