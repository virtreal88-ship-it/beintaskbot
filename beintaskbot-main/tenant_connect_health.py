"""Connection observations and owner-only notices. Never reconnect or send WhatsApp.

A reported open socket is not proof of message delivery. Silence is not evidence
of a broken connection. Telegram attempts are checkpointed before network I/O.
"""
from datetime import datetime, timedelta, timezone
import asyncio
import json
import os
from uuid import UUID, uuid4

EVENT = 'channel.connection.updated'
BAD = {'disconnected', 'connecting', 'error'}
STATUSES = BAD | {'connected', 'unknown'}


def timestamp(value):
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timezone required')
    return result.astimezone(timezone.utc)


def observe(previous, status, observed_at, now):
    """Pure ordered reducer; returns state and optional recovered incident id."""
    if status not in STATUSES:
        raise ValueError('Unknown connection status')
    at = timestamp(observed_at)
    if at > now + timedelta(seconds=60):
        raise ValueError('Future connection observation')
    state = dict(previous or {})
    if state.get('lastObservedAt'):
        previous_at = timestamp(state['lastObservedAt'])
        if at < previous_at or (at == previous_at and state.get('status') == status):
            return state, None
        if at == previous_at:
            # Event-ID receipts dedupe identity. Millisecond timestamps cannot
            # order distinct conflicting events, and arrival order is unsafe.
            status = 'unknown'
            state['ambiguousTimestamp'] = at.isoformat()
        else:
            state.pop('ambiguousTimestamp', None)
    recovered = None
    if status == 'connected':
        if state.get('alertQueued'):
            recovered = state.get('incidentId')
        if state.get('incidentId'):
            state['recoveredIncidentId'] = state['incidentId']
        state.update(badSince=None, incidentId=None, alertQueued=False, alertAttempted=False)
    elif status in BAD:
        if state.get('status') not in BAD:
            state['badSince'] = at.isoformat()
        if not state.get('incidentId'):
            state['incidentGeneration'] = int(state.get('incidentGeneration') or 0) + 1
            state['incidentId'] = str(uuid4())
            state['alertAttempted'] = False
        state['recoveredIncidentId'] = None
    else:
        # Unknown is neither outage proof nor recovery proof.
        state['badSince'] = None
        state['recoveredIncidentId'] = None
        # A queued but unattempted alert is no longer backed by continuous
        # bad evidence. Retire it; a later bad observation starts fresh.
        # Never retire an external attempt (including uncertain outcomes).
        if not state.get('alertAttempted'):
            state.update(incidentId=None, alertQueued=False, alertAttempted=False)
    state.update(status=status, lastObservedAt=at.isoformat())
    return state, recovered


def debounce_elapsed(state, now):
    if state.get('status') not in BAD or not state.get('badSince'):
        return False
    seconds = 300 if state['status'] == 'connecting' else 90
    return now >= timestamp(state['badSince']) + timedelta(seconds=seconds)


def due(state, now):
    return not state.get('alertQueued') and debounce_elapsed(state, now)


SCHEMA_COLUMNS = {
    'saas_connect_health': ('tenant_id','channel_id','state','updated_at'),
    'saas_connect_health_notices': ('tenant_id','channel_id','incident_id','kind','recipient_id','status','message_id','updated_at'),
}
SCHEMA_INDEXES = ('saas_connect_health_pkey','saas_connect_health_notices_pkey','saas_connect_health_pending_idx')


def require_schema(conn):
    from tenant_schema_preflight import ready
    with conn.cursor() as cur:
        if not ready(cur, SCHEMA_COLUMNS, SCHEMA_INDEXES):
            raise RuntimeError('Connect health startup schema preparation required')


def prepare_database(conn):
    """Only invoked by fail-fast startup, before HTTP or scheduler listeners."""
    from tenant_connect_transport import _schema
    _schema(conn)
    ensure_schema(conn)
    require_schema(conn)


