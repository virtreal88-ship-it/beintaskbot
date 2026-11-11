"""Synthetic, I/O-free checks for the deliberately inactive relation contract."""
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from tenant_crm_relation_contract import (
    BoundIdentity, ProviderBinding, ProviderExpectation, RecordRef,
    RelationContractError, RelationIntent, classify_observation,
)


TENANT = '10000000-0000-4000-8000-000000000001'
EVENT = '20000000-0000-4000-8000-000000000001'
DEAL = RecordRef('deal', '30000000-0000-4000-8000-000000000001')
CONTACT_A = RecordRef('contact', '40000000-0000-4000-8000-000000000001')
CONTACT_B = RecordRef('contact', '40000000-0000-4000-8000-000000000002')
COMPANY = RecordRef('company', '50000000-0000-4000-8000-000000000001')
BINDING = ProviderBinding(TENANT, 'kommo', 'synthetic-account', 'synthetic-generation-1')
REVISION = datetime(2026, 11, 11, 12, 0, 0, 123456, timezone.utc)
EXPECTED, DESIRED, OTHER = 'a' * 64, 'b' * 64, 'c' * 64


def identity(record, external_id, binding=BINDING):
    return BoundIdentity(record, external_id, binding)


def intent(**overrides):
    values = dict(event_id=EVENT, actor_id=42, source=CONTACT_B, relation='deal_contact',
                  changed=identity(DEAL, '101'), before=identity(CONTACT_A, '201'),
                  after=identity(CONTACT_B, '202'), local_revision=REVISION)
    values.update(overrides)
    return RelationIntent(**values)


def planned():
    # Synthetic digests test the classifier. No production Kommo/BEIN adapter
    # or approved relation semantics are implied by this fixture.
    return intent(provider_expectation=ProviderExpectation('synthetic-v1', EXPECTED, DESIRED))


def classify(event=None, **overrides):
    event = event or planned()
    values = dict(event_fingerprint=event.fingerprint(), binding=event.changed.binding,
                  contract_version='synthetic-v1', observed_digest=EXPECTED,
                  attempt_outcome='not_started')
    values.update(overrides)
    return classify_observation(event, **values)


def test_event_round_trip_is_strict_narrow_and_detached_from_mutable_payload():
    event = intent()
    payload = json.loads(json.dumps(event.to_payload()))
    assert RelationIntent.from_payload(payload) == event
    assert payload['source_action'] == 'update'
    assert payload['provider_expectation'] is None
    assert set(payload['changed']) == {'entity_type', 'internal_id', 'external_id'}
    assert not {'name', 'title', 'phone', 'email', 'description', 'pipeline_id',
                'stage_id', 'responsible_id', 'access_token', 'secrets'} & set(payload)
    payload['after']['external_id'] = '999'
    assert event.after.external_id == '202'
    with pytest.raises(FrozenInstanceError):
        event.actor_id = 99
    with pytest.raises(FrozenInstanceError):
        event.after.external_id = '999'


@pytest.mark.parametrize('source', [DEAL, CONTACT_B])
def test_deal_card_and_contact_card_share_narrow_change_with_distinct_source(source):
    event = intent(source=source)
    assert event.source == source
    assert event.changed.record == DEAL
    assert event.after.record == CONTACT_B


@pytest.mark.parametrize('source', [CONTACT_A, COMPANY])
@pytest.mark.parametrize('unlink', [False, True])
def test_company_contact_directions_preserve_changed_entity_and_exact_unlink(source, unlink):
    company = identity(COMPANY, '301')
    event = intent(source=source, relation='contact_company', changed=identity(CONTACT_A, '201'),
                   before=company if unlink else None, after=None if unlink else company)
    assert event.changed.record == CONTACT_A
    assert event.source == source
    assert RelationIntent.from_payload(event.to_payload()) == event


