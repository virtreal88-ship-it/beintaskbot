"""Opaque pagination position, not an authorization token."""
import base64
import json
from datetime import datetime


def encode(session: dict, lead: int, row: dict) -> str:
    value = {'tenant':str(session['tenant_id']), 'actor':str(session['telegram_id']),
             'lead':lead, 'at':row['position_at'].isoformat(), 'id':row['external_id']}
    return base64.urlsafe_b64encode(json.dumps(value, separators=(',', ':')).encode()).decode().rstrip('=')


def decode(value: str, session: dict, lead: int) -> tuple[datetime, str] | None:
    if not value:
        return None
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError('Invalid cursor')
    try:
        data = json.loads(base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True))
        if not isinstance(data, dict) or (data.get('tenant'), data.get('actor'), data.get('lead')) != (
                str(session['tenant_id']), str(session['telegram_id']), lead):
            raise ValueError('Cursor scope changed')
        at = datetime.fromisoformat(data['at'])
        external = data['id']
        if at.tzinfo is None or not isinstance(external, str) or not external or len(external) > 512:
            raise ValueError('Invalid cursor position')
        return at, external
    except (KeyError, TypeError, UnicodeDecodeError) as error:
        raise ValueError('Invalid cursor') from error