def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('saas_connect_health') IS NOT NULL AND to_regclass('saas_connect_health_notices') IS NOT NULL AS ready")
        if (cur.fetchone() or {}).get('ready'):
            return
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended('connect-health-schema',0))")
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_connect_health (
          tenant_id UUID NOT NULL, channel_id UUID NOT NULL, state JSONB NOT NULL DEFAULT '{}',
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(tenant_id,channel_id),
          FOREIGN KEY(tenant_id,channel_id) REFERENCES saas_connect_channels(tenant_id,connect_channel_id) ON DELETE CASCADE)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_connect_health_notices (
          tenant_id UUID NOT NULL, channel_id UUID NOT NULL, incident_id UUID NOT NULL,
          kind TEXT NOT NULL CHECK(kind IN ('down','recovered')), recipient_id BIGINT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sending','delivered','unknown','skipped')),
          message_id BIGINT, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,channel_id,incident_id,kind),
          FOREIGN KEY(tenant_id,channel_id) REFERENCES saas_connect_health(tenant_id,channel_id) ON DELETE CASCADE)''')
        cur.execute("CREATE INDEX IF NOT EXISTS saas_connect_health_pending_idx ON saas_connect_health_notices(updated_at) WHERE status IN ('pending','sending')")


def _owner(cur, tenant):
    cur.execute('''SELECT t.owner_telegram_id FROM saas_tenants t JOIN saas_tenant_members m
      ON m.tenant_id=t.id AND m.telegram_id=t.owner_telegram_id
      WHERE t.id=%s::uuid AND t.status IN ('active','onboarding','ready_for_integration')
      AND m.role='owner' AND m.active=TRUE AND m.telegram_id>0''', (tenant,))
    return (cur.fetchone() or {}).get('owner_telegram_id')


def _queue(cur, tenant, channel, incident, kind):
    if str(tenant) not in allowed_tenants():
        return False
    owner = _owner(cur, tenant)
    if not owner:
        return False
    cur.execute('''INSERT INTO saas_connect_health_notices(tenant_id,channel_id,incident_id,kind,recipient_id)
      VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s) ON CONFLICT DO NOTHING''',
      (tenant, channel, incident, kind, owner))
    return True


def record(cur, tenant, channel, data, now):
    """Called only after HMAC verification and active tenant/channel validation."""
    cur.execute('''INSERT INTO saas_connect_health(tenant_id,channel_id) VALUES(%s::uuid,%s::uuid)
      ON CONFLICT DO NOTHING''', (tenant, channel))
    cur.execute('SELECT state FROM saas_connect_health WHERE tenant_id=%s::uuid AND channel_id=%s::uuid FOR UPDATE', (tenant, channel))
    state, recovered = observe(cur.fetchone()['state'], data['status'], data['observedAt'], now)
    cur.execute('UPDATE saas_connect_health SET state=%s::jsonb,updated_at=now() WHERE tenant_id=%s::uuid AND channel_id=%s::uuid',
                (json.dumps(state), tenant, channel))
    if recovered:
        _queue(cur, tenant, channel, recovered, 'recovered')


def ingest(raw, signature, event):
    """Own route: no conversation, AI ingestion, message history or replay."""
    from tenant_connect_transport import _connect, _fernet, _verify_signature, ConnectWebhookError
    data = event.get('data') or {}
    try:
        channel = str(data['channelId'])
        from uuid import UUID
        UUID(channel)
        observe({}, data['status'], data['observedAt'], datetime.now(timezone.utc))
        if not event.get('id') or event.get('type') != EVENT:
            raise ValueError('Missing event identity')
    except (ValueError, KeyError, TypeError):
        raise ConnectWebhookError('invalid_event', 'Invalid connection observation.', 400)
    with _connect() as conn:
        # Authentication uses existing integration tables; unauthenticated
        # requests must never trigger this feature's schema preparation.
        with conn.cursor() as cur:
            cur.execute("SELECT tenant_id,secrets FROM saas_tenant_integrations WHERE provider='connect' AND status='connected' AND secrets IS NOT NULL")
            matches = []
            for row in cur.fetchall():
                try:
                    secret = json.loads(_fernet().decrypt(bytes(row['secrets'])).decode()).get('webhook_secret', '')
                except Exception:
                    continue
                if secret and _verify_signature(secret, raw, signature):
                    matches.append(str(row['tenant_id']))
            if len(matches) != 1:
                raise ConnectWebhookError('invalid_signature', 'Invalid Connect signature.', 401)
            tenant = matches[0]
            require_schema(conn)
            # Connect tenant IDs are external identifiers, not CRM tenant UUIDs.
            # Ownership is established by the unique secret AND local mapping.
            cur.execute('SELECT 1 FROM saas_connect_channels WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND active=TRUE FOR SHARE', (tenant, channel))
            if not cur.fetchone():
                return False
            cur.execute('INSERT INTO saas_connect_webhook_events(tenant_id,event_id) VALUES(%s::uuid,%s) ON CONFLICT DO NOTHING RETURNING event_id', (tenant, str(event['id'])))
            if not cur.fetchone():
                return False
            record(cur, tenant, channel, data, datetime.now(timezone.utc))
        conn.commit()
    return True


def allowed_tenants():
    """Explicit rollout scope. Any malformed token invalidates the whole list."""
    raw = os.environ.get('CONNECT_HEALTH_ALERT_TENANT_IDS', '')
    try:
        parts = [part.strip() for part in raw.split(',')]
        if not all(parts):
            return frozenset()
        return frozenset(str(UUID(part)) for part in parts)
    except (ValueError, TypeError, AttributeError):
        return frozenset()


def prepare():
    tenants = allowed_tenants()
    if not tenants:
        return
    from tenant_connect_transport import _connect
    with _connect() as conn:
        require_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT h.* FROM saas_connect_health h JOIN saas_connect_channels c
              ON c.tenant_id=h.tenant_id AND c.connect_channel_id=h.channel_id
              WHERE c.active=TRUE AND h.tenant_id=ANY(%s::uuid[]) AND h.state->>'status' IN ('disconnected','connecting','error')
                AND COALESCE((h.state->>'alertQueued')::boolean,FALSE)=FALSE
              ORDER BY h.updated_at LIMIT 50 FOR UPDATE OF h SKIP LOCKED''', (list(tenants),))
            for row in cur.fetchall():
                state = row['state']
                # Rotate bounded scan fairly even if early rows are not yet due
                # or their owners have not linked Telegram.
                cur.execute('UPDATE saas_connect_health SET updated_at=now() WHERE tenant_id=%s AND channel_id=%s',
                            (row['tenant_id'], row['channel_id']))
                if due(state, datetime.now(timezone.utc)) and _queue(cur, str(row['tenant_id']), str(row['channel_id']), state['incidentId'], 'down'):
                    state['alertQueued'] = True
                    cur.execute('UPDATE saas_connect_health SET state=%s::jsonb,updated_at=now() WHERE tenant_id=%s AND channel_id=%s',
                                (json.dumps(state), row['tenant_id'], row['channel_id']))
        conn.commit()


