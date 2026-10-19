"""Debounced, idempotent realtime extraction of support case candidates.

Connect webhook admission only writes a queue receipt in the same transaction
as the source message. The worker then reuses the bounded knowledge extractor,
its budget checks, AI emergency stop, and review-only candidate persistence.
"""
import asyncio
import os
import uuid

from tenant_platform import _connect
from tenant_linear_policy import TenantLinearError
import tenant_knowledge_candidates_store as store
from tenant_knowledge_candidates_service import analyze

DEBOUNCE_SECONDS = 20
LEASE_SECONDS = 300


def extraction_enabled():
    # Separate opt-in: never inherit the existing AI-agent enabled flag.
    return os.environ.get('SUPPORT_CASES_LIVE_EXTRACTION_ENABLED', '').strip().lower() in ('1', 'true', 'yes', 'on')


def create_schema(conn):
    with conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS saas_knowledge_live_queue(
          tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
          channel_id UUID NOT NULL,
          conversation_id TEXT NOT NULL,
          lead_id BIGINT NOT NULL,
          actor_id BIGINT,
          last_message_id UUID NOT NULL,
          generation BIGINT NOT NULL DEFAULT 1,
          state TEXT NOT NULL DEFAULT 'pending'
            CHECK(state IN ('pending','running','completed','blocked')),
          next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          lease_until TIMESTAMPTZ,
          extraction_id UUID,
          error_code TEXT NOT NULL DEFAULT '',
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,channel_id,conversation_id),
          FOREIGN KEY(tenant_id,actor_id) REFERENCES saas_tenant_members(tenant_id,telegram_id)
        )""")
        cur.execute("CREATE INDEX IF NOT EXISTS saas_knowledge_live_queue_pending_idx ON saas_knowledge_live_queue(next_attempt_at,updated_at) WHERE state='pending'")


def enqueue(cur, tenant, channel_id, conversation_id, lead_id, message_id, actor_id=None):
    """Record a new source message; duplicate Connect events are filtered upstream."""
    if not extraction_enabled():
        return
    cur.execute("""INSERT INTO saas_knowledge_live_queue
      (tenant_id,channel_id,conversation_id,lead_id,actor_id,last_message_id,generation,state,next_attempt_at)
      VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s::uuid,1,'pending',now()+(%s * interval '1 second'))
      ON CONFLICT(tenant_id,channel_id,conversation_id) DO UPDATE SET
        lead_id=EXCLUDED.lead_id,
        actor_id=COALESCE(EXCLUDED.actor_id,saas_knowledge_live_queue.actor_id),
        last_message_id=EXCLUDED.last_message_id,
        generation=saas_knowledge_live_queue.generation+1,
        state=CASE WHEN saas_knowledge_live_queue.state='running' THEN 'running' ELSE 'pending' END,
        next_attempt_at=EXCLUDED.next_attempt_at,
        extraction_id=NULL,error_code='',updated_at=now()
      WHERE saas_knowledge_live_queue.last_message_id IS DISTINCT FROM EXCLUDED.last_message_id""",
      (tenant,channel_id,conversation_id,lead_id,actor_id,message_id,DEBOUNCE_SECONDS))


def claim():
    """Claim one due conversation without holding DB locks during model calls."""
    if not extraction_enabled():
        return None
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""UPDATE saas_knowledge_live_queue SET state='blocked',error_code='worker_lease_expired',
              lease_until=NULL,updated_at=now() WHERE state='running' AND lease_until<now()""")
            cur.execute("""SELECT q.*,m.telegram_id AS reviewer_id FROM saas_knowledge_live_queue q
              JOIN saas_tenant_members m ON m.tenant_id=q.tenant_id AND m.active=TRUE
                AND m.role IN ('owner','admin')
              JOIN saas_ai_agent_settings s ON s.tenant_id=q.tenant_id AND s.enabled=TRUE AND s.emergency_stop=FALSE
              JOIN saas_tenants t ON t.id=q.tenant_id AND t.status='active'
              WHERE q.state='pending' AND q.next_attempt_at<=now()
              ORDER BY q.next_attempt_at,q.updated_at,CASE WHEN m.role='owner' THEN 0 ELSE 1 END,q.tenant_id,q.channel_id
              LIMIT 1 FOR UPDATE OF q SKIP LOCKED""")
            row=cur.fetchone()
            if not row:
                conn.commit();return None
            cur.execute("UPDATE saas_knowledge_live_queue SET state='running',lease_until=now()+(%s * interval '1 second'),updated_at=now() WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id=%s",
              (LEASE_SECONDS,row['tenant_id'],row['channel_id'],row['conversation_id']))
        conn.commit()
    return {**row,'reviewer_id':int(row['reviewer_id'])}


def _request_id(item):
    # Stable receipt ID means a worker restart reuses the original AI receipt
    # rather than repeating an uncertain provider call.
    value=':'.join(('live-support-case',str(item['tenant_id']),str(item['channel_id']),
                    str(item['conversation_id']),str(item['last_message_id'])))
    return str(uuid.uuid5(uuid.NAMESPACE_URL,value))


def finish(item, extraction_id=None, error_code=''):
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""UPDATE saas_knowledge_live_queue SET
              state=CASE WHEN generation=%s THEN 'completed' ELSE 'pending' END,
              next_attempt_at=CASE WHEN generation=%s THEN next_attempt_at ELSE now()+(%s * interval '1 second') END,
              lease_until=NULL,extraction_id=%s::uuid,error_code=%s,updated_at=now()
              WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id=%s AND state='running'""",
              (item['generation'],item['generation'],DEBOUNCE_SECONDS,extraction_id,error_code,
               item['tenant_id'],item['channel_id'],item['conversation_id']))
        conn.commit()


def tick():
    if not extraction_enabled():
        return False
    item=claim()
    if not item:return False
    profile={'tenant_id':str(item['tenant_id']),'telegram_id':item['reviewer_id']}
    request_id=_request_id(item)
    try:
        # Re-check after claim: a switch-off between claim and provider dispatch
        # must not start a new analysis. Preserve the receipt for later review.
        if not extraction_enabled():
            finish(item,error_code='disabled_after_claim')
            return False
        response=analyze(profile,{
          'request_id':request_id,'provider':'connect','channel_id':str(item['channel_id']),
          'conversation_id':item['conversation_id'],'history_mode':'channel','language':'az'})
        request=response.get('request') or {}
        state=request.get('status')
        if state not in {'completed','failed','unknown'}:
            finish(item,error_code='extraction_state_unknown')
        else:
            finish(item,request_id,'' if state=='completed' else 'provider_'+state)
    except TenantLinearError as exc:
        # Quota, disabled AI, emergency stop, lost route, and source changes
        # fail closed. A later real message may enqueue a fresh generation.
        finish(item,error_code='quota_or_policy_blocked' if exc.status in (403,409,429) else 'source_unavailable')
    except Exception:
        # Do not automatically retry a possibly submitted provider request.
        finish(item,error_code='worker_result_unknown')
    return True


async def run(logger):
    try:
        with _connect() as conn:
            from tenant_ai_agents_schema import ensure_schema
            ensure_schema(conn)
            create_schema(conn)
            conn.commit()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning('Realtime support case extraction queue schema unavailable')
    while True:
        try:
            did_work=await asyncio.to_thread(tick)
            if not did_work:await asyncio.sleep(2)
        except asyncio.CancelledError:raise
        except Exception:
            logger.warning('Realtime support case extraction queue polling failed')
            await asyncio.sleep(2)
