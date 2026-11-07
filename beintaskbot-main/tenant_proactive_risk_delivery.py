"""Owner-only, opt-in delivery for important proactive risk findings.

This outbox is separate from status-change notices. It rechecks tenant
ownership, event preferences, current source scope and source evidence before
creating a bell entry or reserving a push attempt. Unknown provider outcomes
are terminal and are never retried automatically.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import uuid

from tenant_platform import _connect, _read_tenant_workflow
from tenant_policy import TenantPolicy
from tenant_notifications import notification_channels
from tenant_proactive_risk_policy import analyze_crm_task, analyze_linear, crm_scope, fingerprint
from tenant_proactive_risk_schema import ensure_schema

MAX_RISK_ALERTS_PER_DAY = 10
MAX_RISK_EVENTS_PER_TICK = 5
MAX_BASELINE_FINDINGS_PER_TICK = 5
MAX_PUSH_ATTEMPTS_PER_TICK = 5
CRM_SOURCE_MAX_AGE = timedelta(minutes=15)
RISK_EVENT = 'proactive_risk'
DEFERRED_FOR_QUOTA = object()


def alertable(finding: dict) -> bool:
    """Only P1 candidates or medium/high-confidence P2 candidates notify."""
    return finding.get('severity') == 'P1' or (
        finding.get('severity') == 'P2' and finding.get('confidence') in {'medium', 'high'})


def alert_fingerprint(finding: dict) -> str:
    """Stable within one unresolved rule; changing elapsed-time facts do not spam."""
    return fingerprint([{'rule_id': str(finding.get('rule_id') or ''),
                         'severity': str(finding.get('severity') or ''),
                         'confidence': str(finding.get('confidence') or '')}])


def _advisory_lock(cur, namespace: str, identity: str) -> None:
    key = hashlib.sha256(f'{namespace}:{identity}'.encode()).hexdigest()[:16]
    cur.execute("SELECT pg_advisory_xact_lock(('x' || %s)::bit(64)::bigint)", (key,))


def lock_daily_quota_transition(cur, tenant: str) -> None:
    """Take the tenant quota lock before any source lock in a risk transaction."""
    _advisory_lock(cur, 'risk-daily-quota', str(tenant))


def lock_source_transition(cur, tenant: str, source_kind: str, source_id: str) -> None:
    """Call before reading/replacing source findings to serialize first scans too."""
    _advisory_lock(cur, 'risk-source', f'{tenant}:{source_kind}:{source_id}')


def _channels(profile: dict, tenant: str) -> list[str]:
    return notification_channels(TenantPolicy(profile), event=RISK_EVENT, tenant_id=tenant)


def _owner_profile(cur, tenant: str, recipient: int) -> dict | None:
    cur.execute('''SELECT m.*,t.owner_telegram_id,t.name AS tenant_name,t.status AS tenant_status,
                          t.modules,t.notification_rules,t.onboarding
      FROM saas_tenant_members m JOIN saas_tenants t ON t.id=m.tenant_id
      WHERE m.tenant_id=%s::uuid AND m.telegram_id=%s AND t.owner_telegram_id=m.telegram_id
        AND m.role='owner' AND m.active=TRUE AND t.status='active'
      FOR SHARE OF m,t''', (tenant, int(recipient)))
    row = cur.fetchone()
    if not row:
        return None
    return {**row, 'tenant_id': str(row['tenant_id']),
            'workflow': _read_tenant_workflow(cur, tenant)}


def _queue_new_finding(cur, tenant: str, source_kind: str, source_id: str,
                       source_scope: str, source_version: str, finding: dict) -> str | None | object:
    cur.execute("SELECT owner_telegram_id FROM saas_tenants WHERE id=%s::uuid AND status='active' FOR SHARE", (tenant,))
    tenant_row = cur.fetchone()
    if not tenant_row or not tenant_row.get('owner_telegram_id'):
        return None
    recipient = int(tenant_row['owner_telegram_id'])
    profile = _owner_profile(cur, tenant, recipient)
    if not profile or not _channels(profile, tenant):
        return None
    # The daily quota spans all source locks, so serialize the count and insert
    # for this tenant to prevent parallel scans of different tasks exceeding it.
    lock_daily_quota_transition(cur, tenant)
    cur.execute('''SELECT count(*) AS count FROM saas_proactive_risk_alert_events
      WHERE tenant_id=%s::uuid AND created_at>=now()-interval '1 day'
        AND state NOT IN ('suppressed','deferred') ''', (tenant,))
    limited = int((cur.fetchone() or {}).get('count') or 0) >= MAX_RISK_ALERTS_PER_DAY
    event_id = uuid.uuid4()
    cur.execute('''INSERT INTO saas_proactive_risk_alert_events
      (id,tenant_id,recipient_id,source_kind,source_id,rule_id,source_scope,source_version,alert_fingerprint,state,retry_after)
      VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,
        CASE WHEN %s THEN now()+interval '15 minutes' ELSE NULL END)
      ON CONFLICT DO NOTHING RETURNING id''',
      (event_id, tenant, recipient, source_kind, str(source_id), finding['rule_id'], str(source_scope),
       str(source_version), alert_fingerprint(finding), 'deferred' if limited else 'pending', limited))
    inserted = cur.fetchone()
    if limited:
        return DEFERRED_FOR_QUOTA if inserted else None
    return str(event_id) if inserted else None


def _event_exists(cur, tenant: str, source_kind: str, source_id: str,
                  rule_id: str, source_scope: str, signature: str) -> bool:
    cur.execute('''SELECT 1 FROM saas_proactive_risk_alert_events e
      JOIN saas_tenants t ON t.id=e.tenant_id AND t.owner_telegram_id=e.recipient_id
      WHERE e.tenant_id=%s::uuid AND e.source_kind=%s AND e.source_id=%s AND e.rule_id=%s
        AND e.source_scope=%s AND e.alert_fingerprint=%s
        AND e.state IN ('pending','expanded','deferred') LIMIT 1''',
      (tenant, source_kind, str(source_id), rule_id, str(source_scope), signature))
    return cur.fetchone() is not None


def register_baseline_backfill(cur, tenant: str, owner_id: int, *, restart: bool = False) -> bool:
    """Register one bounded initial review after the owner saves risk opt-in."""
    profile = _owner_profile(cur, str(tenant), int(owner_id))
    if not profile or not _channels(profile, str(tenant)):
        return False
    if restart:
        cur.execute('''INSERT INTO saas_proactive_risk_alert_backfills(tenant_id,owner_id)
          VALUES(%s::uuid,%s) ON CONFLICT(tenant_id,owner_id) DO UPDATE SET state='pending',
            cursor_source_kind='',cursor_source_id='',cursor_rule_id='',retry_after=NULL,updated_at=now()
          WHERE saas_proactive_risk_alert_backfills.state='done' RETURNING tenant_id''',
          (str(tenant), int(owner_id)))
    else:
        cur.execute('''INSERT INTO saas_proactive_risk_alert_backfills(tenant_id,owner_id)
          VALUES(%s::uuid,%s) ON CONFLICT(tenant_id,owner_id) DO NOTHING RETURNING tenant_id''',
          (str(tenant), int(owner_id)))
    return cur.fetchone() is not None


def _seed_one_baseline_alert(cur) -> tuple[int, bool]:
    """Commit at most one source transition so no transaction holds two source locks."""
    cur.execute("""SELECT * FROM saas_proactive_risk_alert_backfills
      WHERE state='pending' AND (retry_after IS NULL OR retry_after<=now())
      ORDER BY updated_at,tenant_id,owner_id LIMIT 1 FOR UPDATE SKIP LOCKED""")
    job = cur.fetchone()
    if not job:
        return 0, False
    tenant = str(job['tenant_id'])
    owner_id = int(job['owner_id'])
    profile = _owner_profile(cur, tenant, owner_id)
    if not profile:
        cur.execute("""UPDATE saas_proactive_risk_alert_backfills SET state='done',updated_at=now()
          WHERE tenant_id=%s::uuid AND owner_id=%s""", (tenant, owner_id))
        return 0, True
    channels = _channels(profile, tenant)
    if not channels:
        # Keep the cursor; a later explicit re-opt-in can resume the same job.
        cur.execute('''UPDATE saas_proactive_risk_alert_backfills SET retry_after=now()+interval '15 minutes',updated_at=now()
          WHERE tenant_id=%s::uuid AND owner_id=%s''', (tenant, owner_id))
        return 0, True

    cursor = (job.get('cursor_source_kind') or '', job.get('cursor_source_id') or '',
              job.get('cursor_rule_id') or '')
    cur.execute('''SELECT source_kind,source_id,rule_id,source_scope,source_version,
                          severity,confidence,source_title
      FROM saas_proactive_risk_findings
      WHERE tenant_id=%s::uuid AND active=TRUE
        AND (severity='P1' OR (severity='P2' AND confidence IN ('medium','high')))
        AND (source_kind,source_id,rule_id)>(%s,%s,%s)
      ORDER BY source_kind,source_id,rule_id LIMIT 1''', (tenant, *cursor))
    row = cur.fetchone()
    if not row:
        cur.execute("""UPDATE saas_proactive_risk_alert_backfills SET state='done',updated_at=now()
          WHERE tenant_id=%s::uuid AND owner_id=%s""", (tenant, owner_id))
        return 0, True

    row_key = (str(row['source_kind']), str(row['source_id']), str(row['rule_id']))
    # All paths use quota -> source ordering. CRM may process many sources in
    # one transaction, so acquiring quota after a source here can deadlock with
    # the observer when it advances to another source.
    lock_daily_quota_transition(cur, tenant)
    lock_source_transition(cur, tenant, row_key[0], row_key[1])
    cur.execute('''SELECT source_kind,source_id,rule_id,source_scope,source_version,
                          severity,confidence,source_title
      FROM saas_proactive_risk_findings WHERE tenant_id=%s::uuid AND source_kind=%s
        AND source_id=%s AND rule_id=%s AND active=TRUE FOR UPDATE''', (tenant, *row_key))
    current = cur.fetchone()
    queued = 0
    if current and alertable(current):
        signature = alert_fingerprint(current)
        if not _event_exists(cur, tenant, current['source_kind'], current['source_id'],
                             current['rule_id'], current['source_scope'], signature):
            candidate = {**current, 'tenant_id':tenant, 'recipient_id':owner_id,
                         'alert_fingerprint':signature}
            try:
                confirmed = current_finding(cur, candidate, profile)
            except Exception as error:
                from tenant_linear_policy import TenantLinearError
                if not isinstance(error, (TenantLinearError, TimeoutError, ConnectionError, OSError)):
                    raise
                if isinstance(error, TenantLinearError) and error.status not in {408, 429, 500, 502, 503, 504}:
                    raise
                # Release this job for other companies; retry the exact row later.
                cur.execute('''UPDATE saas_proactive_risk_alert_backfills
                  SET retry_after=now()+interval '15 minutes',updated_at=now()
                  WHERE tenant_id=%s::uuid AND owner_id=%s''', (tenant, owner_id))
                return 0, True
            if confirmed:
                queued_result = _queue_new_finding(cur, tenant, current['source_kind'], current['source_id'],
                    current['source_scope'], current['source_version'], current)
                if queued_result is DEFERRED_FOR_QUOTA:
                    # Quota exhaustion is temporary. Leave this cursor in place
                    # so it can be queued after older alerts age out.
                    cur.execute('''UPDATE saas_proactive_risk_alert_backfills
                      SET retry_after=now()+interval '15 minutes',updated_at=now()
                      WHERE tenant_id=%s::uuid AND owner_id=%s''', (tenant, owner_id))
                    return 0, True
                if queued_result is None:
                    cur.execute('''UPDATE saas_proactive_risk_alert_backfills
                      SET retry_after=now()+interval '15 minutes',updated_at=now()
                      WHERE tenant_id=%s::uuid AND owner_id=%s''', (tenant, owner_id))
                    return 0, True
                queued = 1

    cur.execute('''UPDATE saas_proactive_risk_alert_backfills SET cursor_source_kind=%s,
      cursor_source_id=%s,cursor_rule_id=%s,retry_after=NULL,updated_at=now()
      WHERE tenant_id=%s::uuid AND owner_id=%s''', (*row_key, tenant, owner_id))
    cur.execute('''SELECT 1 FROM saas_proactive_risk_findings
      WHERE tenant_id=%s::uuid AND active=TRUE
        AND (severity='P1' OR (severity='P2' AND confidence IN ('medium','high')))
        AND (source_kind,source_id,rule_id)>(%s,%s,%s) LIMIT 1''', (tenant, *row_key))
    if not cur.fetchone():
        cur.execute("""UPDATE saas_proactive_risk_alert_backfills SET state='done',updated_at=now()
          WHERE tenant_id=%s::uuid AND owner_id=%s""", (tenant, owner_id))
    return queued, True


def seed_baseline_alerts(limit: int = MAX_BASELINE_FINDINGS_PER_TICK) -> int:
    """Queue a bounded baseline batch, committing and releasing locks per source."""
    safe_limit = max(1, min(int(limit), MAX_BASELINE_FINDINGS_PER_TICK))
    queued = 0
    for _ in range(safe_limit):
        with _connect() as conn:
            ensure_schema(conn)
            with conn.cursor() as cur:
                added, worked = _seed_one_baseline_alert(cur)
            conn.commit()
        queued += added
        if not worked:
            break
    return queued


def promote_deferred_events(limit: int = MAX_RISK_EVENTS_PER_TICK) -> int:
    """Promote deferred alerts only after their tenant's rolling quota clears."""
    safe_limit = max(1, min(int(limit), MAX_RISK_EVENTS_PER_TICK))
    promoted = 0
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT id,tenant_id FROM saas_proactive_risk_alert_events
              WHERE state='deferred' AND (retry_after IS NULL OR retry_after<=now())
              ORDER BY retry_after,created_at,id LIMIT %s''', (safe_limit,))
            candidates=cur.fetchall()
            for event in candidates:
                tenant=str(event['tenant_id'])
                # Match observers/backfill: acquire quota before locking the
                # event row, otherwise a promoter can hold an event while
                # waiting on an observer that is cancelling that same event.
                lock_daily_quota_transition(cur,tenant)
                cur.execute('''SELECT id FROM saas_proactive_risk_alert_events
                  WHERE tenant_id=%s::uuid AND id=%s::uuid AND state='deferred'
                    AND (retry_after IS NULL OR retry_after<=now())
                  FOR UPDATE SKIP LOCKED''',(tenant,event['id']))
                if not cur.fetchone():
                    continue
                cur.execute('''SELECT count(*) AS count FROM saas_proactive_risk_alert_events
                  WHERE tenant_id=%s::uuid AND created_at>=now()-interval '1 day'
                    AND state NOT IN ('suppressed','deferred')''',(tenant,))
                used=int((cur.fetchone() or {}).get('count') or 0)
                if used >= MAX_RISK_ALERTS_PER_DAY:
                    cur.execute('''UPDATE saas_proactive_risk_alert_events
                      SET retry_after=now()+interval '15 minutes'
                      WHERE tenant_id=%s::uuid AND id=%s::uuid AND state='deferred' ''',
                      (tenant,event['id']))
                    continue
                cur.execute('''UPDATE saas_proactive_risk_alert_events SET state='pending',retry_after=NULL
                  WHERE tenant_id=%s::uuid AND id=%s::uuid AND state='deferred' RETURNING id''',
                  (tenant,event['id']))
                promoted += int(cur.fetchone() is not None)
        conn.commit()
    return promoted


def _cancel_event(cur, tenant: str, event_id) -> None:
    cur.execute("""UPDATE saas_proactive_risk_alert_events SET state='cancelled'
      WHERE tenant_id=%s::uuid AND id=%s::uuid AND state IN ('pending','expanded','deferred')""", (tenant, event_id))
    cur.execute("""UPDATE saas_proactive_risk_alert_deliveries SET status='skipped',updated_at=now()
      WHERE tenant_id=%s::uuid AND event_id=%s::uuid AND status='pending' """, (tenant, event_id))


def record_finding_transitions(cur, tenant: str, source_kind: str, source_id: str,
                               source_scope: str, source_version: str,
                               findings: list[dict]) -> None:
    """Queue important findings with no prior alert, then cancel stale work."""
    current = {(str(source_scope), str(item.get('rule_id')), alert_fingerprint(item))
               for item in findings if alertable(item)}
    for item in findings:
        rule = str(item.get('rule_id') or '')
        signature = alert_fingerprint(item)
        if (alertable(item) and not _event_exists(cur, tenant, source_kind, str(source_id), rule,
                                                   source_scope, signature)):
            _queue_new_finding(cur, tenant, source_kind, str(source_id), source_scope,
                               str(source_version), item)
    cur.execute('''SELECT id,rule_id,alert_fingerprint,source_scope
      FROM saas_proactive_risk_alert_events
      WHERE tenant_id=%s::uuid AND source_kind=%s AND source_id=%s
        AND state IN ('pending','expanded','deferred') FOR UPDATE''', (tenant, source_kind, str(source_id)))
    for event in cur.fetchall():
        identity = (str(event['source_scope']), str(event['rule_id']), str(event['alert_fingerprint']))
        if identity not in current:
            _cancel_event(cur, tenant, event['id'])


def current_finding(cur, event: dict, owner_profile: dict) -> dict | None:
    tenant = str(event['tenant_id'])
    cur.execute('''SELECT * FROM saas_proactive_risk_findings
      WHERE tenant_id=%s::uuid AND source_kind=%s AND source_id=%s AND rule_id=%s
        AND source_scope=%s AND active=TRUE''',
      (tenant, event['source_kind'], event['source_id'], event['rule_id'], event['source_scope']))
    stored = cur.fetchone()
    if not stored or alert_fingerprint(stored) != event['alert_fingerprint'] or not alertable(stored):
        return None

    now = datetime.now(timezone.utc)
    if event['source_kind'] == 'crm_task':
        if not TenantPolicy(owner_profile).allows('tasks'):
            return None
        cur.execute('''SELECT account_domain,connected_at FROM saas_tenant_integrations
          WHERE tenant_id=%s::uuid AND provider='kommo' AND status='connected' FOR SHARE''', (tenant,))
        connection = cur.fetchone()
        if not connection or crm_scope(connection['account_domain'], connection['connected_at']) != event['source_scope']:
            return None
        cur.execute('''SELECT * FROM saas_crm_tasks WHERE tenant_id=%s::uuid AND kommo_task_id::text=%s''',
                    (tenant, str(event['source_id'])))
        task = cur.fetchone()
        if not task or task.get('completed') is not False:
            return None
        synced = task.get('synced_at')
        if isinstance(synced, str):
            try:
                synced = datetime.fromisoformat(synced.replace('Z', '+00:00'))
            except ValueError:
                return None
        if not synced or synced.tzinfo is None or now - synced.astimezone(timezone.utc) > CRM_SOURCE_MAX_AGE:
            return None
        finding = next((item for item in analyze_crm_task(task, checked_at=now)
                        if alertable(item) and item.get('rule_id') == event['rule_id']
                        and alert_fingerprint(item) == event['alert_fingerprint']), None)
        return ({**finding, 'source_kind':'crm_task', 'source_id':str(event['source_id']),
                 'source_title':str(stored.get('source_title') or f"CRM task {event['source_id']}")[:200],
                 'source_url':'/app?view=tasks', 'source':'CRM task'} if finding else None)

    # Linear authorization is re-read from the current owner membership and
    # encrypted tenant connection, then the exact issue is fetched live.
    from tenant_linear_context import context
    from tenant_linear_observer_policy import source_time
    from tenant_linear_tasks_provider import issue
    from tenant_proactive_risk_policy import analyze_linear, linear_scope

    from tenant_linear_policy import TenantLinearError
    try:
        profile, config, key, config_version = context(cur, {
            'tenant_id': tenant, 'telegram_id': int(owner_profile['telegram_id'])})
    except TenantLinearError as error:
        if error.status in {429, 500, 502, 503}:
            raise
        return None
    workflow = config.get('workflow') or {}
    team = str(config.get('team_id') or '')
    if workflow.get('enabled') is not True or workflow.get('sync_enabled') is not True:
        return None
    if linear_scope(config_version, team) != event['source_scope']:
        return None
    try:
        current = issue(key, str(event['source_id']))
    except TenantLinearError as error:
        if error.status in {429, 500, 502, 503}:
            raise
        return None
    if not current or str((current.get('team') or {}).get('id') or '') != team:
        return None
    if str((current.get('state') or {}).get('id') or '') in set(config.get('done_state_ids') or []):
        return None
    if not source_time(current.get('updatedAt')):
        return None
    onboarding = owner_profile.get('onboarding') or {}
    timezone_name = onboarding.get('timezone') or (onboarding.get('company') or {}).get('timezone')
    finding = next((item for item in analyze_linear(current, tenant_timezone=timezone_name, checked_at=now)
                    if alertable(item) and item.get('rule_id') == event['rule_id']
                    and alert_fingerprint(item) == event['alert_fingerprint']), None)
    return ({**finding, 'source_kind':'linear', 'source_id':str(event['source_id']),
             'source_title':str(current.get('title') or stored.get('source_title') or '')[:200],
             'source_url':str(current.get('url') or ''),
             'source':'Linear'} if finding else None)


def _finding_is_current(cur, event: dict, owner_profile: dict) -> bool:
    return current_finding(cur, event, owner_profile) is not None


def _authorize_event(cur, event: dict, *, channel: str | None = None) -> tuple[dict | None, bool]:
    tenant = str(event['tenant_id'])
    owner = _owner_profile(cur, tenant, int(event['recipient_id']))
    if not owner:
        return None, False
    channels = _channels(owner, tenant)
    if (not channels or (channel and channel not in channels)
            or not _finding_is_current(cur, event, owner)):
        return owner, False
    return owner, True


def _transient_source_error(error: Exception) -> bool:
    from tenant_linear_policy import TenantLinearError
    if isinstance(error, TenantLinearError):
        return error.status in {408, 429, 500, 502, 503, 504}
    return isinstance(error, (TimeoutError, ConnectionError, OSError))


def expand_events(limit: int = MAX_RISK_EVENTS_PER_TICK) -> int:
    """Add bell entries and freeze only currently bound owner devices."""
    safe_limit = max(1, min(int(limit), MAX_RISK_EVENTS_PER_TICK))
    expanded = 0
    seed_baseline_alerts()
    promote_deferred_events()
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT * FROM saas_proactive_risk_alert_events
              WHERE state='pending' AND (retry_after IS NULL OR retry_after<=now())
              ORDER BY created_at,id LIMIT %s FOR UPDATE SKIP LOCKED''', (safe_limit,))
            for event in cur.fetchall():
                event['tenant_id'] = str(event['tenant_id'])
                try:
                    owner, allowed = _authorize_event(cur, event)
                except Exception as error:
                    if not _transient_source_error(error):
                        raise
                    cur.execute('''UPDATE saas_proactive_risk_alert_events
                      SET retry_after=now()+interval '15 minutes'
                      WHERE tenant_id=%s::uuid AND id=%s::uuid AND state='pending' ''',
                      (event['tenant_id'], event['id']))
                    continue
                if not allowed:
                    _cancel_event(cur, event['tenant_id'], event['id'])
                    continue
                channels = _channels(owner, event['tenant_id'])
                if 'internal' in channels:
                    event_key = 'proactive_risk:' + str(event['id'])
                    cur.execute('''INSERT INTO saas_tenant_notifications
                      (id,tenant_id,telegram_id,event_key,title,body,target_url)
                      SELECT %s::uuid,%s::uuid,%s,%s,%s,%s,%s
                      WHERE NOT EXISTS (SELECT 1 FROM saas_tenant_notifications
                        WHERE tenant_id=%s::uuid AND telegram_id=%s AND event_key=%s)''',
                      (uuid.uuid4(), event['tenant_id'], event['recipient_id'], event_key,
                       'Risk yoxlaması tələb olunur',
                       'Tapşırıq üzrə yoxlanmalı siqnal var. Mənbəni kabinetdə yoxlayın.',
                       '/app?view=linear&risk_alert_id=' + str(event['id']),
                       event['tenant_id'], event['recipient_id'], event_key))
                    cur.execute('''INSERT INTO saas_proactive_risk_alert_deliveries
                      (tenant_id,event_id,recipient_id,channel,status)
                      VALUES(%s::uuid,%s::uuid,%s,'internal','delivered') ON CONFLICT DO NOTHING''',
                      (event['tenant_id'], event['id'], event['recipient_id']))
                if 'push' in channels:
                    cur.execute('''INSERT INTO saas_proactive_risk_alert_deliveries
                      (tenant_id,event_id,recipient_id,channel,endpoint_hash)
                      SELECT %s::uuid,%s::uuid,%s,'push',b.endpoint_hash
                      FROM saas_member_push_devices b JOIN saas_push_devices p USING(endpoint_hash)
                      WHERE b.tenant_id=%s::uuid AND b.telegram_id=%s AND b.active=TRUE
                        AND p.owner_telegram_id=b.telegram_id ON CONFLICT DO NOTHING''',
                      (event['tenant_id'], event['id'], event['recipient_id'],
                       event['tenant_id'], event['recipient_id']))
                cur.execute('''UPDATE saas_proactive_risk_alert_events SET state='expanded'
                  WHERE tenant_id=%s::uuid AND id=%s::uuid AND state='pending' ''',
                  (event['tenant_id'], event['id']))
                expanded += 1
        conn.commit()
    return expanded


