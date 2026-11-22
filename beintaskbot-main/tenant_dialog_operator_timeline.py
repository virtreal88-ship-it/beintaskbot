"""Read-side only: actual native accept/start/transfer audit evidence.

Call only from visible, exact authorized operator history; this is not route
authorization. The projection never becomes a message or provider event.
"""
from collections import OrderedDict
from threading import Lock
from time import monotonic

from psycopg.errors import LockNotAvailable, QueryCanceled


_PAGE_LIMIT = 60
_RETRY_SECONDS = 12
_MAX_COOLDOWNS = 512
_MAX_ACTIVE = 16
_probe_lock = Lock()
_cooldown = OrderedDict()
_active = set()


def _retry(remaining):
    return {"operator_events_retry_after_ms": max(250, int(remaining * 1000) + 1)}


def _stamp(message):
    return message.get("happened_at") or message.get("created_at")


def page(conn, cur, tenant, channel, conversation, **window):
    # Advisory process-local backoff only; it never confers permission and
    # cannot substitute for the exact route checks in history().
    key=(tenant,channel,conversation)
    with _probe_lock:
        now=monotonic()
        while _cooldown and next(iter(_cooldown.values()))<=now:
            _cooldown.popitem(last=False)
        retry_at=_cooldown.get(key, 0)
        if now<retry_at:
            return _retry(retry_at-now)
        _cooldown.pop(key,None)
        if key in _active or len(_active)>=_MAX_ACTIVE:
            return _retry(2)
        _active.add(key)
    try:
        return _read_window(conn,cur,tenant,channel,conversation,**window)
    except (LockNotAvailable, QueryCanceled):
        with _probe_lock:
            _cooldown[key]=monotonic()+_RETRY_SECONDS
            _cooldown.move_to_end(key)
            while len(_cooldown)>_MAX_COOLDOWNS:
                _cooldown.popitem(last=False)
        return _retry(_RETRY_SECONDS)
    finally:
        with _probe_lock:
            _active.discard(key)


def history_window(cur, tenant, conversation, *, messages, before,
                   history_leads, history_channel, group_channels=None):
    """Fence metadata to the same authorized message page as the timeline."""
    upper = None
    if before:
        if group_channels is not None:
            cur.execute("""SELECT COALESCE(m.happened_at,m.created_at) AS stamp
              FROM saas_crm_messages m JOIN saas_connect_conversations c
                ON c.tenant_id=m.tenant_id AND c.lead_id=m.kommo_lead_id
                AND m.channel='connect:'||c.connect_channel_id::text
              WHERE m.tenant_id=%s::uuid AND m.id=%s::uuid AND c.conversation_id=%s
                AND c.connect_channel_id=ANY(%s::uuid[])""",
                (tenant,before,conversation,group_channels))
        else:
            cur.execute("""SELECT COALESCE(happened_at,created_at) AS stamp
              FROM saas_crm_messages WHERE tenant_id=%s::uuid AND id=%s::uuid
                AND kommo_lead_id=ANY(%s::bigint[]) AND channel=%s""",
                (tenant,before,history_leads,history_channel))
        boundary = cur.fetchone()
        if not boundary:
            return None
        upper = boundary["stamp"]
    # Include earlier events on the oldest page, without changing the cursor.
    lower = _stamp(messages[49]) if len(messages)>50 else None
    return lower, upper


def _read_window(conn, cur, tenant, channel, conversation, *, messages, before,
                 history_leads, history_channel, group_channels=None):
    # No new index/migration. Limit the optional JSON audit read's latency,
    # and keep a timeout from suppressing otherwise valid chat history.
    # The statement setting is restored on success or savepoint rollback.
    with conn.transaction():
        cur.execute("""SELECT current_setting('statement_timeout') AS previous,setting::integer AS milliseconds
          FROM pg_settings WHERE name='statement_timeout'""")
        setting = cur.fetchone()
        timeout = setting["previous"]
        budget = min(setting["milliseconds"] or 200, 200)
        cur.execute("SELECT set_config('statement_timeout',%s,true)", (f"{budget}ms",))
        window = history_window(cur,tenant,conversation,messages=messages,before=before,
            history_leads=history_leads,history_channel=history_channel,group_channels=group_channels)
        if window is None:
            cur.execute("SELECT set_config('statement_timeout',%s,true)", (timeout,))
            return {'operator_events': []}
        lower,upper=window
        cur.execute("""SELECT a.id::text AS id,a.action,a.created_at,m.display_name
          FROM saas_tenant_audit_events a
          LEFT JOIN saas_tenant_members m ON m.tenant_id=a.tenant_id
            AND m.telegram_id::text=a.payload->>'operator_id'
          WHERE a.tenant_id=%s::uuid AND a.entity_type='dialog_episode'
            AND a.action IN ('dialog_accept','dialog_start','dialog_transfer')
            AND a.payload->>'channel_id'=%s AND a.payload->>'conversation_id'=%s
            AND (a.action='dialog_transfer' OR a.actor_telegram_id::text=a.payload->>'operator_id')
            AND (%s::timestamptz IS NULL OR a.created_at>=%s::timestamptz)
            AND (%s::timestamptz IS NULL OR a.created_at<%s::timestamptz)
          ORDER BY a.created_at DESC,a.id DESC LIMIT %s""",
            (tenant,channel,conversation,lower,lower,upper,upper,_PAGE_LIMIT+1))
        rows=cur.fetchall()
        cur.execute("SELECT set_config('statement_timeout',%s,true)", (timeout,))
    kinds={"dialog_accept":"accept","dialog_start":"start","dialog_transfer":"transfer"}
    shown=rows[:_PAGE_LIMIT]
    result={"operator_events": [{"id":row["id"],"kind":kinds[row["action"]],"at":row["created_at"].isoformat(),
                                "employee_name":str(row["display_name"] or "").strip()[:120]}
                               for row in reversed(shown)]}
    if len(rows)>_PAGE_LIMIT:
        result["operator_events_omitted_at"]=shown[-1]["created_at"].isoformat()
    return result
