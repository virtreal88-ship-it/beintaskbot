"""Generic push alerts: no company/person/task identifiers in external payload."""
import asyncio
import requests
from pywebpush import webpush, WebPushException
from tenant_platform import _fernet
from tenant_push import normalize_subscription
from tenant_push_outbox import claim_push, finish_push
from tenant_push_provider import send as send_provider
from tenant_notice_transport import attempt


class NoRedirectSession(requests.Session):
    def request(self, method, url, **kwargs):
        kwargs['allow_redirects']=False
        return super().request(method,url,**kwargs)


def send_push(item: dict, private_key: str, claims: dict) -> None:
    send_provider(item, private_key, claims, decrypt=_fernet().decrypt,
                  normalize=normalize_subscription, session_factory=NoRedirectSession,
                  webpush=webpush, rejection=WebPushException)


async def deliver_push_notifications(private_key: str, claims: dict, logger) -> None:
    if not private_key:
        return
    for _ in range(10):
        item=await asyncio.to_thread(claim_push)
        if item is None:
            break
        if not item['send']:
            continue
        result = await attempt(item, 'push', None, text=lambda row: '', parts=lambda text: [],
                               push_sender=send_push, private_key=private_key, claims=claims)
        if result.status != 'delivered':
            logger.warning('Tenant push result=%s tenant=%s request=%s',
                           result.status, str(item['tenant_id']), str(item['request_id']))
        await asyncio.to_thread(finish_push, item, result.status)
