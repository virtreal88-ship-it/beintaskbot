"""One speaking authority per verified WhatsApp group; channel history stays separate.

No default assignment, fuzzy identity, automatic Resume, or expert send capability.
All mutations and final sends use the group advisory lock before channel locks.
"""
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone

from tenant_linear_policy import TenantLinearError
from tenant_platform import _connect


def group_jid(value):
    return str(value) if re.fullmatch(r'[0-9]{5,22}(?:-[0-9]{5,22})?@g\.us', str(value or '')) else None


def create_schema(conn):
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_group_coordination(
          tenant_id UUID NOT NULL REFERENCES saas_tenants(id),provider TEXT NOT NULL DEFAULT 'connect' CHECK(provider='connect'),
          group_jid TEXT NOT NULL,revision BIGINT NOT NULL DEFAULT 1,
          state TEXT NOT NULL DEFAULT 'paused' CHECK(state IN ('active','human','paused')),
          reason TEXT NOT NULL DEFAULT 'group_assignment_required',assignments JSONB NOT NULL DEFAULT '[]',
          assigned_to BIGINT,updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),resumed_at TIMESTAMPTZ,
          PRIMARY KEY(tenant_id,provider,group_jid))''')
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_group_channel_legacy_permits(
          tenant_id UUID NOT NULL,provider TEXT NOT NULL DEFAULT 'connect' CHECK(provider='connect'),
          group_jid TEXT NOT NULL,channel_id UUID NOT NULL,agent_id UUID NOT NULL,
          legacy_revision BIGINT NOT NULL,preview_hash TEXT NOT NULL,
          granted_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),actor BIGINT NOT NULL,
          PRIMARY KEY(tenant_id,provider,group_jid,channel_id),
          FOREIGN KEY(tenant_id,provider,group_jid)
            REFERENCES saas_ai_group_coordination(tenant_id,provider,group_jid) ON DELETE CASCADE,
          FOREIGN KEY(tenant_id,agent_id) REFERENCES saas_ai_agents(tenant_id,id))''')
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_group_events(
          tenant_id UUID NOT NULL,provider TEXT NOT NULL DEFAULT 'connect',group_jid TEXT NOT NULL,event_key TEXT NOT NULL,
          channel_id TEXT NOT NULL,revision BIGINT NOT NULL,claimed BOOLEAN NOT NULL DEFAULT FALSE,created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          PRIMARY KEY(tenant_id,provider,group_jid,event_key),
          FOREIGN KEY(tenant_id,provider,group_jid) REFERENCES saas_ai_group_coordination(tenant_id,provider,group_jid))''')
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_group_deliveries(
          tenant_id UUID NOT NULL,provider TEXT NOT NULL DEFAULT 'connect',group_jid TEXT NOT NULL,event_key TEXT NOT NULL,
          run_id TEXT NOT NULL,revision BIGINT NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,provider,group_jid,event_key),
          FOREIGN KEY(tenant_id,provider,group_jid) REFERENCES saas_ai_group_coordination(tenant_id,provider,group_jid))''')
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_group_staff(
          tenant_id UUID NOT NULL,channel_id UUID NOT NULL,jid TEXT NOT NULL,member_id BIGINT NOT NULL,
          verified_by BIGINT NOT NULL,verified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY(tenant_id,channel_id,jid),
          FOREIGN KEY(tenant_id,member_id) REFERENCES saas_tenant_members(tenant_id,telegram_id))''')
        # A group permit is deliberately narrower than the ordinary channel-local
        # continuation permit: it is valid only while this exact group binding
        # (channel + primary agent) remains configured.
        from tenant_ai_legacy_permit import group_function
        group_function(cur)


def lock_group(cur, tenant, conversation):
    if group_jid(conversation):
        cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                    ('ai-group:' + json.dumps([str(tenant), 'connect', conversation]),))


def read(cur, tenant, conversation):
    cur.execute("SELECT * FROM saas_ai_group_coordination WHERE tenant_id=%s::uuid AND provider='connect' AND group_jid=%s", (tenant, conversation))
    return dict(cur.fetchone() or {})


def _legacy_work_items(cur, tenant, conversation, selected_channel):
    """Return bounded work metadata and cancelability evidence, never content."""
    cur.execute('''SELECT 'job'::text AS item_type,j.event_fingerprint AS job_id,j.agent_id::text AS agent_id,
        j.channel_id,j.lead_id,j.state AS job_state,e.received_at AS created_at,j.updated_at,j.due_at,
        j.attempts,j.error_code,j.lease_until,j.lease_token IS NOT NULL AS has_lease,
        e.state AS event_state,e.event_type,e.received_at,e.attempts AS event_attempts,
        e.lease_until AS event_lease_until,e.lease_token IS NOT NULL AS event_has_lease,
        CASE WHEN e.payload->>'message_type' IN ('text','image','audio','video','document')
          THEN e.payload->>'message_type' ELSE 'other' END AS message_type,
        e.error_code AS event_error_code,
        r.id::text AS run_id,r.state AS run_state,r.model,r.created_at AS run_created_at,
        d.id::text AS draft_id,d.state AS draft_state,d.created_at AS draft_created_at,
        (j.channel_id=%s OR e.payload->>'channel_id'=%s
          OR coalesce(e.payload->>'channel_id','')='') AS relevant_to_selection,
        (j.state='queued' AND j.attempts=0 AND j.lease_token IS NULL AND j.lease_until IS NULL
          AND e.state='queued' AND e.attempts=0 AND e.lease_token IS NULL AND e.lease_until IS NULL
          AND r.id IS NULL AND d.id IS NULL
          AND e.event_type='incoming_message'
          AND e.payload->>'conversation_id'=%s
          AND e.payload->>'channel_id'=%s
          AND NOT EXISTS(SELECT 1 FROM saas_ai_agent_jobs other
            WHERE other.tenant_id=j.tenant_id AND other.provider='connect'
              AND other.event_fingerprint=j.event_fingerprint
              AND other.state IN ('queued','processing','drafted','unknown')
              AND (other.agent_id<>j.agent_id OR other.conversation_id<>j.conversation_id
                OR other.scope_channel_id<>j.scope_channel_id))
          AND NOT EXISTS(SELECT 1 FROM saas_ai_agent_runs active_run
            WHERE active_run.tenant_id=j.tenant_id AND active_run.event_fingerprint=j.event_fingerprint
              AND (active_run.state IN ('generating','unknown')
                OR active_run.result->>'send_state' IN ('reserved','unknown')))
          AND NOT EXISTS(SELECT 1 FROM saas_ai_agent_drafts active_draft
            WHERE active_draft.tenant_id=j.tenant_id AND active_draft.source_fingerprint=j.event_fingerprint
              AND active_draft.state IN ('pending','sending','unknown'))
          AND (j.channel_id=e.payload->>'channel_id' OR j.channel_id='')) AS safely_stoppable
      FROM saas_ai_agent_jobs j
      JOIN saas_tenant_webhook_events e ON e.tenant_id=j.tenant_id AND e.fingerprint=j.event_fingerprint
        AND e.provider='connect'
      LEFT JOIN LATERAL (SELECT r0.id,r0.state,r0.model,r0.result,r0.created_at FROM saas_ai_agent_runs r0
        WHERE r0.tenant_id=j.tenant_id AND r0.agent_id=j.agent_id AND r0.event_fingerprint=j.event_fingerprint
        ORDER BY r0.created_at DESC,r0.id DESC LIMIT 1) r ON TRUE
      LEFT JOIN LATERAL (SELECT d0.id,d0.state,d0.created_at FROM saas_ai_agent_drafts d0
        WHERE d0.tenant_id=j.tenant_id AND d0.agent_id=j.agent_id AND d0.source_fingerprint=j.event_fingerprint
        ORDER BY d0.created_at DESC,d0.id DESC LIMIT 1) d ON TRUE
      WHERE j.tenant_id=%s::uuid AND j.provider='connect' AND j.conversation_id=%s
        AND (j.state IN ('queued','processing','drafted','unknown') OR r.state IN ('generating','unknown')
          OR r.result->>'send_state' IN ('reserved','unknown') OR d.state IN ('pending','sending','unknown'))
      ORDER BY j.updated_at DESC,j.event_fingerprint LIMIT 100''',
      (selected_channel,selected_channel,conversation,selected_channel,tenant,conversation))
    items = [dict(row) for row in cur.fetchall()]
    cur.execute('''SELECT 'run'::text AS item_type,r.id::text AS item_id,r.event_fingerprint AS job_id,r.agent_id::text AS agent_id,
        e.payload->>'channel_id' AS channel_id,e.event_type,
        CASE WHEN e.payload->>'message_type' IN ('text','image','audio','video','document')
          THEN e.payload->>'message_type' ELSE 'other' END AS message_type,
        e.error_code AS event_error_code,e.attempts AS event_attempts,
        e.lease_until AS event_lease_until,e.lease_token IS NOT NULL AS event_has_lease,
        (e.payload->>'channel_id'=%s OR coalesce(e.payload->>'channel_id','')='') AS relevant_to_selection,
        r.state AS run_state,r.model,r.created_at,e.state AS event_state,
        NULL::text AS job_state,NULL::integer AS attempts,NULL::timestamptz AS due_at,
        NULL::timestamptz AS lease_until,NULL::text AS draft_state,
        FALSE AS safely_stoppable
      FROM saas_ai_agent_runs r JOIN saas_tenant_webhook_events e
        ON e.tenant_id=r.tenant_id AND e.fingerprint=r.event_fingerprint AND e.provider='connect'
      WHERE r.tenant_id=%s::uuid AND e.payload->>'conversation_id'=%s
        AND (r.state IN ('generating','unknown') OR r.result->>'send_state' IN ('reserved','unknown'))
        AND NOT EXISTS(SELECT 1 FROM saas_ai_agent_jobs j WHERE j.tenant_id=r.tenant_id
          AND j.agent_id=r.agent_id AND j.event_fingerprint=r.event_fingerprint AND j.conversation_id=%s)
      ORDER BY r.created_at DESC,r.id DESC LIMIT 100''', (selected_channel,tenant,conversation,conversation))
    items.extend(dict(row) for row in cur.fetchall())
    cur.execute('''SELECT 'draft'::text AS item_type,d.id::text AS item_id,d.source_fingerprint AS job_id,d.agent_id::text AS agent_id,
        e.payload->>'channel_id' AS channel_id,e.event_type,
        CASE WHEN e.payload->>'message_type' IN ('text','image','audio','video','document')
          THEN e.payload->>'message_type' ELSE 'other' END AS message_type,
        e.error_code AS event_error_code,e.attempts AS event_attempts,
        e.lease_until AS event_lease_until,e.lease_token IS NOT NULL AS event_has_lease,
        (e.payload->>'channel_id'=%s OR coalesce(e.payload->>'channel_id','')='') AS relevant_to_selection,
        r.state AS run_state,r.model,d.created_at,e.state AS event_state,
        NULL::text AS job_state,NULL::integer AS attempts,NULL::timestamptz AS due_at,
        NULL::timestamptz AS lease_until,d.state AS draft_state,
        FALSE AS safely_stoppable
      FROM saas_ai_agent_drafts d JOIN saas_tenant_webhook_events e
        ON e.tenant_id=d.tenant_id AND e.fingerprint=d.source_fingerprint AND e.provider='connect'
      LEFT JOIN LATERAL (SELECT r0.state,r0.model FROM saas_ai_agent_runs r0
        WHERE r0.tenant_id=d.tenant_id AND r0.id=d.run_id LIMIT 1) r ON TRUE
      WHERE d.tenant_id=%s::uuid AND e.payload->>'conversation_id'=%s
        AND d.state IN ('pending','sending','unknown')
        AND NOT EXISTS(SELECT 1 FROM saas_ai_agent_jobs j WHERE j.tenant_id=d.tenant_id
          AND j.agent_id=d.agent_id AND j.event_fingerprint=d.source_fingerprint AND j.conversation_id=%s)
      ORDER BY d.created_at DESC,d.id DESC LIMIT 100''', (selected_channel,tenant,conversation,conversation))
    items.extend(dict(row) for row in cur.fetchall())
    cur.execute('''SELECT 'event'::text AS item_type,e.fingerprint AS item_id,e.fingerprint AS job_id,
        e.payload->>'channel_id' AS channel_id,e.event_type,e.state AS event_state,
        e.error_code AS event_error_code,e.attempts AS event_attempts,
        e.lease_until AS event_lease_until,e.lease_token IS NOT NULL AS event_has_lease,
        e.received_at AS created_at,
        CASE WHEN e.payload->>'message_type' IN ('text','image','audio','video','document')
          THEN e.payload->>'message_type' ELSE 'other' END AS message_type,
        (e.payload->>'channel_id'=%s OR coalesce(e.payload->>'channel_id','')='') AS relevant_to_selection,
        (e.state='queued' AND e.attempts=0 AND e.lease_token IS NULL AND e.lease_until IS NULL
          AND e.event_type='incoming_message'
          AND e.payload->>'channel_id'=%s) AS safely_stoppable
      FROM saas_tenant_webhook_events e
      WHERE e.tenant_id=%s::uuid AND e.provider='connect'
        AND e.payload->>'conversation_id'=%s AND e.state IN ('queued','processing')
        AND NOT EXISTS(SELECT 1 FROM saas_ai_agent_jobs j WHERE j.tenant_id=e.tenant_id
          AND j.event_fingerprint=e.fingerprint AND j.provider='connect')
        AND NOT EXISTS(SELECT 1 FROM saas_ai_agent_runs r WHERE r.tenant_id=e.tenant_id
          AND r.event_fingerprint=e.fingerprint)
        AND NOT EXISTS(SELECT 1 FROM saas_ai_agent_drafts d WHERE d.tenant_id=e.tenant_id
          AND d.source_fingerprint=e.fingerprint)
      ORDER BY e.received_at DESC,e.fingerprint LIMIT 100''',
      (selected_channel,selected_channel,tenant,conversation))
    items.extend(dict(row) for row in cur.fetchall())
    # A capped inventory must fail closed; never assume omitted rows are safe.
    truncated = len(items) >= 100
    if truncated:
        items = items[:100]
    # Serialize datetimes before exposing a preview through JSON.
    for row in items:
        for key, value in list(row.items()):
            if isinstance(value, datetime):
                row[key] = value.isoformat()
    safely_stoppable = [row for row in items if row.get('safely_stoppable') is True]
    blocked = [row for row in items if row.get('relevant_to_selection') is True
               and row.get('safely_stoppable') is not True]
    if truncated:
        blocked.append({'item_id':'inventory_truncated','job_state':'unknown','safely_stoppable':False})
    return items, safely_stoppable, blocked, truncated


def _legacy_resolution_snapshot(cur, tenant, actor, conversation, channel, lead, agent_id):
    import hashlib
    from tenant_ai_agent_connect import scope_matches

    cur.execute('''SELECT s.channel_id,s.state,s.reason,s.revision,s.agent_id,s.agent_assignment_id
      FROM saas_ai_conversation_state s WHERE s.tenant_id=%s::uuid AND s.provider='connect'
        AND s.conversation_id=%s ORDER BY s.channel_id''', (tenant, conversation))
    states = [dict(row) for row in cur.fetchall()]
    legacy = next((row for row in states if row.get('channel_id') == ''), None)
    cur.execute('''SELECT c.connect_channel_id::text AS channel_id,c.lead_id,c.conversation_kind,ch.name,ch.active,
        count(m.id)::integer AS message_count,
        count(m.id) FILTER (WHERE m.direction='incoming')::integer AS incoming_count,
        count(m.id) FILTER (WHERE m.direction='outgoing')::integer AS outgoing_count
      FROM saas_connect_conversations c JOIN saas_connect_channels ch
        ON ch.tenant_id=c.tenant_id AND ch.connect_channel_id=c.connect_channel_id
      LEFT JOIN saas_crm_messages m ON m.tenant_id=c.tenant_id AND m.kommo_lead_id=c.lead_id
        AND m.channel='connect:'||c.connect_channel_id::text
      WHERE c.tenant_id=%s::uuid AND c.conversation_id=%s
      GROUP BY c.connect_channel_id,c.lead_id,c.conversation_kind,ch.name,ch.active ORDER BY c.connect_channel_id''',
      (tenant, conversation))
    routes = [dict(row) for row in cur.fetchall()]
    cur.execute('''SELECT state,count(*)::integer AS count FROM saas_ai_agent_jobs
      WHERE tenant_id=%s::uuid AND provider='connect' AND conversation_id=%s
      GROUP BY state ORDER BY state''', (tenant, conversation))
    jobs = [dict(row) for row in cur.fetchall()]
    cur.execute('''SELECT d.state,count(*)::integer AS count FROM saas_ai_agent_drafts d
      JOIN saas_tenant_webhook_events e ON e.tenant_id=d.tenant_id AND e.fingerprint=d.source_fingerprint
      WHERE d.tenant_id=%s::uuid AND e.provider='connect' AND d.chat_id=%s
      GROUP BY d.state ORDER BY d.state''', (tenant, conversation))
    drafts = [dict(row) for row in cur.fetchall()]
    cur.execute('''SELECT count(*)::integer AS count FROM saas_ai_agent_runs r
      JOIN saas_tenant_webhook_events e ON e.tenant_id=r.tenant_id AND e.fingerprint=r.event_fingerprint
      WHERE r.tenant_id=%s::uuid AND e.provider='connect' AND e.payload->>'conversation_id'=%s
        AND (r.state IN ('generating','unknown') OR r.result->>'send_state' IN ('reserved','unknown'))''',
      (tenant, conversation))
    runs = int((cur.fetchone() or {}).get('count') or 0)
    cur.execute('''SELECT count(*)::integer AS count FROM saas_connect_dialog_links
      WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND source_id=%s AND active''',
      (tenant, channel, conversation))
    active_links = int((cur.fetchone() or {}).get('count') or 0)
    work_items, safely_stoppable, blocked_work, inventory_truncated = _legacy_work_items(
      cur,tenant,conversation,channel)
    cur.execute('''SELECT id,name,role,enabled,mode,version,scopes FROM saas_ai_agents
      WHERE tenant_id=%s::uuid AND id=%s::uuid''', (tenant, agent_id))
    agent = dict(cur.fetchone() or {})
    group_state = read(cur, tenant, conversation)
    selected_route = next((row for row in routes
      if str(row['channel_id']) == channel and int(row['lead_id']) == lead), None)
    scope_ok = bool(agent and agent.get('enabled') is True and agent.get('role') == 'primary'
      and scope_matches(agent.get('scopes'), channel, is_group=True))
    reason = ('active_source_link' if active_links else
      'no_legacy_state' if not legacy else
      'selected_route_changed' if not selected_route else
      'selected_route_changed' if selected_route.get('conversation_kind') != 'group' else
      'selected_channel_inactive' if selected_route.get('active') is not True else
      'selected_route_has_channel_state' if any(row.get('channel_id') == channel for row in states) else
      'group_not_paused_or_already_assigned' if group_state.get('state') == 'active' else
      'outstanding_work' if blocked_work else
      'agent_not_group_scoped' if not scope_ok else '')
    evidence = {
      'legacy_state': {'revision': int(legacy['revision']), 'state': legacy['state'],
        'agent_id': str(legacy.get('agent_id') or '')} if legacy else None,
      'routes': routes, 'states': states, 'jobs': jobs, 'drafts': drafts,
      'runs': runs, 'selected_channel_active_links': active_links,
      'group_revision': int(group_state.get('revision') or 0),
      'group_state': group_state.get('state', 'paused'),
      'group_assignments': group_state.get('assignments') or [],
      'work_items': work_items,
      'safely_stoppable_job_ids': sorted({str(row['job_id']) for row in safely_stoppable if row.get('job_id')}),
      'agent': {key: agent.get(key) for key in ('id','role','enabled','version','scopes')} if agent else None,
      'selection': {'tenant_id': str(tenant), 'actor': int(actor), 'conversation_id': conversation,
        'channel_id': channel,
        'lead_id': lead, 'agent_id': agent_id},
    }
    token = hashlib.sha256(json.dumps(evidence, default=str, sort_keys=True).encode()).hexdigest()
    return {'reason': reason, 'token': token, 'legacy': legacy, 'routes': routes,
      'group_state': group_state, 'agent': agent, 'evidence': evidence,
      'active_links': active_links, 'jobs': jobs, 'drafts': drafts, 'runs': runs,
      'work_items':work_items,'safely_stoppable':safely_stoppable,'blocked_work':blocked_work,
      'inventory_truncated':inventory_truncated}


def legacy_resolution_preview(profile, data):
    """Read-only owner preview for one exact group/channel/agent selection."""
    from tenant_ai_agents_schema import ensure_schema
    from tenant_ai_dialog_scope import channel_scope
    from tenant_operator_access import assert_route
    from tenant_operator_legacy import admin

    conversation = group_jid(data.get('conversation_id'))
    if not conversation:
        raise TenantLinearError('Укажите проверенный групповой диалог.', 400)
    channel = channel_scope('connect', data.get('channel_id'))
    try:
        lead = int(data.get('lead_id'))
        agent_id = str(uuid.UUID(str(data.get('agent_id'))))
    except (ValueError, TypeError, AttributeError):
        raise TenantLinearError('Выберите точный канал и основного агента.', 400) from None
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, actor, policy = admin(cur, profile)
            route = assert_route(cur, tenant, policy, 'connect', conversation, channel, lead)
            if route.get('conversation_kind') != 'group':
                raise TenantLinearError('Выбранный диалог не подтверждён как группа.', 409)
            snap = _legacy_resolution_snapshot(cur, tenant, actor, conversation, channel, lead, agent_id)
            return {'allowed': not snap['reason'], 'reason': snap['reason'],
              'requires_resolution': bool(snap['legacy'] or snap['active_links']),
              'expected_token': snap['token'],
              'expected_revision': int(snap['group_state'].get('revision') or 0),
              'legacy_revision': int(snap['legacy']['revision']) if snap['legacy'] else None,
              'agent_version': int(snap['agent']['version']) if snap['agent'] else None,
              'channel_id': channel, 'lead_id': lead, 'agent_id': agent_id,
              'work_items':snap['work_items'],
              'safely_stoppable_queued_count':len(snap['safely_stoppable']),
              'blocked_work_count':len(snap['blocked_work']),
              'work_record_count':len(snap['work_items']),
              'work_inventory_truncated':snap['inventory_truncated'],
              'channels': [{'channel_id': str(row['channel_id']), 'lead_id': int(row['lead_id']),
                'name': str(row.get('name') or ''), 'active': row.get('active') is True,
                'message_count': int(row.get('message_count') or 0),
                'incoming_count': int(row.get('incoming_count') or 0),
                'outgoing_count': int(row.get('outgoing_count') or 0)} for row in snap['routes']],
              'evidence': {'route_count': len(snap['routes']),
                'message_count': sum(int(row.get('message_count') or 0) for row in snap['routes']),
                'job_counts': snap['jobs'], 'draft_counts': snap['drafts'],
                'unknown_run_count': snap['runs'], 'active_source_links': snap['active_links'],
                'legacy_state': snap['legacy']['state'] if snap['legacy'] else None,
                'group_state': snap['group_state'].get('state', 'paused')},
              'current_assignments':[{'agent_id':str(row.get('agent_id') or ''),
                'channel_id':str(row.get('channel_id') or '')}
                for row in (snap['group_state'].get('assignments') or [])]}


def choose(assignments, agents, *, now=None):
    """Return the sole valid primary binding; legacy multi-slot state is ambiguous."""
    from tenant_ai_agent_connect import scope_matches
    if not isinstance(assignments, list) or len(assignments) != 1:
        return None
    assignment = assignments[0]
    agent = agents.get(str(assignment.get('agent_id')))
    if (not agent or not agent.get('enabled') or agent.get('role', 'primary') != 'primary'
            or not scope_matches(agent.get('scopes'), assignment.get('channel_id'), is_group=True)):
        return None
    return assignment


def authority(cur, tenant, channel, conversation, agent_id=None, expected_revision=None, *, allow_stopped=False):
    if not group_jid(conversation):
        return None
    state = read(cur, tenant, conversation)
    if not state or (not allow_stopped and state['state'] != 'active'):
        return None
    if expected_revision is not None and int(state['revision']) != int(expected_revision):
        return None
    cur.execute("""SELECT ai_legacy_blocked(%s::uuid,%s,%s,NULL) AS legacy_blocked,
      EXISTS(SELECT 1 FROM saas_connect_dialog_links WHERE tenant_id=%s::uuid
        AND channel_id=%s::uuid AND source_id=%s AND active) AS active_link""",
      (tenant,channel,conversation,tenant,channel,conversation))
    quarantine=cur.fetchone() or {}
    if quarantine.get('legacy_blocked') or quarantine.get('active_link'):
        return None
    cur.execute('SELECT * FROM saas_ai_agents WHERE tenant_id=%s::uuid', (tenant,))
    winner = choose(state['assignments'], {str(a['id']): dict(a) for a in cur.fetchall()})
    if not winner or (channel and str(channel) != winner['channel_id']) or (agent_id and str(agent_id) != winner['agent_id']):
        return None
    cur.execute('''SELECT 1 FROM saas_connect_conversations c JOIN saas_connect_channels ch
      ON ch.tenant_id=c.tenant_id AND ch.connect_channel_id=c.connect_channel_id AND ch.active=TRUE
      WHERE c.tenant_id=%s::uuid AND c.connect_channel_id=%s::uuid AND c.conversation_id=%s''',
      (tenant, winner['channel_id'], conversation))
    if not cur.fetchone():
        return None
    if not allow_stopped:
        cur.execute("SELECT state FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id=%s",(tenant,winner['channel_id'],conversation))
        if (cur.fetchone() or {}).get('state') in ('human','paused'):
            return None
    return {**state, 'primary_agent_id': winner['agent_id'], 'sending_channel_id': winner['channel_id']}


def validate_authority(tenant, channel, conversation, agent_id, expected_revision):
    with _connect() as conn:
        with conn.cursor() as cur:
            return authority(cur, tenant, channel, conversation, agent_id, expected_revision)


def event_key(external_id):
    # Only provider message ID from the signed Connect event, never text/time.
    if not isinstance(external_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{5,200}', external_id):
        return None
    return hashlib.sha256(external_id.encode()).hexdigest()


def admit(cur, tenant, channel, conversation, external_id, source_message_id=None):
    key = event_key(external_id)
    if not key:
        return None
    cur.execute("INSERT INTO saas_ai_group_coordination(tenant_id,group_jid) VALUES(%s::uuid,%s) ON CONFLICT DO NOTHING", (tenant,conversation))
    cur.execute('''INSERT INTO saas_ai_group_events(tenant_id,group_jid,event_key,channel_id,revision)
      VALUES(%s::uuid,%s,%s,%s,0) ON CONFLICT DO NOTHING''', (tenant,conversation,key,channel))
    state = authority(cur, tenant, channel, conversation)
    if not state:
        return None
    if source_message_id and state.get('resumed_at'):
        cur.execute('''SELECT 1 FROM saas_crm_messages WHERE tenant_id=%s::uuid AND channel=%s AND external_id=%s
          AND (created_at<=%s::timestamptz OR happened_at<=%s::timestamptz)''',
          (tenant,'connect:'+channel,'connect:'+str(source_message_id),state['resumed_at'],state['resumed_at']))
        if cur.fetchone():return None
    cur.execute('''UPDATE saas_ai_group_events SET claimed=TRUE,channel_id=%s,revision=%s
      WHERE tenant_id=%s::uuid AND provider='connect' AND group_jid=%s AND event_key=%s
        AND claimed=FALSE AND (%s::timestamptz IS NULL OR created_at>%s::timestamptz) RETURNING event_key''',
      (channel,state['revision'],tenant,conversation,key,state.get('resumed_at'),state.get('resumed_at')))
    if not cur.fetchone():
        return None
    return {'group_revision': state['revision'], 'group_event_key': key, 'primary_agent_id': state['primary_agent_id']}


def claim_control_event(cur,tenant,channel,conversation,external_id):
    """Deduplicate a control fact across every channel and later Resume."""
    key=event_key(external_id)
    if not key:return False
    cur.execute("INSERT INTO saas_ai_group_coordination(tenant_id,group_jid) VALUES(%s::uuid,%s) ON CONFLICT DO NOTHING",(tenant,conversation))
    cur.execute("INSERT INTO saas_ai_group_events(tenant_id,group_jid,event_key,channel_id,revision,claimed) VALUES(%s::uuid,%s,%s,%s,0,TRUE) ON CONFLICT DO NOTHING RETURNING event_key",(tenant,conversation,key,channel))
    return bool(cur.fetchone())


def stop(cur, tenant, conversation, state='human', reason='manager_replied', actor=None):
    if not group_jid(conversation):
        return
    lock_group(cur, tenant, conversation)
    cur.execute('''INSERT INTO saas_ai_group_coordination(tenant_id,group_jid,state,reason,assigned_to)
      VALUES(%s::uuid,%s,%s,%s,%s) ON CONFLICT(tenant_id,provider,group_jid) DO UPDATE
      SET state=EXCLUDED.state,reason=EXCLUDED.reason,assigned_to=EXCLUDED.assigned_to,
      revision=saas_ai_group_coordination.revision+1,updated_at=now()''', (tenant, conversation, state, reason, actor))
    cur.execute("UPDATE saas_ai_agent_jobs SET state='skipped',revision=revision+1,lease_token=NULL,lease_until=NULL,error_code=%s,updated_at=now() WHERE tenant_id=%s::uuid AND provider='connect' AND conversation_id=%s AND state IN ('queued','processing')", (reason, tenant, conversation))


def job_allowed(cur, job):
    if job.get('provider') != 'connect' or not group_jid(job.get('conversation_id')):
        return True
    payload = job.get('payload') or {}
    # Old jobs and old drafts carry no coordination fence: never grandfather.
    if not payload.get('group_event_key') or not payload.get('group_revision'):
        return False
    if payload.get('channel_id') != job['channel_id'] or payload.get('conversation_id') != job['conversation_id']:
        return False
    cur.execute("SELECT 1 FROM saas_ai_group_events WHERE tenant_id=%s::uuid AND provider='connect' AND group_jid=%s AND event_key=%s AND revision=%s AND channel_id=%s AND claimed=TRUE", (job['tenant_id'],job['conversation_id'],payload['group_event_key'],payload['group_revision'],job['channel_id']))
    if not cur.fetchone():
        return False
    return bool(authority(cur, job['tenant_id'], job['channel_id'], job['conversation_id'],
                          job.get('agent_id'), payload['group_revision']))


def delivery_source(cur, tenant, run_id):
    cur.execute('''SELECT r.agent_id,r.event_fingerprint,e.payload,'run' AS source_kind FROM saas_ai_agent_runs r
      JOIN saas_tenant_webhook_events e ON e.tenant_id=r.tenant_id AND e.fingerprint=r.event_fingerprint
      WHERE r.tenant_id=%s::uuid AND r.id=%s::uuid
      UNION ALL SELECT d.agent_id,d.source_fingerprint AS event_fingerprint,e.payload,'draft' AS source_kind FROM saas_ai_agent_drafts d
      JOIN saas_tenant_webhook_events e ON e.tenant_id=d.tenant_id AND e.fingerprint=d.source_fingerprint
      WHERE d.tenant_id=%s::uuid AND d.id=%s::uuid''', (tenant, run_id, tenant, run_id))
    rows = cur.fetchall()
    return dict(rows[0]) if len(rows) == 1 else None


def reserve_delivery(tenant, channel, conversation, run_id):
    """Commit a permanent no-retry reservation before network I/O.

    Unknown outcome stays reserved. Losing a response is safer than duplicate
    delivery; operators can inspect it. Final locked check still runs afterward.
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            lock_group(cur, tenant, conversation)
            source = delivery_source(cur, tenant, run_id)
            if not source or not job_allowed(cur, {**source, 'tenant_id':tenant, 'provider':'connect',
                                                   'channel_id':channel, 'conversation_id':conversation}):
                return False
            payload = source['payload']
            cur.execute('''INSERT INTO saas_ai_group_deliveries(tenant_id,group_jid,event_key,run_id,revision)
              VALUES(%s::uuid,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING run_id''',
              (tenant, conversation, payload['group_event_key'], str(run_id), payload['group_revision']))
            return bool(cur.fetchone())


def configure(profile, data):
    from tenant_ai_agents_store import _authorize, _audit
    from tenant_ai_agents_schema import ensure_schema
    from tenant_ai_dialog_scope import channel_scope, assert_resolved
    from tenant_ai_agent_connect import scope_matches
    from tenant_platform import _json
    from tenant_linear_policy import identifier
    conversation = group_jid(data.get('conversation_id'))
    slots = data.get('assignments')
    resolution = data.get('legacy_resolution')
    if not conversation or not isinstance(slots, list) or len(slots) > 1:
        raise TenantLinearError('Укажите проверенную группу и не более одного основного агента.', 400)
    if type(data.get('expected_revision')) is not int or data['expected_revision']<0:
        raise TenantLinearError('Требуется актуальная версия состояния.',400)
    normalized = []
    for slot in slots:
        if not isinstance(slot,dict):
            raise TenantLinearError('Некорректное назначение.',400)
        normalized.append({'agent_id':identifier(slot.get('agent_id')), 'channel_id':channel_scope('connect',slot.get('channel_id')), 'priority':0})
    with _connect() as conn:
        ensure_schema(conn)
        if resolution is not None:
            # Schema setup is complete; start a bounded serializable transaction
            # for the owner-confirmed resolution snapshot/write.
            conn.commit()
            with conn.cursor() as cur:
                cur.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE')
                cur.execute("SET LOCAL lock_timeout = '1500ms'")
                cur.execute("SET LOCAL statement_timeout = '5000ms'")
        with conn.cursor() as cur:
            tenant, actor = _authorize(cur, profile)
            lock_group(cur, tenant, conversation)
            if resolution is not None:
                from tenant_operator_legacy import admin
                from tenant_ai_dialog_scope import lock_dialog
                # Group resolution is an owner/admin-only, explicit operation.
                admin(cur, profile)
                if not isinstance(resolution, dict) or resolution.get('confirmed') is not True:
                    raise TenantLinearError('Подтвердите проверенный канал и назначение группы.', 409)
                if len(normalized) != 1:
                    raise TenantLinearError('Для разрешения выберите одного агента и один канал.', 409)
                selected = normalized[0]
                if (str(resolution.get('channel_id') or '') != selected['channel_id']
                        or str(resolution.get('agent_id') or '') != selected['agent_id']):
                    raise TenantLinearError('Подтверждение должно совпадать с выбранными каналом и агентом.', 409)
                if (set(resolution)-{'confirmed','expected_token','agent_version','channel_id','lead_id','agent_id','cancel_queued_work'}
                        or not isinstance(resolution.get('expected_token'), str)
                        or not re.fullmatch(r'[0-9a-f]{64}',resolution['expected_token'])):
                    raise TenantLinearError('Сначала проверьте текущую привязку группы.', 409)
                if 'cancel_queued_work' in resolution and type(resolution['cancel_queued_work']) is not bool:
                    raise TenantLinearError('Подтверждение остановки ожидающих задач некорректно.',409)
                if type(resolution.get('agent_version')) is not int or resolution.get('agent_version') < 1:
                    raise TenantLinearError('Обновите проверку агента.', 409)
                if type(resolution.get('lead_id')) is not int or resolution['lead_id'] < 1:
                    raise TenantLinearError('Обновите точный маршрут группы.', 409)
                lock_dialog(cur, tenant, 'connect', selected['channel_id'], conversation)
                cur.execute('''INSERT INTO saas_ai_group_coordination(tenant_id,group_jid,revision)
                  VALUES(%s::uuid,%s,0) ON CONFLICT DO NOTHING''', (tenant, conversation))
                # Revalidate the exact owner preview under the group/channel locks.
                cur.execute('''SELECT c.lead_id,c.conversation_kind,ch.active FROM saas_connect_conversations c
                  JOIN saas_connect_channels ch ON ch.tenant_id=c.tenant_id
                    AND ch.connect_channel_id=c.connect_channel_id
                  WHERE c.tenant_id=%s::uuid AND c.connect_channel_id=%s::uuid AND c.conversation_id=%s
                  FOR SHARE OF c,ch''',
                  (tenant, selected['channel_id'], conversation))
                route = dict(cur.fetchone() or {})
                if not route or int(route.get('lead_id') or 0) != int(resolution.get('lead_id') or 0):
                    raise TenantLinearError('Маршрут группы изменился. Проверьте его ещё раз.', 409)
                if route.get('conversation_kind') != 'group':
                    raise TenantLinearError('Выбранный маршрут больше не подтверждён как группа.', 409)
                if route.get('active') is not True:
                    raise TenantLinearError('Выбранный канал сейчас неактивен.', 409)
                # Lock only this exact group/channel's pending work. A worker
                # claiming one of these jobs must wait; unrelated tenant queues
                # and other channel routes remain available.
                cur.execute('''SELECT event_fingerprint FROM saas_ai_agent_jobs
                  WHERE tenant_id=%s::uuid AND provider='connect' AND conversation_id=%s
                    AND channel_id IN (%s,'') AND state IN ('queued','processing','drafted','unknown')
                  FOR UPDATE''',(tenant,conversation,selected['channel_id']))
                cur.fetchall()
                cur.execute('''SELECT fingerprint FROM saas_tenant_webhook_events
                  WHERE tenant_id=%s::uuid AND provider='connect'
                    AND payload->>'conversation_id'=%s
                    AND (payload->>'channel_id'=%s OR coalesce(payload->>'channel_id','')='')
                    AND state IN ('queued','processing')
                  FOR UPDATE''',(tenant,conversation,selected['channel_id']))
                cur.fetchall()
                cur.execute('''SELECT r.id FROM saas_ai_agent_runs r JOIN saas_tenant_webhook_events e
                  ON e.tenant_id=r.tenant_id AND e.fingerprint=r.event_fingerprint
                  WHERE r.tenant_id=%s::uuid AND e.provider='connect'
                    AND e.payload->>'conversation_id'=%s
                    AND (e.payload->>'channel_id'=%s OR coalesce(e.payload->>'channel_id','')='')
                    AND (r.state IN ('generating','unknown') OR r.result->>'send_state' IN ('reserved','unknown'))
                  FOR UPDATE OF r''',(tenant,conversation,selected['channel_id']))
                cur.fetchall()
                cur.execute('''SELECT d.id FROM saas_ai_agent_drafts d JOIN saas_tenant_webhook_events e
                  ON e.tenant_id=d.tenant_id AND e.fingerprint=d.source_fingerprint
                  WHERE d.tenant_id=%s::uuid AND e.provider='connect'
                    AND e.payload->>'conversation_id'=%s
                    AND (e.payload->>'channel_id'=%s OR coalesce(e.payload->>'channel_id','')='')
                    AND d.state IN ('pending','sending','unknown')
                  FOR UPDATE OF d''',(tenant,conversation,selected['channel_id']))
                cur.fetchall()
                cur.execute('''SELECT source_id FROM saas_connect_dialog_links WHERE tenant_id=%s::uuid
                  AND channel_id=%s::uuid AND source_id=%s AND active FOR UPDATE''',
                  (tenant,selected['channel_id'],conversation))
                cur.fetchall()
                snapshot = _legacy_resolution_snapshot(cur, tenant, actor, conversation,
                    selected['channel_id'], int(route['lead_id']), selected['agent_id'])
                if (snapshot['reason'] or snapshot['token'] != resolution['expected_token']
                        or int(snapshot['group_state'].get('revision') or 0) != int(data['expected_revision'])
                        or int(snapshot['agent'].get('version') or 0) != resolution['agent_version']):
                    raise TenantLinearError('Данные изменились или требуют отдельной проверки. Обновите предварительный просмотр.', 409)
                cancel_queued = snapshot['safely_stoppable']
                if cancel_queued and resolution.get('cancel_queued_work') is not True:
                    raise TenantLinearError('Подтвердите остановку только тех задач, которые ещё не запускались.',409)
                if not cancel_queued and resolution.get('cancel_queued_work') is True:
                    raise TenantLinearError('Очередь изменилась. Обновите предварительный просмотр.',409)
                cur.execute('SELECT * FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid FOR SHARE',
                  (tenant, selected['agent_id']))
                verified_agent = cur.fetchone()
                if not verified_agent or verified_agent.get('enabled') is not True or verified_agent.get('role') != 'primary' or not scope_matches(verified_agent.get('scopes'), selected['channel_id'], is_group=True):
                    raise TenantLinearError('У выбранного агента нет разрешённой области для этой группы и канала.', 409)
                stopped_ids=[]
                stopped_job_count=0
                stopped_event_only_count=0
                for item in cancel_queued:
                    if item.get('item_type') == 'job':
                        cur.execute('''UPDATE saas_ai_agent_jobs SET state='skipped',revision=revision+1,
                            lease_token=NULL,lease_until=NULL,error_code='owner_group_resolution_cancelled',updated_at=now()
                          WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND provider='connect'
                            AND conversation_id=%s AND channel_id=%s AND scope_channel_id=%s AND event_fingerprint=%s
                            AND state='queued' AND attempts=0 AND lease_token IS NULL AND lease_until IS NULL
                          RETURNING event_fingerprint''',
                          (tenant,item['agent_id'],conversation,item.get('channel_id') or '',item.get('channel_id') or '',item['job_id']))
                        changed=cur.fetchone()
                        if not changed:
                            raise TenantLinearError('Очередь изменилась. Обновите предварительный просмотр.',409)
                        stopped_ids.append(str(changed['event_fingerprint']))
                        stopped_job_count+=1
                    elif item.get('item_type') == 'event':
                        stopped_ids.append(str(item['job_id']))
                        stopped_event_only_count+=1
                    else:
                        raise TenantLinearError('Очередь изменилась. Обновите предварительный просмотр.',409)
                stopped_ids=sorted(set(stopped_ids))
                if stopped_ids:
                    cur.execute('''UPDATE saas_tenant_webhook_events SET state='skipped',
                        error_code='owner_group_resolution_cancelled',completed_at=now()
                      WHERE tenant_id=%s::uuid AND fingerprint=ANY(%s::text[]) AND state='queued'
                        AND attempts=0 AND lease_token IS NULL AND lease_until IS NULL
                        AND provider='connect' AND payload->>'conversation_id'=%s
                        AND payload->>'channel_id'=%s
                      RETURNING fingerprint''',(tenant,stopped_ids,conversation,selected['channel_id']))
                    stopped_events={str(row['fingerprint']) for row in cur.fetchall()}
                    if stopped_events!=set(stopped_ids):
                        raise TenantLinearError('Очередь события изменилась. Обновите предварительный просмотр.',409)
                # Keep the unbound historical row untouched. The group-specific
                # permit only makes the exact selected group/channel binding
                # admissible; all other channel routes remain quarantined.
                cur.execute('''UPDATE saas_ai_group_coordination SET assignments=%s::jsonb,
                    state=CASE WHEN state='human' THEN 'human' ELSE 'paused' END,
                    reason=CASE WHEN state='human' THEN reason ELSE 'legacy_group_resolved_paused' END,
                    revision=revision+1,updated_at=now()
                  WHERE tenant_id=%s::uuid AND provider='connect' AND group_jid=%s
                    AND revision=%s AND state IN ('paused','human') RETURNING revision,state''',
                  (_json(normalized), tenant, conversation, int(data['expected_revision'])) )
                saved = dict(cur.fetchone() or {})
                if not saved:
                    raise TenantLinearError('Состояние группы изменилось. Обновите предварительный просмотр.', 409)
                cur.execute('''INSERT INTO saas_ai_group_channel_legacy_permits
                  (tenant_id,provider,group_jid,channel_id,agent_id,legacy_revision,preview_hash,actor)
                  VALUES(%s::uuid,'connect',%s,%s::uuid,%s::uuid,%s,%s,%s)
                  ON CONFLICT(tenant_id,provider,group_jid,channel_id) DO UPDATE SET
                    agent_id=EXCLUDED.agent_id,legacy_revision=EXCLUDED.legacy_revision,
                    preview_hash=EXCLUDED.preview_hash,granted_at=clock_timestamp(),actor=EXCLUDED.actor''',
                  (tenant, conversation, selected['channel_id'], selected['agent_id'],
                   int(snapshot['legacy']['revision']), snapshot['token'], actor))
                _audit(cur,tenant,actor,'ai_group_legacy_channel_resolved','conversation',conversation,
                  {'channel_id':selected['channel_id'],'agent_id':selected['agent_id'],
                   'legacy_revision':int(snapshot['legacy']['revision']),
                   'preview_hash':snapshot['token'],'previous_assignments':snapshot['group_state'].get('assignments') or [],
                   'paused_or_human_preserved':True,'history_merged':False,
                   'stopped_queued_job_count':stopped_job_count,
                   'stopped_queued_event_only_count':stopped_event_only_count,
                   'stopped_queued_event_ids':stopped_ids})
                conn.commit()
                return {'revision':int(saved['revision']),'state':saved['state'],
                  'legacy_resolved':True,'resume_required':True,'automatic_replies_enabled':False,
                  'stopped_queued_count':stopped_job_count+stopped_event_only_count,
                  'stopped_queued_event_ids':stopped_ids}
            existing = read(cur, tenant, conversation)
            if int(data.get('expected_revision', -1)) != int(existing.get('revision', 0)):
                raise TenantLinearError('Состояние группы изменилось. Обновите окно.', 409)
            for slot in normalized:
                assert_resolved(cur, tenant, slot['channel_id'], conversation)
                cur.execute('''SELECT 1 FROM saas_connect_conversations WHERE tenant_id=%s::uuid
                  AND connect_channel_id=%s::uuid AND conversation_id=%s''', (tenant, slot['channel_id'], conversation))
                if not cur.fetchone():
                    raise TenantLinearError('Канал группы не подтверждён.', 409)
                cur.execute('SELECT * FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid FOR SHARE', (tenant, slot['agent_id']))
                agent = cur.fetchone()
                if not agent or agent.get('enabled') is not True or agent.get('role','primary') != 'primary' or not scope_matches(agent['scopes'], slot['channel_id'], is_group=True):
                    raise TenantLinearError('Агент не имеет права работать с группами этого канала.', 409)
            old=existing.get('assignments') or []
            if len(old)==len(normalized)==1 and str(old[0].get('agent_id'))==str(normalized[0]['agent_id']) and str(old[0].get('channel_id'))==str(normalized[0]['channel_id']):
                return {'revision':int(existing.get('revision',0)), 'state':existing.get('state','paused'), 'unchanged':True}
            # Any change fences prepared responses without resuming AI or erasing an existing handoff.
            current_state=existing.get('state') or 'paused'
            next_state='human' if current_state=='human' else 'paused'
            next_reason=existing.get('reason') if current_state in {'human','paused'} else 'group_assignment_changed'
            stop(cur, tenant, conversation, state=next_state, reason=next_reason or 'group_assignment_changed',
                 actor=existing.get('assigned_to') if current_state=='human' else actor)
            cur.execute("UPDATE saas_ai_group_coordination SET assignments=%s::jsonb WHERE tenant_id=%s::uuid AND provider='connect' AND group_jid=%s", (_json(normalized), tenant, conversation))
            _audit(cur,tenant,actor,'ai_group_configured','conversation',conversation,{'assignments':normalized})
            return {'revision':read(cur,tenant,conversation)['revision'], 'state':read(cur,tenant,conversation)['state']}


def transition(cur, tenant, conversation, state, reason, actor):
    from tenant_ai_agents_store import _assert_resume_allowed
    if state != 'active':
        stop(cur, tenant, conversation, state, reason, actor)
        return
    selected = authority(cur, tenant, None, conversation, allow_stopped=True)
    if not selected:
        raise TenantLinearError('Нет единственного основного агента: проверьте назначения, расписания и карантин.', 409)
    channel = selected['sending_channel_id']
    # Only this selected channel is resumed. Other channel states/history remain.
    cur.execute('''INSERT INTO saas_ai_conversation_state(tenant_id,provider,channel_id,conversation_id,agent_id,state)
      VALUES(%s::uuid,'connect',%s,%s,%s::uuid,'paused') ON CONFLICT(tenant_id,provider,channel_id,conversation_id)
      DO UPDATE SET agent_id=EXCLUDED.agent_id,revision=saas_ai_conversation_state.revision+1''',
      (tenant, channel, conversation, selected['primary_agent_id']))
    # Existing company, runtime, membership and integration gates.
    cur.execute("UPDATE saas_ai_conversation_state SET state='paused' WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id=%s AND state='active'", (tenant,channel,conversation))
    _assert_resume_allowed(cur,tenant,'connect',conversation,channel)
    cur.execute("UPDATE saas_ai_group_coordination SET state='active',reason='',assigned_to=NULL,revision=revision+1,resumed_at=clock_timestamp(),updated_at=now() WHERE tenant_id=%s::uuid AND provider='connect' AND group_jid=%s", (tenant, conversation))
    cur.execute("UPDATE saas_ai_conversation_state SET state='active',reason='',revision=revision+1,resumed_at=clock_timestamp(),updated_at=now() WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id=%s", (tenant,channel,conversation))


def view(cur, tenant, conversation):
    state = read(cur, tenant, conversation)
    selected = authority(cur, tenant, None, conversation, allow_stopped=True)
    cur.execute('''SELECT c.connect_channel_id::text AS channel_id,c.lead_id,ch.name AS channel_name
      FROM saas_connect_conversations c JOIN saas_connect_channels ch
      ON ch.tenant_id=c.tenant_id AND ch.connect_channel_id=c.connect_channel_id
      WHERE c.tenant_id=%s::uuid AND c.conversation_id=%s ORDER BY ch.name,c.connect_channel_id''', (tenant,conversation))
    channels = [dict(r) for r in cur.fetchall()]
    if selected and state.get('state')=='active':
        cur.execute("SELECT state FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND conversation_id=%s",(tenant,selected['sending_channel_id'],conversation))
        channel_state=(cur.fetchone() or {}).get('state')
        if channel_state in ('human','paused'):
            state={**state,'state':channel_state,'reason':'group_channel_requires_resume'}
    return {'group_jid':conversation,'state':state.get('state','paused'),
      'reason':state.get('reason') or ('' if selected else 'group_assignment_required_or_ambiguous'),
      'revision':int(state.get('revision',0)), 'assignments':state.get('assignments',[]),
      'assigned_to':state.get('assigned_to'), 'channels':channels,
      'primary_agent_id':selected['primary_agent_id'] if selected else None,
      'sending_channel_id':selected['sending_channel_id'] if selected else None}


class EmployeeIdentityConflict(ValueError):
    """Verified direct aliases point to different configured employees."""


def verified_employee(cur, tenant, data, *, direct_aliases=()):
    """Resolve only an explicitly configured tenant employee identity.

    Group LIDs are never inferred from phone JIDs. Direct-chat callers may
    supply transactionally verified aliases; matching still requires a unique,
    administrator-configured identity on an active employee card.
    """
    jid = data.get('participantJid')
    if not isinstance(jid,str) or not re.fullmatch(r'[0-9]{5,22}@(s\.whatsapp\.net|c\.us|lid)',jid):
        return None
    identities={jid} | {x for x in direct_aliases if isinstance(x,str)
        and re.fullmatch(r'[0-9]{5,22}@(s\.whatsapp\.net|c\.us|lid)',x)}
    phones=sorted({x.split('@',1)[0] for x in identities if x.endswith(('@s.whatsapp.net','@c.us'))})
    matches=set()
    if phones:
      cur.execute('''SELECT DISTINCT m.telegram_id AS member_id
      FROM saas_tenant_members m
      JOIN saas_tenant_member_whatsapp_numbers n ON n.tenant_id=m.tenant_id AND n.telegram_id=m.telegram_id
      WHERE m.tenant_id=%s::uuid AND m.active=TRUE AND n.recognition_enabled=TRUE
        AND n.phone_digits=ANY(%s::text[])''',(tenant,phones))
      matches.update(int(row['member_id']) for row in cur.fetchall())
    channel=data.get('channelId') or data.get('channel_id')
    try:channel=str(uuid.UUID(str(channel)))
    except (ValueError,TypeError,AttributeError):channel=''
    if channel:
      cur.execute('''SELECT DISTINCT m.telegram_id AS member_id FROM saas_ai_group_staff s
        JOIN saas_tenant_members m ON m.tenant_id=s.tenant_id AND m.telegram_id=s.member_id AND m.active=TRUE
        JOIN saas_connect_channels ch ON ch.tenant_id=s.tenant_id AND ch.connect_channel_id=s.channel_id AND ch.active=TRUE
        WHERE s.tenant_id=%s::uuid AND s.channel_id=%s::uuid AND s.jid=ANY(%s::text[])''',(tenant,channel,sorted(identities)))
      matches.update(int(row['member_id']) for row in cur.fetchall())
    if direct_aliases and len(matches)>1:
        raise EmployeeIdentityConflict('Conflicting configured direct identities')
    return next(iter(matches)) if len(matches)==1 else None


def register_employee(profile,data):
    """Confirm a member against a previously observed signed fromMe identity.

    This is an administrator assertion plus provider proof of the company
    account. A name, inbound message or browser-supplied 'verified' flag cannot
    establish identity. Mapping is explicit; no LID equivalence is invented.
    """
    from tenant_ai_agents_store import _authorize,_audit
    from tenant_ai_agents_schema import ensure_schema
    from tenant_ai_dialog_scope import channel_scope
    jid=data.get('jid'); channel=channel_scope('connect',data.get('channel_id'))
    if not isinstance(jid,str) or not re.fullmatch(r'[0-9]{5,22}@(s\.whatsapp\.net|lid)',jid):
        raise TenantLinearError('Требуется точный подтверждённый JID/LID.',400)
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,actor=_authorize(cur,profile)
            cur.execute('''SELECT 1 FROM saas_crm_messages WHERE tenant_id=%s::uuid AND channel=%s
              AND direction='outgoing' AND raw->>'fromMe'='true' AND raw->>'source'='linked_phone'
              AND raw->>'participantJid'=%s AND COALESCE(raw->>'authorshipUncertain','false')<>'true'
              AND COALESCE(raw->>'originClientMessageId','')='' AND COALESCE(raw->>'clientMessageId','')='' LIMIT 1''',(tenant,'connect:'+channel,jid))
            if not cur.fetchone():raise TenantLinearError('Нет подтверждения авторства с номера компании.',409)
            member=int(data.get('member_id'))
            cur.execute('SELECT 1 FROM saas_tenant_members WHERE tenant_id=%s::uuid AND telegram_id=%s AND active=TRUE',(tenant,member))
            if not cur.fetchone():raise TenantLinearError('Сотрудник недоступен.',403)
            cur.execute('''INSERT INTO saas_ai_group_staff(tenant_id,channel_id,jid,member_id,verified_by)
              VALUES(%s::uuid,%s::uuid,%s,%s,%s) ON CONFLICT(tenant_id,channel_id,jid)
              DO UPDATE SET member_id=EXCLUDED.member_id,verified_by=EXCLUDED.verified_by,verified_at=now()''',(tenant,channel,jid,member,actor))
            _audit(cur,tenant,actor,'ai_group_staff_verified','member',str(member),{'channel_id':channel})
            return {'verified':True}


def known_ai_origin(cur, tenant, data):
    token = data.get('originClientMessageId') or data.get('clientMessageId')
    import uuid
    try:token=str(uuid.UUID(str(token)))
    except (ValueError,TypeError):return False
    cur.execute('''SELECT 1 FROM saas_ai_agent_runs WHERE tenant_id=%s::uuid AND id=%s::uuid
      UNION ALL SELECT 1 FROM saas_ai_agent_drafts WHERE tenant_id=%s::uuid AND id=%s::uuid''',(tenant,token,tenant,token))
    return bool(cur.fetchone())
