"""Owner-only, incident-deduplicated alerts from existing CRM queues and receipts."""
import asyncio
from datetime import datetime, timezone
import json
from uuid import uuid4

from tenant_notice_transport import attempt

MIN_COUNT = 3
CAP = 20
SIGNS = {
    'send': ('Проверить отправку сообщений', (
        ("saas_chat_send_receipts", """state IN ('sending','unknown') AND
          updated_at >= now()-interval '30 minutes' AND
          (state='unknown' OR updated_at < now()-interval '5 minutes')"""),)),
    'queue': ('Проверить очередь синхронизации CRM', (
        ("saas_crm_sync_outbox", """state IN ('pending','processing') AND
          created_at < now()-interval '5 minutes' AND
          ((state='pending' AND next_attempt_at < now()-interval '5 minutes')
           OR (state='processing' AND lease_until < now()-interval '5 minutes'))"""),
        ("saas_crm_scalar_outbox", """state IN ('pending','processing')
          AND updated_at < now()-interval '5 minutes'"""),
        ("saas_crm_relation_outbox", """state IN ('pending','processing') AND
          updated_at < now()-interval '5 minutes' AND next_attempt_at < now()-interval '5 minutes'"""))),
    'sync': ('Проверить ошибки синхронизации CRM', (
        ("saas_crm_sync_outbox", """state IN ('failed','conflict','unknown') AND
          updated_at >= now()-interval '30 minutes'"""),
        ("saas_crm_scalar_outbox", """state IN ('conflict','unknown','needs_verification') AND
          updated_at >= now()-interval '30 minutes'"""),
        ("saas_crm_relation_outbox", """state IN ('conflict','unknown') AND
          updated_at >= now()-interval '30 minutes'"""))),
}
# Delivery states are evidence of CRM uncertainty, not evidence of customer
# non-delivery. Aged-out samples are not presented as repaired records.


def transition(state, count, now, owner, incident_factory=uuid4):
    """Two observations span at least 55s; gaps over 180s break corroboration."""
    bad = count >= MIN_COUNT
    current = dict(state or {})
    symptom = 'bad' if bad else 'good'
    elapsed = now - current.get('sample_time', now)
    if elapsed < 0:
        return current, None
    if current.get('sample') != symptom or elapsed > 180:
        current.update(sample=symptom, sample_time=now)
        return current, None
    if elapsed < 55:
        return current, None
    current['sample_time'] = now
    if bad and not current.get('incident'):
        incident = str(incident_factory())
        current.update(incident=incident, recipient=owner, acknowledged=False)
        return current, dict(incident=incident, recipient_id=owner, kind='down', count=count, send=True)
    if not bad and current.get('incident'):
        incident = current.pop('incident')
        recipient = current.pop('recipient', None)
        acknowledged = current.pop('acknowledged', False)
        if acknowledged and recipient == owner:
            return current, dict(incident=incident, recipient_id=owner, kind='up', send=True)
    return current, None


def _db():
    from tenant_platform_core import _connect
    return _connect(connect_timeout=3, options="-c lock_timeout=300 -c statement_timeout=1200")


def _count(cur, tenant, signal):
    total = 0
    for table, predicates in SIGNS[signal][1]:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL AS ready", (table,))
        if not (cur.fetchone() or {}).get('ready'):
            # Missing evidence cannot clear an existing alert.
            return None
        cur.execute(f"""SELECT count(*) AS n FROM (
          SELECT 1 FROM {table} WHERE tenant_id=%s::uuid AND {predicates}
          LIMIT %s
        ) recent""", (tenant, CAP+1-total))
        total += int(cur.fetchone()['n'])
        if total > CAP:
            break
    return total


def _step(tenant, signal, now):
    from tenant_connect_health import _owner
    with _db() as conn, conn.cursor() as cur:
        owner = _owner(cur, tenant)
        if owner is None:
            return None
        value = _count(cur, tenant, signal)
        if value is None:
            return None
        cur.execute("""INSERT INTO saas_owner_operational_alert_checkpoints(tenant_id,signal)
          VALUES(%s::uuid,%s) ON CONFLICT DO NOTHING""", (tenant, signal))
        cur.execute("""SELECT value FROM saas_owner_operational_alert_checkpoints
          WHERE tenant_id=%s::uuid AND signal=%s FOR UPDATE""", (tenant,signal))
        row = cur.fetchone()
        if row is None:
            return None
        state = row['value']
        result, item = transition(state, value, now, owner)
        if result != state:
            cur.execute("""UPDATE saas_owner_operational_alert_checkpoints SET value=%s::jsonb,updated_at=now()
              WHERE tenant_id=%s::uuid AND signal=%s""", (json.dumps(result),tenant,signal))
        return item


def _confirm(tenant, signal, item):
    with _db() as conn, conn.cursor() as cur:
        cur.execute("""UPDATE saas_owner_operational_alert_checkpoints SET
          value=jsonb_set(value,'{acknowledged}','true'::jsonb),updated_at=now()
          WHERE tenant_id=%s::uuid AND signal=%s AND value->>'incident'=%s
          AND value->>'recipient'=%s""", (tenant,signal,item['incident'],str(item['recipient_id'])))


def _still_owner(tenant, item):
    from tenant_connect_health import _owner
    from tenant_beinsystems_operator_access import configured_workspace_id
    if configured_workspace_id() != tenant:
        return False
    with _db() as conn, conn.cursor() as cur:
        return _owner(cur, tenant) == item['recipient_id']


def _message(signal, item):
    label = SIGNS[signal][0]
    if item['kind'] == 'up':
        return f"CRM: порог по сигналу «{label}» больше не превышен. Это не подтверждает доставку или обработку отдельных записей."
    count = f"{CAP}+" if item['count'] > CAP else str(item['count'])
    details = {
        'send':'Не подтверждено принятие провайдером или отправка зависла',
        'queue':'Задания задержаны минимум на 5 минут',
        'sync':'За последние 30 минут есть ошибки или неподтверждённые изменения',
    }[signal]
    return f"CRM: {label}. {details}: {count}.\nhttps://crm.pro.az/app"


async def deliver(bot, logger):
    from tenant_beinsystems_operator_access import configured_workspace_id
    tenant = configured_workspace_id()
    if bot is None or not tenant:
        return
    for signal in SIGNS:
        try:
            item = await asyncio.to_thread(_step, tenant, signal, int(datetime.now(timezone.utc).timestamp()))
            if not item or not await asyncio.to_thread(_still_owner, tenant, item):
                continue
            result = await asyncio.wait_for(
                attempt(item, 'telegram', bot, text=lambda item: _message(signal, item),
                        parts=lambda text: [text]), timeout=12)
            if result.status == 'delivered' and result.message_id:
                if item['kind'] == 'down':
                    await asyncio.to_thread(_confirm, tenant, signal, item)
            else:
                logger.warning("Owner CRM incident notice not acknowledged; no replay (%s)", signal)
        except Exception:
            # Fixed label only: do not leak SQL or transport exception payloads.
            logger.warning("Owner CRM incident check unavailable (%s)", signal)