def claim():
    from tenant_connect_transport import _connect
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE saas_connect_health_notices SET status='unknown',updated_at=now() WHERE status='sending' AND updated_at<now()-interval '5 minutes'")
            cur.execute('''SELECT n.*, h.state, c.active, c.name AS channel_name, t.name AS tenant_name
              FROM saas_connect_health_notices n JOIN saas_connect_health h USING(tenant_id,channel_id)
              JOIN saas_connect_channels c ON c.tenant_id=n.tenant_id AND c.connect_channel_id=n.channel_id
              JOIN saas_tenants t ON t.id=n.tenant_id
              WHERE n.status='pending' AND (n.kind='down' OR EXISTS (
                SELECT 1 FROM saas_connect_health_notices d WHERE d.tenant_id=n.tenant_id
                  AND d.channel_id=n.channel_id AND d.incident_id=n.incident_id
                  AND d.kind='down' AND d.status NOT IN ('pending','sending')))
              ORDER BY n.updated_at LIMIT 1 FOR UPDATE OF n,h SKIP LOCKED''')
            item = cur.fetchone()
            if not item:
                conn.commit()
                return None
            allowed = (str(item['tenant_id']) in allowed_tenants() and item['active']
                       and _owner(cur, str(item['tenant_id'])) == item['recipient_id'])
            state = item['state']
            if item['kind'] == 'down':
                allowed = allowed and state.get('status') in BAD and state.get('incidentId') == str(item['incident_id'])
                if allowed and not debounce_elapsed(state, datetime.now(timezone.utc)):
                    # Do not skip a valid pending incident or bypass its new
                    # grace period. Rotate it so another ready notice can run.
                    cur.execute('UPDATE saas_connect_health_notices SET updated_at=now() WHERE tenant_id=%s AND channel_id=%s AND incident_id=%s AND kind=%s', key(item))
                    item['send'] = False
                    item['deferred'] = True
                    conn.commit()
                    return item
                if allowed:
                    state['alertAttempted'] = True
                    cur.execute('UPDATE saas_connect_health SET state=%s::jsonb,updated_at=now() WHERE tenant_id=%s AND channel_id=%s',
                                (json.dumps(state), item['tenant_id'], item['channel_id']))
            else:
                cur.execute('''SELECT status FROM saas_connect_health_notices WHERE tenant_id=%s AND channel_id=%s
                  AND incident_id=%s AND kind='down' ''', (item['tenant_id'], item['channel_id'], item['incident_id']))
                down = cur.fetchone()
                allowed = (allowed and state.get('status') == 'connected'
                           and state.get('recoveredIncidentId') == str(item['incident_id'])
                           and down and down['status'] == 'delivered')
            item['send'] = bool(allowed)
            cur.execute('''UPDATE saas_connect_health_notices SET status=%s,updated_at=now()
              WHERE tenant_id=%s AND channel_id=%s AND incident_id=%s AND kind=%s''',
              ('sending' if allowed else 'skipped', *key(item)))
        conn.commit()
    return item


