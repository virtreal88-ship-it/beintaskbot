"""Debounced, idempotent realtime extraction of support case candidates.

Connect webhook admission only writes a queue receipt in the same transaction
as the source message. The worker then reuses the bounded knowledge extractor,
its budget checks, AI emergency stop, and review-only candidate persistence.
"""
import asyncio
from datetime import datetime, timedelta
import os
import uuid

from tenant_platform import _connect
from tenant_linear_policy import TenantLinearError
import tenant_knowledge_candidates_store as store
from tenant_knowledge_candidates_service import LiveExtractionDisabled, analyze

DEBOUNCE_SECONDS = 20
LEASE_SECONDS = 300
SCOPE_ENV = {
    'tenant_id': 'SUPPORT_CASES_LIVE_EXTRACTION_TENANT_ID',
    'channel_id': 'SUPPORT_CASES_LIVE_EXTRACTION_CHANNEL_ID',
    'conversation_id': 'SUPPORT_CASES_LIVE_EXTRACTION_CONVERSATION_ID',
    'start_after': 'SUPPORT_CASES_LIVE_EXTRACTION_START_AFTER',
}


def pilot_scope():
    """Return the single, explicit pilot scope; missing/invalid scope is off."""
    if os.environ.get('SUPPORT_CASES_LIVE_EXTRACTION_ENABLED', '').strip().lower() not in ('1', 'true', 'yes', 'on'):
        return None
    values = {key: str(os.environ.get(name) or '').strip() for key, name in SCOPE_ENV.items()}
    if not values['conversation_id'] or len(values['conversation_id']) > 200 or any(ord(c) < 32 for c in values['conversation_id']):
        return None
    try:
        values['tenant_id'] = str(uuid.UUID(values['tenant_id']))
        values['channel_id'] = str(uuid.UUID(values['channel_id']))
        start = datetime.fromisoformat(values['start_after'].replace('Z', '+00:00'))
        if start.tzinfo is None:
            return None
        values['start_after'] = start.isoformat()
    except (ValueError, TypeError):
        return None
    return values


def extraction_enabled():
    enabled=os.environ.get('SUPPORT_CASES_LIVE_EXTRACTION_ENABLED', '').strip().lower() in ('1', 'true', 'yes', 'on')
    if not enabled:
        return False
    # Keep an incomplete legacy pilot configuration fail-closed.  A deployment
    # without any legacy scope keys uses tenant/channel settings instead.
    legacy_present=any(os.environ.get(name) for name in SCOPE_ENV.values())
    return bool(pilot_scope()) if legacy_present else True


def _configured(cur, tenant, channel, happened_at=None, conversation=None):
    """Check the global guard and tenant/channel scope at every queue fence."""
    if not extraction_enabled():
        return None
    legacy=pilot_scope()
    if legacy:
        if not _scope_matches(legacy,tenant,channel,conversation, happened_at):
            return None
        return datetime.fromisoformat(legacy['start_after'])
    cur.execute('''SELECT support_case_extraction_started_at FROM saas_ai_agent_settings
      WHERE tenant_id=%s::uuid AND support_case_extraction_mode='automatic'
        AND support_case_extraction_enabled=TRUE
        AND (jsonb_array_length(support_case_extraction_channels)>0)
        AND support_case_extraction_channels ? %s''', (tenant, str(channel)))
    row=cur.fetchone()
    if not row or not row.get('support_case_extraction_started_at'):
        return None
    start=row['support_case_extraction_started_at']
    if happened_at is not None:
        try:
            happened=happened_at if isinstance(happened_at,datetime) else datetime.fromisoformat(str(happened_at).replace('Z','+00:00'))
            if happened < start:
                return None
        except (TypeError,ValueError):
            return None
    return start