def delivery_key(item: dict) -> tuple:
    return (str(item['tenant_id']), str(item['event_id']), int(item['recipient_id']),
            str(item['endpoint_hash']))


def claim_push() -> dict | None:
    """Recheck owner, preference, current source and exact device before I/O."""
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''UPDATE saas_proactive_risk_alert_deliveries SET status='unknown',updated_at=now()
              WHERE channel='push' AND status='sending' AND updated_at<now()-interval '5 minutes' ''')
            cur.execute('''SELECT d.*,e.source_kind,e.source_id,e.rule_id,e.source_scope,e.source_version,
                    e.alert_fingerprint,e.state AS event_state,p.subscription,p.owner_telegram_id,
                    b.active AS device_active,t.owner_telegram_id AS current_owner_id
              FROM saas_proactive_risk_alert_deliveries d
              JOIN saas_proactive_risk_alert_events e ON e.tenant_id=d.tenant_id AND e.id=d.event_id
              JOIN saas_tenants t ON t.id=d.tenant_id
              LEFT JOIN saas_push_devices p ON p.endpoint_hash=d.endpoint_hash
              LEFT JOIN saas_member_push_devices b ON b.tenant_id=d.tenant_id
                AND b.telegram_id=d.recipient_id AND b.endpoint_hash=d.endpoint_hash
              WHERE d.channel='push' AND d.status='pending' AND e.state='expanded'
              ORDER BY d.updated_at,d.event_id LIMIT 1 FOR UPDATE OF d SKIP LOCKED''')
            item = cur.fetchone()
            if not item:
                conn.commit()
                return None
            item['tenant_id'] = str(item['tenant_id'])
            owner, event_allowed = _authorize_event(cur, item, channel='push')
            # Source/Linear revalidation above can be slow. Re-read and lock the
            # exact owner-device binding only at the reservation boundary.
            cur.execute('''SELECT p.subscription,p.owner_telegram_id,b.active AS device_active,
                    t.owner_telegram_id AS current_owner_id
              FROM saas_member_push_devices b
              JOIN saas_push_devices p ON p.endpoint_hash=b.endpoint_hash
              JOIN saas_tenants t ON t.id=b.tenant_id
              WHERE b.tenant_id=%s::uuid AND b.telegram_id=%s AND b.endpoint_hash=%s
                AND p.owner_telegram_id=b.telegram_id
              FOR UPDATE OF b,p''',
              (item['tenant_id'], item['recipient_id'], item['endpoint_hash']))
            device = cur.fetchone() or {}
            item.update(device)
            device_allowed = bool(device.get('device_active') is True and device.get('subscription')
                and device.get('owner_telegram_id') == item['recipient_id']
                and device.get('current_owner_id') == item['recipient_id'])
            allowed = bool(event_allowed and item.get('event_state') == 'expanded' and device_allowed)
            cur.execute('''UPDATE saas_proactive_risk_alert_deliveries SET status=%s,updated_at=now()
              WHERE tenant_id=%s::uuid AND event_id=%s::uuid AND recipient_id=%s
                AND channel='push' AND endpoint_hash=%s AND status='pending' ''',
              ('sending' if allowed else 'skipped', *delivery_key(item)))
            if not event_allowed and item.get('event_state') == 'expanded':
                _cancel_event(cur, item['tenant_id'], item['event_id'])
            item.update(send=allowed, event=RISK_EVENT)
            item['target_url'] = '/app?view=linear&risk_alert_id=' + str(item['event_id'])
        conn.commit()
    return item


def finish_push(item: dict, status: str) -> None:
    if status not in {'accepted', 'unknown', 'expired'}:
        raise ValueError('Invalid delivery result')
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''UPDATE saas_proactive_risk_alert_deliveries SET status=%s,updated_at=now()
              WHERE tenant_id=%s::uuid AND event_id=%s::uuid AND recipient_id=%s
                AND channel='push' AND endpoint_hash=%s AND status='sending' ''',
              (status, *delivery_key(item)))
            if status == 'expired':
                cur.execute('''UPDATE saas_member_push_devices SET active=FALSE,updated_at=now()
                  WHERE tenant_id=%s::uuid AND telegram_id=%s AND endpoint_hash=%s''',
                  (item['tenant_id'], item['recipient_id'], item['endpoint_hash']))
        conn.commit()


async def deliver(bot, private_key: str, claims: dict, logger) -> None:
    """Bounded cycle; bell writes are transactional, push unknown is terminal."""
    import asyncio
    from tenant_notice_transport import attempt
    from tenant_push_worker import send_push

    try:
        await asyncio.to_thread(expand_events)
    except Exception:
        logger.warning('Proactive risk alert expansion unavailable')
        return
    if not private_key:
        return
    for _ in range(MAX_PUSH_ATTEMPTS_PER_TICK):
        try:
            item = await asyncio.to_thread(claim_push)
        except Exception:
            logger.warning('Proactive risk push claim unavailable')
            return
        if not item:
            break
        if not item['send']:
            continue
        result = await attempt(item, 'push', None, text=lambda _row: '', parts=lambda _text: [],
                               push_sender=send_push, private_key=private_key, claims=claims)
        status = 'accepted' if result.status == 'delivered' else result.status
        if status != 'accepted':
            logger.warning('Proactive risk push result=%s tenant=%s', status, item['tenant_id'])
        await asyncio.to_thread(finish_push, item, status)