def key(item):
    return tuple(item[k] for k in ('tenant_id','channel_id','incident_id','kind'))


def finish(item, result=None, *, skipped=False):
    from tenant_connect_transport import _connect
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute('''UPDATE saas_connect_health_notices SET status=%s,message_id=%s,updated_at=now()
              WHERE tenant_id=%s AND channel_id=%s AND incident_id=%s AND kind=%s AND status='sending' ''',
              ('skipped' if skipped else 'delivered' if result.status == 'delivered' else 'unknown',
               result.message_id if result is not None else None, *key(item)))
        conn.commit()


def notice(item):
    title = ('WhatsApp bağlantısı bərpa olunduğunu bildirir. Mesajların çatdırılması ayrıca yoxlanmalıdır.'
             if item['kind'] == 'recovered' else
             'Təcili: WhatsApp bağlantısında problem var. Yeni mesajlar CRM-ə çatmaya bilər. Bağlantını yoxlayın.')
    return '\n\n'.join((title, 'Şirkət: ' + str(item['tenant_name'])[:160],
                         'Kanal: ' + str(item['channel_name'] or item['channel_id'])[:160],
                         'https://crm.pro.az/app'))


async def deliver(bot, logger):
    # Explicit rollout gate. Observation ingestion is safe while delivery is off.
    if os.environ.get('CONNECT_HEALTH_ALERTS_ENABLED', '').lower() != 'true' or not allowed_tenants():
        return
    from tenant_notice_transport import attempt
    await asyncio.to_thread(prepare)
    for _ in range(10):
        item = await asyncio.to_thread(claim)
        if item is None:
            break
        if not item['send']:
            continue
        # Re-read rollout authorization immediately before the external send,
        # not only when the pending notice was prepared or claimed.
        if str(item['tenant_id']) not in allowed_tenants() or os.environ.get('CONNECT_HEALTH_ALERTS_ENABLED', '').lower() != 'true':
            await asyncio.to_thread(finish, item, skipped=True)
            continue
        result = await attempt(item, 'telegram', bot, text=notice, parts=lambda text: [text])
        await asyncio.to_thread(finish, item, result)
        if result.status != 'delivered':
            logger.warning('Connect health notice outcome unknown; no automatic replay')
