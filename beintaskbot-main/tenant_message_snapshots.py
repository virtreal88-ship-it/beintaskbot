"""Prepare chat snapshot batches; authority always comes from caller context."""
import hashlib
from typing import Callable
from uuid import UUID

from tenant_crm_snapshots import JsonEncoder, SnapshotRow


def message_rows(tenant_id: str, lead_id: int, messages: list[dict],
                 encode: JsonEncoder, new_id: Callable[[], UUID]) -> list[SnapshotRow]:
    rows: list[SnapshotRow] = []
    for item in messages:
        external_id = str(item.get('external_id') or item.get('id') or '').strip()
        if not external_id:
            fingerprint = encode({'at': item.get('happened_at'), 'body': item.get('body'),
                                  'direction': item.get('direction')})
            external_id = hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()
        rows.append((
            new_id(), tenant_id, int(lead_id), external_id[:500],
            str(item.get('direction') or 'incoming')[:30], str(item.get('channel') or '')[:100],
            str(item.get('author_name') or '')[:240], str(item.get('body') or '')[:12000],
            str(item.get('message_type') or 'text')[:60], str(item.get('media_url') or '')[:3000],
            item.get('happened_at') or None,
            encode(item.get('raw') if isinstance(item.get('raw'), dict) else {}),
        ))
    return rows
