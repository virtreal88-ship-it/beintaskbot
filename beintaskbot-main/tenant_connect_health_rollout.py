"""Explicit, one-tenant rollout step. Never called by startup or a scheduler.

Uses existing encrypted integration credentials in-process. Does not reconnect,
change AI modes, inspect message/client data, or send a notification.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from uuid import UUID

import tenant_connect_health as health
import tenant_connect_transport as transport


def reconcile(tenant_id, callback_url, *, apply=False):
    tenant=str(UUID(tenant_id))
    if os.environ.get('CONNECT_HEALTH_ALERTS_ENABLED','').lower() == 'true':
        raise RuntimeError('Keep health delivery disabled during reconciliation')
    if tenant not in health.allowed_tenants():
        raise RuntimeError('Tenant must be explicitly allowlisted')
    if not callback_url.startswith('https://') or not callback_url.endswith('/api/platform/connect/webhook'):
        raise ValueError('Exact HTTPS CRM callback URL required')
    with transport._connect() as conn:
        health.require_schema(conn)
        with conn.cursor() as cur:
            if not health._owner(cur,tenant):
                raise RuntimeError('Active exact-owner Telegram binding required')
            cur.execute("SELECT secrets FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='connect' AND status='connected'",(tenant,))
            row=cur.fetchone()
            if not row or not row.get('secrets'):raise RuntimeError('Connected tenant integration required')
            encrypted=bytes(row['secrets'])
            credentials=json.loads(transport._fernet().decrypt(encrypted).decode())
            cur.execute('SELECT connect_channel_id FROM saas_connect_channels WHERE tenant_id=%s::uuid AND active=TRUE ORDER BY connect_channel_id',(tenant,))
            channels=[str(row['connect_channel_id']) for row in cur.fetchall()]
    if not channels:raise RuntimeError('No active mapped channels')
    key=str(credentials.get('api_key') or '')
    hook_id=str(UUID(str(credentials.get('webhook_id') or '')))
    if not key or not credentials.get('webhook_secret'):raise RuntimeError('Existing integration credentials incomplete')

    def hook():
        hooks=transport._request(key,'GET','/v1/webhooks').get('data',[])
        matches=[item for item in hooks if isinstance(item,dict) and str(item.get('id'))==hook_id]
        if len(matches)!=1 or matches[0].get('active') is not True or matches[0].get('url')!=callback_url:
            raise RuntimeError('Existing active callback identity must match exactly')
        events=matches[0].get('events')
        if not isinstance(events,list) or not all(isinstance(value,str) for value in events):raise RuntimeError('Invalid subscription events')
        return events
    events=hook()
    report={'tenant_id':tenant,'active_channels':len(channels),'owner_binding_verified':True,
            'telegram_reachability_tested':False,'health_subscription_verified':health.EVENT in events,
            'seeded_channels':0,'mode':'apply' if apply else 'audit'}
    if not apply:return report
    if health.EVENT not in events:
        # Only add this event to the already-owned subscription, preserving all
        # unrelated events/URL/active state/secret. Never replace the webhook.
        transport._request(key,'PATCH','/v1/webhooks/'+hook_id,{'events':list(dict.fromkeys([*events,health.EVENT]))})
    if health.EVENT not in hook():raise RuntimeError('Subscription reconciliation could not be verified')
    report['health_subscription_verified']=True
    observations=[]
    for channel in channels:
        started=datetime.now(timezone.utc)
        current=transport._request(key,'GET','/v1/channels/'+channel+'/status')
        if str(current.get('id'))!=channel:raise RuntimeError('Status channel mismatch')
        if datetime.now(timezone.utc)-started>timedelta(seconds=30):raise RuntimeError('Provider status collection exceeded freshness budget')
        status=current.get('status')
        if status=='disabled':raise RuntimeError('Channel retired during reconciliation')
        # The existing status endpoint collapses provider request errors to
        # `error`; this is not evidence of a definite disconnection.
        status=status if status in {'connected','disconnected','connecting'} else 'unknown'
        observations.append((channel,{'status':status,'observedAt':started.isoformat()}))
    with transport._connect() as conn:
        health.require_schema(conn)
        with conn.cursor() as cur:
            # Revalidate configuration before any local seed writes. Provider
            # calls happen outside DB locks and before this atomic transaction.
            cur.execute("SELECT secrets FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='connect' AND status='connected' FOR SHARE",(tenant,))
            current=cur.fetchone()
            if not current or bytes(current['secrets'])!=encrypted:raise RuntimeError('Integration changed during reconciliation')
            if tenant not in health.allowed_tenants() or os.environ.get('CONNECT_HEALTH_ALERTS_ENABLED','').lower()=='true':
                raise RuntimeError('Rollout authorization changed')
            if not health._owner(cur,tenant):raise RuntimeError('Owner binding changed')
            for channel,data in observations:
                if datetime.now(timezone.utc)-health.timestamp(data['observedAt'])>timedelta(seconds=60):
                    raise RuntimeError('Seed observations became stale; retry the scoped step')
                cur.execute('SELECT 1 FROM saas_connect_channels WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND active=TRUE FOR SHARE',(tenant,channel))
                if not cur.fetchone():raise RuntimeError('Channel mapping changed')
                health.record(cur,tenant,channel,data,datetime.now(timezone.utc))
        conn.commit()
    report['seeded_channels']=len(observations)
    report['provider_reported_statuses']=[data['status'] for _,data in observations]
    report['message_flow_verified']=False
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tenant',required=True)
    parser.add_argument('--callback-url',required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    print(json.dumps(reconcile(args.tenant,args.callback_url,apply=args.apply),sort_keys=True))


if __name__=='__main__':main()