def test_contact_card_unlink_retains_exact_old_contact():
    event = intent(source=CONTACT_A, after=None)
    assert event.before.record == CONTACT_A and event.after is None
    with pytest.raises(RelationContractError, match='source_not_in_relation'):
        intent(source=CONTACT_B, after=None)
    with pytest.raises(RelationContractError, match='source_action_mismatch'):
        intent(source=CONTACT_A)


@pytest.mark.parametrize('changes', [
    {'tenant_id': '10000000-0000-4000-8000-000000000002'},
    {'provider': 'beinsystems'}, {'account_id': 'other-account'},
    {'connection_generation': 'synthetic-generation-2'},
])
@pytest.mark.parametrize('target', ['before', 'after'])
def test_mixed_tenant_provider_account_or_connection_identity_is_rejected(changes, target):
    event = intent()
    changed_identity = replace(getattr(event, target), binding=replace(BINDING, **changes))
    with pytest.raises(RelationContractError, match='relation_binding_mismatch'):
        replace(event, **{target: changed_identity})


@pytest.mark.parametrize('actor', [None, 0, -1, True, 42.0, '42'])
def test_exact_human_actor_is_required_without_integration_fallback(actor):
    with pytest.raises(RelationContractError, match='human_actor_required'):
        intent(actor_id=actor)


@pytest.mark.parametrize('changes,error', [
    ({'relation': 'company_deal'}, 'invalid_local_relation'),
    ({'changed': identity(CONTACT_A, '201')}, 'invalid_changed_entity'),
    ({'after': identity(COMPANY, '301')}, 'invalid_relation_target'),
    ({'source': COMPANY}, 'source_not_in_relation'),
    ({'before': None, 'after': None}, 'no_local_relation_change'),
    ({'before': identity(CONTACT_B, '999')}, 'no_local_relation_change'),
    ({'after': identity(CONTACT_B, '201')}, 'external_identity_collision'),
    ({'local_revision': datetime(2026, 11, 11)}, 'timezone_aware_revision_required'),
    ({'event_id': 'not-a-uuid'}, 'invalid_record_identity'),
])
def test_invalid_or_ambiguous_local_intent_is_rejected(changes, error):
    with pytest.raises(RelationContractError, match=error):
        intent(**changes)


@pytest.mark.parametrize('location,key', [
    (None, 'title'), ('source', 'phone'), ('changed', 'name'),
    ('before', 'email'), ('after', 'description'), ('binding', 'access_token'),
    ('provider_expectation', 'json_body'),
])
def test_payload_cannot_smuggle_record_fields_credentials_or_wire_requests(location, key):
    payload = planned().to_payload()
    target = payload if location is None else payload[location]
    target[key] = 'SYNTHETIC-NOT-A-SECRET'
    with pytest.raises(RelationContractError, match='invalid_relation_payload'):
        RelationIntent.from_payload(payload)


@pytest.mark.parametrize('key,value', [('version', 2), ('version', True),
                                      ('source_action', 'delete'), ('source_action', 'view')])
def test_unknown_contract_and_non_update_source_actions_are_rejected(key, value):
    payload = intent().to_payload()
    payload[key] = value
    with pytest.raises(RelationContractError, match='unsupported_relation_contract'):
        RelationIntent.from_payload(payload)


@pytest.mark.parametrize('value', ['', ' ', ' account', 'account ', 'account\nsecret', 101])
def test_binding_identifier_must_be_explicit_and_canonical(value):
    with pytest.raises(RelationContractError, match='invalid_binding_identifier'):
        replace(BINDING, connection_generation=value)


