"""Closed voice-write admission and read-only drain diagnostics.

This isolated module is not wired to a production route. In particular, it does
not intercept legacy writers, and no environment flag or supplied assertion is
accepted as evidence that they were drained. There is deliberately no open path.
"""
from dataclasses import dataclass


class VoiceWriteUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ReceiptObservation:
    sending: int
    unknown: int


def observe_receipts(cur):
    """Aggregate visible receipt states without mutating them or exposing content.

    This is only a snapshot of committed rows, not proof of drain, no older
    workers, authorization, provider completion, or permission to open the gate.
    """
    cur.execute("""SELECT count(*) FILTER (WHERE status='sending') AS sending,
      count(*) FILTER (WHERE status='unknown') AS unknown
      FROM saas_chat_ai_runs WHERE provider='connect'""")
    row = cur.fetchone()
    if not row or any(type(row.get(key)) is not int or row[key] < 0 for key in ('sending','unknown')):
        raise VoiceWriteUnavailable('receipt_observation_unavailable')
    return ReceiptObservation(sending=row['sending'], unknown=row['unknown'])


def require_voice_write_ready():
    """Deny all voice writes until an independently proven cutover exists.

    No arguments are accepted: browser claims, config toggles, stale snapshots,
    synthetic proofs and environment variables cannot override this boundary.
    A future opening protocol must enforce writer fencing, cutover epochs,
    authorization and drain atomically at the real voice write boundary.
    """
    raise VoiceWriteUnavailable('legacy_writer_drain_not_proven')
