"""Background orchestration. No delivery imports; only existing knowledge extraction."""
import asyncio
import uuid
from tenant_platform import _connect
from tenant_linear_policy import TenantLinearError
import tenant_knowledge_campaigns as queue
import tenant_knowledge_campaign_discovery as discovery
import tenant_knowledge_candidates_store as store
from tenant_knowledge_candidates_service import analyze


def check_fence(cur, tenant, extraction_id):
    cur.execute('''SELECT f.status,c.status AS campaign_status,f.lease_until>now() AS live,c.sources,e.source_refs
      FROM saas_knowledge_campaign_fragments f JOIN saas_knowledge_campaigns c
      ON c.tenant_id=f.tenant_id AND c.id=f.campaign_id LEFT JOIN saas_knowledge_extractions e ON e.tenant_id=f.tenant_id AND e.id=f.id WHERE f.tenant_id=%s::uuid AND f.id=%s::uuid''',(tenant,extraction_id))
    row=cur.fetchone()
    if row and (row['status']!='running' or row['campaign_status']!='running' or not row['live'] or any(str(r.get('channel_id')) not in row['sources'] for r in row.get('source_refs') or [])):
        raise TenantLinearError('Накопление знаний остановлено.',409)


def tick():
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM saas_knowledge_campaigns WHERE status='running' AND approved_at IS NOT NULL ORDER BY updated_at LIMIT 1 FOR UPDATE SKIP LOCKED")
            campaign=cur.fetchone()
            if not campaign:return False
            tenant=str(campaign['tenant_id']);key=str(campaign['id'])
            profile={'tenant_id':tenant,'telegram_id':campaign['created_by']}
            store.authorize(cur,profile)
            cur.execute('SELECT enabled,emergency_stop FROM saas_ai_agent_settings WHERE tenant_id=%s::uuid',(tenant,))
            settings=cur.fetchone()
            if not settings or not settings['enabled'] or settings['emergency_stop']:
                cur.execute("UPDATE saas_knowledge_campaigns SET status='paused',version=version+1 WHERE tenant_id=%s::uuid AND id=%s::uuid",(tenant,key))
                conn.commit();return False
            try:discovery.discover(cur,profile,campaign)
            except TenantLinearError:
                cur.execute("UPDATE saas_knowledge_campaigns SET status='blocked',version=version+1,progress=progress||'{\"error\":\"source_scope_changed\"}'::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",(tenant,key))
                conn.commit();return False
            cur.execute("SELECT 1 FROM saas_knowledge_campaign_sources WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid AND status='pending' LIMIT 1",(tenant,key))
            if campaign['progress'] and not cur.fetchone():
                discovery.discover_resolved(cur,profile,campaign)
            cur.execute('UPDATE saas_knowledge_campaigns SET updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,key))
        conn.commit()
    reconcile(profile,key)
    from tenant_knowledge_quality import tick as check_quality
    if check_quality(profile,key):return True
    claimed=queue.claim(tenant,key)
    if not claimed:return False
    try:
        result=analyze(profile,{**claimed['route'],'request_id':str(claimed['id'])})
        queue.complete(claimed,result)
    except TenantLinearError as error:
        # No silent retry of an existing/uncertain provider receipt. A limit or
        # empty fragment before reservation is known not to have made a call.
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT status FROM saas_knowledge_extractions WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,claimed['id']))
                receipt=cur.fetchone()
                empty=not receipt and error.status==409 and 'нет доступного текста' in str(error)
                code='no_text' if empty else 'provider_unknown' if receipt else 'limit_exceeded' if error.status==429 else 'source_unavailable'
                cur.execute("UPDATE saas_knowledge_campaign_fragments SET status=%s,error_code=%s WHERE tenant_id=%s::uuid AND id=%s::uuid AND lease_token=%s::uuid",('unknown' if receipt else 'completed' if empty else 'blocked',code,tenant,claimed['id'],claimed['lease_token']))
                if not receipt:
                    cur.execute('UPDATE saas_knowledge_campaigns SET reserved_calls=reserved_calls-2,reserved_tokens=reserved_tokens-24000 WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,key))
                if empty and getattr(error,'next_before',None):
                    queue.enqueue(cur,profile,campaign,{**claimed['route'],'before':error.next_before})
                if not empty:
                    cur.execute("UPDATE saas_knowledge_campaigns SET status=%s,version=version+1 WHERE tenant_id=%s::uuid AND id=%s::uuid",('paused' if error.status==429 and not receipt else 'blocked',tenant,key))
            conn.commit()
    return True


async def run(logger):
    while True:
        try:await asyncio.to_thread(tick)
        except asyncio.CancelledError:raise
        except Exception:logger.warning('Knowledge campaign polling unavailable')
        await asyncio.sleep(10)


def reconcile(profile,campaign_id):
    """Recover already-completed receipts only; never issue a provider call."""
    with _connect() as conn:
        with conn.cursor() as cur:
            tenant,_,_=store.authorize(cur,profile)
            cur.execute("""SELECT f.* FROM saas_knowledge_campaign_fragments f
              JOIN saas_knowledge_extractions e ON e.tenant_id=f.tenant_id AND e.id=f.id
              WHERE f.tenant_id=%s::uuid AND f.campaign_id=%s::uuid AND e.status='completed'
              AND (f.status='unknown' OR (f.status='running' AND f.lease_until<now())) LIMIT 4""",(tenant,campaign_id))
            rows=cur.fetchall()
    for row in rows:
        response=store.request_detail(profile,str(row['id']))
        queue.complete({**row,'profile':profile},response)