def _scope_matches(scope, tenant, channel, conversation, happened_at=None):
    if not (scope and str(tenant) == scope['tenant_id'] and str(channel) == scope['channel_id']
            and str(conversation) == scope['conversation_id']):
        return False
    if happened_at is not None:
        try:
            happened = happened_at if isinstance(happened_at, datetime) else datetime.fromisoformat(str(happened_at).replace('Z','+00:00'))
            return happened.tzinfo is not None and happened >= datetime.fromisoformat(scope['start_after'])
        except (ValueError, TypeError):
            return False
    return True


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
    legacy = pilot_scope()
    start=_configured(cur,tenant,channel_id,conversation=conversation_id)
    if not start:
        return
    # Reject delayed/replayed pre-pilot webhooks using the persisted message
    # timestamp, not enqueue time. This also prevents old queue rows from being
    # refreshed into apparently new work.
    if legacy:
        # Preserve the old pilot receipt contract exactly.  In particular, a
        # legacy scope remains one conversation and can never imply all tenant
        # channels after upgrade.
        cur.execute("""SELECT 1 FROM saas_crm_messages WHERE tenant_id=%s::uuid AND id=%s::uuid
          AND kommo_lead_id=%s AND channel=%s AND happened_at >= %s::timestamptz
          AND direction IN ('incoming','outgoing') AND EXISTS(
            SELECT 1 FROM saas_ai_agent_settings s WHERE s.tenant_id=saas_crm_messages.tenant_id
              AND s.support_case_extraction_enabled=TRUE AND s.support_case_extraction_started_at IS NOT NULL
              AND saas_crm_messages.happened_at>=s.support_case_extraction_started_at)""",
          (legacy['tenant_id'], str(uuid.UUID(str(message_id))), int(lead_id), 'connect:' + legacy['channel_id'], legacy['start_after']))
    else:
        cur.execute("""SELECT 1 FROM saas_crm_messages WHERE tenant_id=%s::uuid AND id=%s::uuid
          AND kommo_lead_id=%s AND channel=%s AND happened_at >= %s::timestamptz
          AND direction IN ('incoming','outgoing') AND EXISTS(
            SELECT 1 FROM saas_ai_agent_settings s WHERE s.tenant_id=saas_crm_messages.tenant_id
              AND s.support_case_extraction_mode='automatic' AND s.support_case_extraction_enabled=TRUE AND s.support_case_extraction_started_at IS NOT NULL
              AND s.support_case_extraction_channels ? %s
              AND saas_crm_messages.happened_at>=s.support_case_extraction_started_at)""",
          (tenant, str(uuid.UUID(str(message_id))), int(lead_id), 'connect:' + str(channel_id), start, str(channel_id)))
    if not cur.fetchone():
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
    legacy=pilot_scope()
    with _connect() as conn:
        with conn.cursor() as cur:
            expired = """UPDATE saas_knowledge_live_queue SET state='blocked',error_code='worker_lease_expired',
              lease_until=NULL,updated_at=now() WHERE state='running' AND lease_until<now()"""
            if legacy:
                cur.execute(expired + " AND tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id=%s",
                    (legacy['tenant_id'], legacy['channel_id'], legacy['conversation_id']))
                cur.execute("""SELECT q.*,m.telegram_id AS reviewer_id,source.happened_at AS last_message_at
                  FROM saas_knowledge_live_queue q JOIN saas_crm_messages source ON source.tenant_id=q.tenant_id AND source.id=q.last_message_id AND source.kommo_lead_id=q.lead_id AND source.channel=('connect:' || q.channel_id::text) AND source.happened_at >= %s::timestamptz AND source.direction IN ('incoming','outgoing')
                  JOIN saas_tenant_members m ON m.tenant_id=q.tenant_id AND m.active=TRUE AND m.role IN ('owner','admin')
                  JOIN saas_ai_agent_settings extraction ON extraction.tenant_id=q.tenant_id AND extraction.support_case_extraction_enabled=TRUE AND extraction.support_case_extraction_started_at IS NOT NULL AND source.happened_at>=extraction.support_case_extraction_started_at
                  JOIN saas_ai_agent_settings s ON s.tenant_id=q.tenant_id AND s.enabled=TRUE AND s.emergency_stop=FALSE JOIN saas_tenants t ON t.id=q.tenant_id AND t.status='active'
                  WHERE q.tenant_id=%s::uuid AND q.channel_id=%s::uuid AND q.conversation_id=%s AND q.state='pending' AND q.next_attempt_at<=now() ORDER BY q.next_attempt_at,q.updated_at LIMIT 1 FOR UPDATE OF q SKIP LOCKED""",(legacy['start_after'],legacy['tenant_id'],legacy['channel_id'],legacy['conversation_id']))
            else:
                cur.execute(expired)
                cur.execute("""SELECT q.*,m.telegram_id AS reviewer_id,source.happened_at AS last_message_at,
                    extraction.support_case_extraction_started_at AS extraction_started_at
              FROM saas_knowledge_live_queue q
              JOIN saas_crm_messages source ON source.tenant_id=q.tenant_id AND source.id=q.last_message_id
                AND source.kommo_lead_id=q.lead_id AND source.channel=('connect:' || q.channel_id::text)
                AND source.direction IN ('incoming','outgoing')
              JOIN saas_tenant_members m ON m.tenant_id=q.tenant_id AND m.active=TRUE
                AND m.role IN ('owner','admin')
              JOIN saas_ai_agent_settings extraction ON extraction.tenant_id=q.tenant_id
                AND extraction.support_case_extraction_mode='automatic' AND extraction.support_case_extraction_enabled=TRUE
                AND extraction.support_case_extraction_started_at IS NOT NULL
                AND extraction.support_case_extraction_channels ? q.channel_id::text
                AND source.happened_at>=extraction.support_case_extraction_started_at
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


def require_extraction_enabled(item=None):
    if not extraction_enabled():
        raise LiveExtractionDisabled()
    legacy=pilot_scope()
    if legacy:
        if item and not _scope_matches(legacy,item['tenant_id'],item['channel_id'],item.get('conversation_id'),item.get('last_message_at')):
            raise LiveExtractionDisabled()
        return
    if item:
        with _connect() as conn:
            with conn.cursor() as cur:
                if not _configured(cur,item['tenant_id'],item['channel_id'],item.get('last_message_at'),item.get('conversation_id')):
                    raise LiveExtractionDisabled()
    elif not extraction_enabled():
        raise LiveExtractionDisabled()
    return


def finish(item, extraction_id=None, error_code=''):
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""UPDATE saas_knowledge_live_queue SET
              state=CASE WHEN generation<>%s THEN 'pending' WHEN %s<>'' THEN 'blocked' ELSE 'completed' END,
              next_attempt_at=CASE WHEN generation=%s THEN next_attempt_at ELSE now()+(%s * interval '1 second') END,
              lease_until=NULL,extraction_id=%s::uuid,error_code=%s,updated_at=now()
              WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id=%s AND state='running'""",
              (item['generation'],error_code,item['generation'],DEBOUNCE_SECONDS,extraction_id,error_code,
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
        try:
            require_extraction_enabled(item)
        except LiveExtractionDisabled:
            finish(item,error_code='live_extraction_scope_changed_after_claim')
            return False
        message_at=item['last_message_at']
        if not isinstance(message_at,datetime):
            message_at=datetime.fromisoformat(str(message_at).replace('Z','+00:00'))
        end_at=(message_at+timedelta(microseconds=1)).isoformat()
        response=analyze(profile,{
          'request_id':request_id,'provider':'connect','channel_id':str(item['channel_id']),
          'conversation_id':item['conversation_id'],'history_mode':'channel','language':'az',
          'period_start':(pilot_scope() or {}).get('start_after') or (item.get('extraction_started_at').isoformat() if isinstance(item.get('extraction_started_at'),datetime) else str(item.get('extraction_started_at') or item['last_message_at'])),'period_end':end_at},
          before_provider=lambda: require_extraction_enabled(item))
        request=response.get('request') or {}
        state=request.get('status')
        if state not in {'completed','failed','unknown'}:
            finish(item,error_code='extraction_state_unknown')
        else:
            finish(item,request_id,'' if state=='completed' else 'provider_'+state)
    except LiveExtractionDisabled:
        # The service stopped at a pre-provider guard. Keep an explicit safe
        # terminal receipt; a later genuine message creates a fresh generation.
        finish(item,error_code='live_extraction_disabled_before_provider')
        return False
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
