"""Inactive, provider-neutral contract for future relation-only synchronization.

Nothing imports this module from a route, queue, migration or provider worker.
It performs no I/O and grants no permission to send. In particular, a local FK
change does not determine the provider's desired relation set. An independently
approved adapter must supply that interpretation and complete state digests.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
import uuid


class RelationContractError(ValueError):
    """Static errors only: never echo provider payloads or customer data."""


def _uuid(value):
    if not isinstance(value, str):
        raise RelationContractError('invalid_record_identity')
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise RelationContractError('invalid_record_identity') from None


def _text(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 240
            or value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise RelationContractError('invalid_binding_identifier')
    return value


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
        raise RelationContractError('invalid_relation_digest')
    return value


def _object(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise RelationContractError('invalid_relation_payload')
    return value


@dataclass(frozen=True)
class RecordRef:
    entity_type: str
    internal_id: str

    def __post_init__(self):
        if self.entity_type not in ('contact', 'deal', 'company'):
            raise RelationContractError('invalid_relation_entity')
        object.__setattr__(self, 'internal_id', _uuid(self.internal_id))


@dataclass(frozen=True)
class ProviderBinding:
    tenant_id: str
    provider: str
    account_id: str
    connection_generation: str

    def __post_init__(self):
        object.__setattr__(self, 'tenant_id', _uuid(self.tenant_id))
        if self.provider not in ('kommo', 'beinsystems'):
            raise RelationContractError('invalid_relation_provider')
        _text(self.account_id)
        _text(self.connection_generation)


@dataclass(frozen=True)
class BoundIdentity:
    record: RecordRef
    external_id: str
    binding: ProviderBinding

    def __post_init__(self):
        if not isinstance(self.record, RecordRef) or not isinstance(self.binding, ProviderBinding):
            raise RelationContractError('invalid_bound_identity')
        _text(self.external_id)


@dataclass(frozen=True)
class ProviderExpectation:
    """Adapter-owned full relation state, not hashes of the single local FK.

    The version identifies approved normalization and operation semantics.
    Digests must cover the complete relevant relation set and provider metadata
    (e.g. all Kommo contacts and main-contact flags), including empty states.
    This module cannot establish that an adapter has such an approved contract.
    """
    contract_version: str
    expected_digest: str
    desired_digest: str

    def __post_init__(self):
        _text(self.contract_version)
        _digest(self.expected_digest)
        _digest(self.desired_digest)


# These describe already implemented local FK changes, not provider support or
# new authorization. Company -> deal and create-time selection are not implied.
_RELATIONS = {'deal_contact': ('deal', 'contact'), 'contact_company': ('contact', 'company')}


@dataclass(frozen=True)
class RelationIntent:
    event_id: str
    actor_id: int
    source: RecordRef
    relation: str
    changed: BoundIdentity
    before: BoundIdentity | None
    after: BoundIdentity | None
    local_revision: datetime
    provider_expectation: ProviderExpectation | None = None

    def __post_init__(self):
        object.__setattr__(self, 'event_id', _uuid(self.event_id))
        if type(self.actor_id) is not int or self.actor_id <= 0:
            raise RelationContractError('human_actor_required')
        if not isinstance(self.source, RecordRef) or not isinstance(self.changed, BoundIdentity):
            raise RelationContractError('invalid_relation_source')
        if not isinstance(self.relation, str) or self.relation not in _RELATIONS:
            raise RelationContractError('invalid_local_relation')
        changed_type, target_type = _RELATIONS[self.relation]
        if self.changed.record.entity_type != changed_type:
            raise RelationContractError('invalid_changed_entity')
        targets = []
        for target in (self.before, self.after):
            if target is None:
                continue
            if not isinstance(target, BoundIdentity) or target.record.entity_type != target_type:
                raise RelationContractError('invalid_relation_target')
            if target.binding != self.changed.binding:
                raise RelationContractError('relation_binding_mismatch')
            targets.append(target)
        if (self.before.record if self.before else None) == (self.after.record if self.after else None):
            raise RelationContractError('no_local_relation_change')
        if len(targets) == 2 and targets[0].external_id == targets[1].external_id:
            raise RelationContractError('external_identity_collision')
        if self.source not in [self.changed.record, *(target.record for target in targets)]:
            raise RelationContractError('source_not_in_relation')
        # Reverse-card linking can assign only that source; reverse-card
        # unlinking can clear only that exact source, never another relation.
        if self.source != self.changed.record:
            target = self.after if self.after is not None else self.before
            if target.record != self.source:
                raise RelationContractError('source_action_mismatch')
        if (not isinstance(self.local_revision, datetime)
                or self.local_revision.tzinfo is None or self.local_revision.utcoffset() is None):
            raise RelationContractError('timezone_aware_revision_required')
        object.__setattr__(self, 'local_revision', self.local_revision.astimezone(timezone.utc))
        if self.provider_expectation is not None and not isinstance(self.provider_expectation, ProviderExpectation):
            raise RelationContractError('invalid_provider_expectation')

    def to_payload(self):
        def record(ref):
            return {'entity_type': ref.entity_type, 'internal_id': ref.internal_id}
        def identity(ref):
            return None if ref is None else {**record(ref.record), 'external_id': ref.external_id}
        binding, expectation = self.changed.binding, self.provider_expectation
        return {'version': 1, 'event_id': self.event_id, 'actor_id': self.actor_id,
                'source': record(self.source), 'source_action': 'update',
                'binding': {'tenant_id': binding.tenant_id, 'provider': binding.provider,
                            'account_id': binding.account_id,
                            'connection_generation': binding.connection_generation},
                'relation': self.relation, 'changed': identity(self.changed),
                'before': identity(self.before), 'after': identity(self.after),
                'local_revision': self.local_revision.isoformat(),
                'provider_expectation': None if expectation is None else {
                    'contract_version': expectation.contract_version,
                    'expected_digest': expectation.expected_digest,
                    'desired_digest': expectation.desired_digest}}

    @classmethod
    def from_payload(cls, value):
        data = _object(value, ('version', 'event_id', 'actor_id', 'source', 'source_action',
                              'binding', 'relation', 'changed', 'before', 'after',
                              'local_revision', 'provider_expectation'))
        if type(data['version']) is not int or data['version'] != 1 or data['source_action'] != 'update':
            raise RelationContractError('unsupported_relation_contract')
        binding = ProviderBinding(**_object(data['binding'],
            ('tenant_id', 'provider', 'account_id', 'connection_generation')))
        source = RecordRef(**_object(data['source'], ('entity_type', 'internal_id')))
        def identity(value):
            if value is None:
                return None
            row = _object(value, ('entity_type', 'internal_id', 'external_id'))
            return BoundIdentity(RecordRef(row['entity_type'], row['internal_id']), row['external_id'], binding)
        if not isinstance(data['local_revision'], str):
            raise RelationContractError('invalid_local_revision')
        try:
            revision = datetime.fromisoformat(data['local_revision'])
        except ValueError:
            raise RelationContractError('invalid_local_revision') from None
        expectation = data['provider_expectation']
        if expectation is not None:
            expectation = ProviderExpectation(**_object(expectation,
                ('contract_version', 'expected_digest', 'desired_digest')))
        return cls(data['event_id'], data['actor_id'], source, data['relation'], identity(data['changed']),
                   identity(data['before']), identity(data['after']), revision, expectation)

    def fingerprint(self):
        """Content identity for collision checks, NOT provider idempotency."""
        encoded = json.dumps(self.to_payload(), sort_keys=True, separators=(',', ':'), ensure_ascii=True)
        return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def classify_observation(intent, *, event_fingerprint, binding, contract_version,
                         observed_digest, attempt_outcome):
    """Classify read-back evidence, never authorize a write or automatic retry.

    'expected_observed' is not send-ready: current source-card U, tenant and
    membership, policy, mappings, local revision and a provider concurrency
    strategy still need transactional revalidation. Even a desired observation
    does not prove that this event caused it or settle an in-flight request.
    """
    if not isinstance(intent, RelationIntent) or not isinstance(binding, ProviderBinding):
        raise RelationContractError('invalid_observation_binding')
    if attempt_outcome not in ('not_started', 'definitely_not_sent', 'unknown', 'acknowledged'):
        raise RelationContractError('invalid_attempt_outcome')
    _digest(event_fingerprint)
    if observed_digest is not None:
        _digest(observed_digest)
    if event_fingerprint != intent.fingerprint():
        return 'event_changed'
    if binding != intent.changed.binding:
        return 'connection_changed'
    expectation = intent.provider_expectation
    if expectation is None:
        return 'provider_contract_missing'
    if contract_version != expectation.contract_version:
        return 'provider_contract_changed'
    if observed_digest is None:
        return 'observation_unknown'
    if observed_digest == expectation.desired_digest:
        return 'desired_observed'
    if observed_digest != expectation.expected_digest:
        return 'relation_conflict'
    # An expired lease or a timeout is not proof the request was never sent.
    # An unchanged read may race the first request; never infer safe replay.
    if attempt_outcome in ('unknown', 'acknowledged'):
        return 'outcome_unknown'
    return 'expected_observed'
