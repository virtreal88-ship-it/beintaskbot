"""Prepare tenant-local CRM batches without database or integration side effects."""
from typing import Callable

JsonEncoder = Callable[[dict], str]
SnapshotRow = tuple[object, ...]


def _record_id(item: dict, key: str) -> int:
    try:
        value = int(item.get(key) or item.get('id') or 0)
        return value if value > 0 else 0
    except (TypeError, ValueError):
        return 0


def deal_rows(tenant_id: str, deals: list[dict], encode: JsonEncoder) -> list[SnapshotRow]:
    rows: list[SnapshotRow] = []
    for item in deals:
        lead_id = _record_id(item, 'kommo_lead_id')
        if not lead_id:
            continue
        rows.append((
            tenant_id, lead_id, int(item.get('pipeline_id') or 0), int(item.get('status_id') or 0),
            str(item.get('stage_name') or '')[:240], str(item.get('name') or '')[:500],
            str(item.get('contact_name') or '')[:500], str(item.get('phone') or '')[:80],
            str(item.get('channel') or '')[:100], str(item.get('last_message') or '')[:4000],
            item.get('last_message_at') or None, item.get('source_updated_at') or None,
            encode(item.get('raw') if isinstance(item.get('raw'), dict) else {}),
        ))
    return rows


def task_rows(tenant_id: str, tasks: list[dict], encode: JsonEncoder) -> list[SnapshotRow]:
    rows: list[SnapshotRow] = []
    for item in tasks:
        task_id = _record_id(item, 'kommo_task_id')
        if not task_id:
            continue
        rows.append((
            tenant_id, task_id, int(item.get('kommo_lead_id') or 0), str(item.get('text') or '')[:4000],
            item.get('due_at') or None, int(item.get('responsible_id') or 0), bool(item.get('completed')),
            encode(item.get('raw') if isinstance(item.get('raw'), dict) else {}),
        ))
    return rows
