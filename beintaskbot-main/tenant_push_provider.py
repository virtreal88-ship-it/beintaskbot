"""Single push provider call, with explicit credentials and dependencies."""
import hashlib
import json
from typing import Callable, ContextManager, Protocol

from tenant_push_payload import payload_for


class PushResponse(Protocol):
    status_code: int


def send(item: dict, private_key: str, claims: dict, *,
         decrypt: Callable[[bytes], bytes], normalize: Callable[[object], dict],
         session_factory: Callable[[], ContextManager[object]],
         webpush: Callable[..., PushResponse], rejection: Callable[..., Exception]) -> None:
    """Queue must check ownership/rights and persist sending before calling.

    The provider verifies the normalized endpoint against the claimed digest.
    No DB access, retries, recipient selection or private error logging here.
    """
    subscription = normalize(json.loads(decrypt(bytes(item['subscription']))))
    digest = hashlib.sha256(subscription['endpoint'].encode()).hexdigest()
    if digest != item['endpoint_hash']:
        raise ValueError('Push device identity mismatch')
    payload = payload_for(item.get('event'))
    with session_factory() as session:
        result = webpush(subscription_info=subscription, data=json.dumps(payload),
                         vapid_private_key=private_key, vapid_claims=dict(claims),
                         requests_session=session, timeout=15, ttl=300)
        if not 200 <= result.status_code < 300:
            raise rejection('Push rejected', response=result)
