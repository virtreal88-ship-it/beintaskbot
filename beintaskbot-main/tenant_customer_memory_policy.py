"""Conservative decisions for a future customer-memory writer.

This module performs no I/O, authorisation, extraction, or persistence and is
not wired into production. Callers must authenticate the principal and verify
subject, sources, budgets and secrets before a transaction applies a decision.
"""
from dataclasses import dataclass
from enum import Enum


class Decision(str, Enum):
    CREATE_REVIEW = 'create_review'
    UNCHANGED = 'unchanged'
    REVIEW_CONFLICT = 'review_conflict'
    STALE_WORK = 'stale_work'
    SUPPRESSED = 'suppressed'


@dataclass(frozen=True)
class Snapshot:
    """Versions captured before extraction; equality is checked under lock."""
    memory_version: int
    source_version: str
    identity_version: str


def assess_automatic(*, started: Snapshot, current: Snapshot,
                     deleted_source: bool, existing_value: str | None,
                     proposed_value: str, manually_edited: bool = False) -> Decision:
    """Classify a proposal, without granting it authority to overwrite a fact.

    `current` and `deleted_source` must be read in the same transaction and
    protected by the locks used to apply the decision. A changed source, memory,
    or identity invalidates the whole result. The tombstone covers a source and
    subject, never the deleted text. Matching values do not automatically
    confirm or update timestamps. Any different value needs explicit review,
    including when its source happens to be newer.
    """
    if not isinstance(started, Snapshot) or not isinstance(current, Snapshot):
        raise TypeError('snapshot required')
    if not isinstance(proposed_value, str) or not proposed_value.strip():
        raise ValueError('nonempty proposed value required')
    if existing_value is not None and not isinstance(existing_value, str):
        raise TypeError('existing value must be text or None')
    if type(deleted_source) is not bool or type(manually_edited) is not bool:
        raise TypeError('boolean flags required')
    if started != current:
        return Decision.STALE_WORK
    if deleted_source:
        return Decision.SUPPRESSED
    if existing_value is None:
        return Decision.CREATE_REVIEW
    if proposed_value == existing_value:
        return Decision.UNCHANGED
    return Decision.REVIEW_CONFLICT
