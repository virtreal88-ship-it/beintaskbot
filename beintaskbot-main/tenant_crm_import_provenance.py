"""Server-only evidence from an existing Kommo read, never from JSON metadata.

This records the credentials actually used for the successful response, not the
connection selected when a job was queued. It authorizes no provider request and
is not evidence for adopting an existing native row or a legacy identity link.
"""
from dataclasses import dataclass
import re
import uuid

from tenant_crm_relation_contract import ProviderBinding, BoundIdentity, RecordRef
from tenant_crm_relation_identity import connection_binding, current_binding, bind_identity
from tenant_crm_native_access import CRMAccessError


@dataclass(frozen=True)
class ImportObservation:
    binding: ProviderBinding
    resource: str
    external_ids: frozenset[str]
    evidence_id: str


class ObservedKommoPage(dict):
    """Dict compatibility; evidence is never part of the provider JSON body."""
    def __init__(self, payload, observation):
        super().__init__(payload)
        self.observation = observation


def observe_page(tenant, resource, credentials, payload):
    """Called only by transport after a direct successful GET with no redirect."""
    if resource not in ('contacts', 'leads'):
        return payload
    binding = connection_binding(tenant, {
        'provider': credentials.get('provider'), 'status': 'connected',
        'account_domain': credentials.get('account_domain'),
        'connected_at': credentials.get('connected_at'),
    })
    if binding is None:
        return payload
    embedded = payload.get('_embedded')
    rows = embedded.get(resource) if isinstance(embedded, dict) else None
    if not isinstance(rows, list) or not rows or len(rows) > 250:
        return payload
    identities = []
    for row in rows:
        value = str(row.get('id') or '') if isinstance(row, dict) else ''
        if not re.fullmatch(r'[1-9][0-9]{0,18}', value):
            return payload
        identities.append(value)
    if len(set(identities)) != len(identities):
        return payload
    return ObservedKommoPage(payload, ImportObservation(binding, resource,
        frozenset(identities), 'kommo-read:' + str(uuid.uuid4())))


def validate_observation(cur, tenant, resource, rows, observation):
    """Hold the tenant/integration fence through import and mapping commit."""
    if (not isinstance(observation, ImportObservation)
            or observation.binding.tenant_id != str(tenant)
            or observation.binding.provider != 'kommo'
            or observation.resource != resource
            or any(str(row.get('id') or '') not in observation.external_ids for row in rows)):
        raise CRMAccessError('import_observation_mismatch', 409)
    if current_binding(cur, tenant) != observation.binding:
        raise CRMAccessError('relation_connection_changed', 409)


def register_inserted(cur, observation, entity, internal, external):
    """Only the branch that INSERTed this native row in this transaction calls us."""
    if observation is None:
        return
    if (not isinstance(observation, ImportObservation)
            or observation.resource != {'contact': 'contacts', 'deal': 'leads'}.get(entity)
            or str(external) not in observation.external_ids):
        raise CRMAccessError('import_observation_mismatch', 409)
    bind_identity(cur, BoundIdentity(RecordRef(entity, internal), str(external), observation.binding),
        provenance='imported', evidence_id=observation.evidence_id)