def test_fingerprint_is_canonical_and_not_just_event_id():
    event = planned()
    reverse_order = dict(reversed(list(event.to_payload().items())))
    assert RelationIntent.from_payload(reverse_order).fingerprint() == event.fingerprint()
    offset_revision = REVISION.astimezone(timezone(timedelta(hours=4)))
    assert replace(event, local_revision=offset_revision).fingerprint() == event.fingerprint()
    changes = [replace(event, actor_id=43), replace(event, source=DEAL),
               replace(event, local_revision=REVISION + timedelta(microseconds=1)),
               replace(event, before=None), replace(event, provider_expectation=None),
               replace(event, provider_expectation=ProviderExpectation('other-contract', EXPECTED, DESIRED))]
    assert all(other.event_id == event.event_id and other.fingerprint() != event.fingerprint()
               for other in changes)


def test_no_provider_semantics_or_digests_are_inferred_from_local_foreign_key():
    event = intent()
    assert event.provider_expectation is None
    assert classify(event) == 'provider_contract_missing'
    # Different possible full remote states remain distinct evidence. The
    # contract does not choose which one implements the local change.
    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    add = digest([{'id': 201, 'is_main': True}, {'id': 202, 'is_main': False}])
    replace_primary = digest([{'id': 201, 'is_main': False}, {'id': 202, 'is_main': True}])
    replace_all = digest([{'id': 202, 'is_main': True}])
    assert len({add, replace_primary, replace_all}) == 3
    assert len({replace(event, provider_expectation=ProviderExpectation('synthetic', EXPECTED, desired))
                .fingerprint() for desired in (add, replace_primary, replace_all)}) == 3


@pytest.mark.parametrize('attempt', ['not_started', 'definitely_not_sent', 'unknown', 'acknowledged'])
@pytest.mark.parametrize('observed,expected_result', [
    (None, 'observation_unknown'), (OTHER, 'relation_conflict'), (DESIRED, 'desired_observed'),
])
def test_reconciliation_does_not_replay_missing_conflicting_or_desired_state(attempt, observed, expected_result):
    assert classify(attempt_outcome=attempt, observed_digest=observed) == expected_result


@pytest.mark.parametrize('attempt,result', [
    ('not_started', 'expected_observed'), ('definitely_not_sent', 'expected_observed'),
    ('unknown', 'outcome_unknown'), ('acknowledged', 'outcome_unknown'),
])
def test_unchanged_read_after_timeout_or_acknowledgement_does_not_prove_safe_retry(attempt, result):
    assert classify(attempt_outcome=attempt) == result


@pytest.mark.parametrize('changes', [
    {'tenant_id': '10000000-0000-4000-8000-000000000002'},
    {'provider': 'beinsystems'}, {'account_id': 'other-account'},
    {'connection_generation': 'synthetic-generation-2'},
])
def test_read_back_from_another_connection_never_settles_this_event(changes):
    assert classify(binding=replace(BINDING, **changes), observed_digest=DESIRED) == 'connection_changed'


def test_old_event_or_contract_observation_never_settles_new_intent():
    event = planned()
    assert classify(event, event_fingerprint=replace(event, actor_id=43).fingerprint(),
                    observed_digest=DESIRED) == 'event_changed'
    assert classify(contract_version='old-contract', observed_digest=DESIRED) == 'provider_contract_changed'


@pytest.mark.parametrize('digest', ['', 'a' * 63, 'G' * 64, {}, None])
def test_invalid_state_evidence_cannot_be_interpreted_as_empty_relation(digest):
    with pytest.raises(RelationContractError, match='invalid_relation_digest'):
        ProviderExpectation('synthetic', digest, DESIRED)


def test_expired_lease_is_not_definitely_not_sent_evidence():
    with pytest.raises(RelationContractError, match='invalid_attempt_outcome'):
        classify(attempt_outcome='lease_expired')


def test_empty_remote_operation_is_observed_without_forcing_a_write():
    # Some approved adapter might determine that the full remote relation set
    # already satisfies a local change. Do not impose a remote cardinality rule.
    event = intent(provider_expectation=ProviderExpectation('synthetic-v1', EXPECTED, EXPECTED))
    assert classify(event) == 'desired_observed'
