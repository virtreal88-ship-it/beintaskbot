"""Relation-only Kommo planning. Never PATCH or rebuild a full CRM record.

Request metadata uses is_main (official PHP SDK ContactModel.getMetadataForLink),
whereas GET /links returns metadata.main_contact. All other contacts are retained.
"""
from dataclasses import dataclass
import re
import hashlib
import json


CONTRACT_VERSION = 'kommo-main-contact-v1'


class RelationConflict(ValueError):
    pass


def external_id(value):
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str) or not re.fullmatch(r'[1-9][0-9]{0,18}', value):
        raise RelationConflict('invalid_kommo_relation_id')
    return value


def snapshot(payload, relation, changed_id):
    """Full relevant relation graph. Absent/malformed data never means empty."""
    changed_id = external_id(changed_id)
    if relation not in ('deal_contact', 'contact_company'):
        raise RelationConflict('invalid_relation_snapshot')
    if (not isinstance(payload, dict) or not isinstance(payload.get('_links', {}), dict)
            or payload.get('_links', {}).get('next')):
        raise RelationConflict('incomplete_remote_relation')
    embedded = payload.get('_embedded')
    rows = embedded.get('links') if isinstance(embedded, dict) else None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise RelationConflict('invalid_remote_relation')
    if '_total_items' in payload and (type(payload['_total_items']) is not int or payload['_total_items'] != len(rows)):
        raise RelationConflict('incomplete_remote_relation')
    target_type = 'contacts' if relation == 'deal_contact' else 'companies'
    expected_source = 'leads' if relation == 'deal_contact' else 'contacts'
    result = {}
    for row in rows:
        if row.get('entity_id') is not None and external_id(row['entity_id']) != changed_id:
            raise RelationConflict('remote_relation_source_mismatch')
        if row.get('entity_type') is not None and row['entity_type'] != expected_source:
            raise RelationConflict('remote_relation_source_mismatch')
        if row.get('to_entity_type') != target_type:
            continue
        ident = external_id(row.get('to_entity_id'))
        if ident in result:
            raise RelationConflict('duplicate_remote_relation')
        if relation == 'deal_contact':
            meta = row.get('metadata')
            main = meta.get('main_contact') if isinstance(meta, dict) else None
            if type(main) is not bool:
                raise RelationConflict('remote_main_contact_unknown')
            result[ident] = main
        else:
            result[ident] = False
    return canonical(result, relation)


def canonical(value, relation):
    if relation not in ('deal_contact', 'contact_company') or not isinstance(value, dict):
        raise RelationConflict('invalid_relation_snapshot')
    result = {}
    for key, main in value.items():
        ident = external_id(key)
        if type(main) is not bool:
            raise RelationConflict('invalid_relation_snapshot')
        result[ident] = main
    if sum(result.values()) > 1 or (relation == 'contact_company' and (len(result) > 1 or any(result.values()))):
        raise RelationConflict('ambiguous_remote_relation')
    return dict(sorted(result.items(), key=lambda item: int(item[0])))


@dataclass(frozen=True)
class Step:
    operation: str
    target_id: str
    main: bool = False


@dataclass(frozen=True)
class Plan:
    relation: str
    changed_id: str
    states: tuple
    steps: tuple

    def read_path(self):
        entity = 'leads' if self.relation == 'deal_contact' else 'contacts'
        return f'{entity}/{self.changed_id}/links'

    def request(self, index):
        step = self.steps[index]
        path = self.read_path().removesuffix('links') + step.operation
        body = {'to_entity_id': int(step.target_id),
                'to_entity_type': 'contacts' if self.relation == 'deal_contact' else 'companies'}
        if step.main:
            body['metadata'] = {'is_main': True}
        return 'POST', path, [body]


def plan(relation, changed_id, before, after, initial):
    """Replace/remove ONLY the old main contact; do not remove other contacts.

    No documented CAS exists. The worker compares full snapshots before/after
    every single step and stops on concurrent differences instead of rewriting
    a graph or attempting a compensating delete.
    """
    changed_id = external_id(changed_id)
    before = external_id(before) if before is not None else None
    after = external_id(after) if after is not None else None
    current = canonical(initial, relation)
    states, steps = [current], []
    selected = next((key for key, main in current.items() if main), None) if relation == 'deal_contact' else next(iter(current), None)
    if selected == after and (before is None or before == after or before not in current):
        return Plan(relation, changed_id, tuple(states), ())
    if selected != before:
        raise RelationConflict('remote_relation_changed')

    def add(operation, target, main=False):
        nonlocal current
        current = dict(current)
        if operation == 'unlink':
            current.pop(target)
        else:
            if main:
                current = {key: False for key in current}
            current[target] = main
        current = canonical(current, relation)
        steps.append(Step(operation, target, main))
        states.append(current)

    if relation == 'deal_contact':
        # Establish the intended main contact before detaching the old one.
        if after is not None:
            add('link', after, True)
        if before is not None and before != after:
            add('unlink', before)
    else:
        # Contact -> company has single-company cardinality; never pretend
        # its two endpoint operations are atomic.
        if before is not None and before != after:
            add('unlink', before)
        if after is not None:
            add('link', after)
    return Plan(relation, changed_id, tuple(states), tuple(steps))


def digest(state, relation):
    encoded = json.dumps(canonical(state, relation), sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(encoded.encode()).hexdigest()
