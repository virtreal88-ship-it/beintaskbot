"""Bounded, tenant-bound keyset cursors for the task approval queue."""
import base64
import binascii
import json
import uuid
from datetime import datetime, timezone


def approval_page_size(value: object = 50) -> int:
    try:
        size = int(value)
        if isinstance(value, bool) or not 1 <= size <= 200:
            raise ValueError()
        return size
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError('Səhifə ölçüsü 1–200 arasında olmalıdır.') from exc


def decode_approval_cursor(value: str, tenant_id: str) -> tuple[datetime, int, str] | None:
    if not value:
        return None
    try:
        if not isinstance(value, str) or len(value) > 1024:
            raise ValueError()
        payload = json.loads(base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True))
        if (not isinstance(payload, dict) or type(payload.get('v')) is not int
                or payload.get('v') != 1 or payload.get('tenant') != str(tenant_id)
                or not isinstance(payload.get('created_at'), str)
                or not isinstance(payload.get('request_id'), str)):
            raise ValueError()
        date = datetime.fromisoformat(payload['created_at'])
        actor = payload['actor_id']
        if date.tzinfo is None or type(actor) is not int or not 0 < actor < 2**63:
            raise ValueError()
        request = str(uuid.UUID(payload['request_id']))
        return date.astimezone(timezone.utc), actor, request
    except (ValueError, TypeError, KeyError, binascii.Error, OverflowError) as exc:
        raise ValueError('Səhifə kursoru düzgün deyil. Siyahını yeniləyin.') from exc


def encode_approval_cursor(row: dict, tenant_id: str) -> str:
    data = {'v': 1, 'tenant': str(tenant_id), 'created_at': row['created_at'].isoformat(),
            'actor_id': int(row['actor_id']), 'request_id': str(row['request_id'])}
    return base64.urlsafe_b64encode(json.dumps(data, separators=(',', ':')).encode()).decode().rstrip('=')
