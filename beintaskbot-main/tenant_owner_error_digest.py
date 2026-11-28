"""Bounded tenant-owner digest of CRM synchronization errors; no customer payload."""
import asyncio
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from tenant_platform import _connect
from tenant_notice_transport import attempt

_ZONE = ZoneInfo("Asia/Baku")
_DUE = time(18, 0)
_KEY = "operator_owner_error_digest_v1"
# Fixed statements, no raw error code, customer ID, or provider payload is selected.
_COUNTS = (
    ("saas_crm_sync_outbox", "Ошибки синхронизации контактов и сделок",
     "state IN ('failed','conflict','unknown')"),
    ("saas_crm_scalar_outbox", "Не подтверждены изменения контактов и сделок",
     "state IN ('conflict','unknown','needs_verification')"),
    ("saas_crm_relation_outbox", "Ошибки синхронизации связей CRM",
     "state IN ('conflict','unknown')"),
)


def window(now):
    local = now.astimezone(_ZONE)
    end = datetime.combine(local.date(), _DUE, tzinfo=_ZONE)
    if local < end:
        return None
    return (local.date().isoformat(), (end-timedelta(days=1)).astimezone(timezone.utc),
            end.astimezone(timezone.utc))


def _db():
    return _connect(connect_timeout=3, options="-c lock_timeout=500 -c statement_timeout=2500")


def _collect(cur, tenant, start, end):
    items = []
    for table, label, condition in _COUNTS:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL AS ready", (table,))
        if not cur.fetchone()['ready']:
            continue
        # Cap per category and never retrieve individual records or raw logs.
        cur.execute(f"""SELECT count(*) AS n FROM (
          SELECT 1 FROM {table}
          WHERE tenant_id=%s::uuid AND updated_at>=%s AND updated_at<%s AND {condition}
          LIMIT 101
        ) errors""", (tenant, start, end))
        count = int(cur.fetchone()['n'])
        if count:
            items.append((label, '100+' if count > 100 else str(count)))
    return items


def _claim(tenant, period):
    from tenant_connect_health import _owner
    day, start, end = period
    with _db() as conn, conn.cursor() as cur:
        # The row only holds the last processed period. Lock the transaction
        # across claim and snapshot, never across network delivery.
        cur.execute("""INSERT INTO saas_tenant_workflow_policies(tenant_id,policy_key)
          VALUES(%s::uuid,%s) ON CONFLICT DO NOTHING""", (tenant, _KEY))
        cur.execute("""SELECT value->>'day' AS day FROM saas_tenant_workflow_policies
          WHERE tenant_id=%s::uuid AND policy_key=%s FOR UPDATE""", (tenant, _KEY))
        saved = cur.fetchone()
        if not saved or (saved['day'] or '') >= day:
            return None
        owner = _owner(cur, tenant)
        if owner is None:
            return None
        items = _collect(cur, tenant, start, end)
        cur.execute("""UPDATE saas_tenant_workflow_policies SET value=jsonb_build_object('day',%s::text),
          updated_at=now() WHERE tenant_id=%s::uuid AND policy_key=%s""", (day, tenant, _KEY))
        if items:
            return {'recipient_id': owner, 'day': day, 'items': items, 'send': True}
    return None


def _still_owner(tenant, recipient):
    from tenant_connect_health import _owner
    with _db() as conn, conn.cursor() as cur:
        return _owner(cur, tenant) == recipient


def _text(item):
    lines = [f"CRM: сводка ошибок за сутки к 18:00 ({item['day']}, по Баку)"]
    lines.extend(f"• {label}: {count}" for label, count in item['items'])
    return "\n".join(lines + ["Проверьте разделы CRM и настройки интеграций.", "https://crm.pro.az/app"])


async def deliver(bot, logger):
    from tenant_operator_widget_sync import enabled
    tenant = enabled()
    period = window(datetime.now(timezone.utc))
    if bot is None or not tenant or not period:
        return
    item = await asyncio.to_thread(_claim, tenant, period)
    if not item or not await asyncio.to_thread(_still_owner, tenant, item['recipient_id']):
        return
    result = await attempt(item, 'telegram', bot, text=_text, parts=lambda text: [text])
    if result.status != 'delivered':
        logger.warning("Owner CRM error digest delivery unknown; no automatic replay")
