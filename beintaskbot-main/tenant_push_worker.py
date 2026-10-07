"""Generic push alerts: no company/person/task identifiers in external payload."""
import asyncio
import hashlib
import json
import requests
from pywebpush import webpush, WebPushException
from tenant_platform import _fernet
from tenant_push import normalize_subscription
from tenant_push_outbox import claim_push, finish_push


class NoRedirectSession(requests.Session):
    def request(self, method, url, **kwargs):
        kwargs['allow_redirects']=False
        return super().request(method,url,**kwargs)


def send_push(item: dict, private_key: str, claims: dict) -> None:
    subscription=normalize_subscription(json.loads(_fernet().decrypt(bytes(item['subscription']))))
    if hashlib.sha256(subscription['endpoint'].encode()).hexdigest()!=item['endpoint_hash']:
        raise ValueError('Push device identity mismatch')
    payload={'title':'CRM Smart Assistant','body':'Yeni tapşırıq təsdiq sorğusu. Kabinetdə yoxlayın.',
             'url':'/app','tag':'crm-task-approval'}
    with NoRedirectSession() as session:
        result=webpush(subscription_info=subscription,data=json.dumps(payload),vapid_private_key=private_key,
                       vapid_claims=dict(claims),requests_session=session,timeout=15,ttl=300)
        if not 200<=result.status_code<300:
            raise WebPushException('Push rejected',response=result)


async def deliver_push_notifications(private_key: str, claims: dict, logger) -> None:
    if not private_key:
        return
    for _ in range(10):
        item=await asyncio.to_thread(claim_push)
        if item is None:
            break
        if not item['send']:
            continue
        status='delivered'
        try:
            await asyncio.to_thread(send_push,item,private_key,claims)
        except Exception as exc:
            response=getattr(exc,'response',None)
            code=getattr(response,'status_code',None)
            status='expired' if code in {404,410} else 'unknown'
            logger.warning('Tenant push result=%s tenant=%s request=%s',status,str(item['tenant_id']),str(item['request_id']))
        await asyncio.to_thread(finish_push,item,status)
