"""Connect provider adapter for the existing durable AI Agent queue.

This module never reads Connect's database or trusts browser supplied routing.
It is called only after the signed Connect webhook was accepted and the message
was written into the tenant's own CRM history in the same transaction.
"""
from __future__ import annotations

import hashlib
from contextlib import nullcontext
import re
from tenant_ai_dialog_scope import channel_scope, job_scope, lock_dialog

from tenant_ai_agents_schema import ensure_schema
from tenant_ai_agent_runtime_flags import enabled as runtime_flag
from tenant_ai_agent_access import tenant_status_allows_ai
from tenant_platform import _connect, _json


def _whatsapp_phone(conversation_id: str) -> str:
    """Return a usable phone only when Connect's conversation id proves one.

    Group, broadcast and other non-person JIDs must not accidentally satisfy a
    ``phone_received`` trigger.  A personal WhatsApp JID is a stable source of
    contact identity even though Connect does not expose a separate contact
    object in every inbound webhook.
    """
    jid = str(conversation_id or '')
    # Evolution's group ids may begin with digits too. Only a direct WhatsApp
    # JID can prove that the prefix is a customer phone number.
    if '@' in jid and not jid.endswith('@s.whatsapp.net'):
        return ''
    candidate = jid.split('@', 1)[0]
    return candidate if re.fullmatch(r'[1-9][0-9]{5,18}', candidate) else ''


def scope_matches(scopes, channel_id: str, *, is_group=False) -> bool:
    """A Connect scope is explicit; legacy Kommo scopes never bleed across."""
    for scope in scopes if isinstance(scopes, list) else []:
        if not isinstance(scope, dict):
            continue
        if str(scope.get('provider') or '').casefold() != 'connect':
            continue
        selected = str(scope.get('channel_id') or '').casefold()
        if (not selected or selected == str(channel_id or '').casefold()) and (
                not is_group or scope.get('allow_group_messages') is True):
            return True
    return False


def test_allowed(cur, tenant: str, channel_id: str, conversation_id: str, *, agent_id=None) -> bool:
    cur.execute('''SELECT enabled,emergency_stop,verified,channel_id,conversation_id,agent_id
      FROM saas_ai_agent_connect_test_mode WHERE tenant_id=%s::uuid FOR SHARE''', (tenant,))
    row = cur.fetchone() or {}
    return bool(row.get('enabled') is True and row.get('verified') is True
                and row.get('emergency_stop') is not True
                and str(row.get('channel_id') or '') == str(channel_id)
                and str(row.get('conversation_id') or '') == str(conversation_id)
                and (not agent_id or not row.get('agent_id') or str(row['agent_id'])==str(agent_id)))


def allowed(job: dict, connection=None) -> bool:
    if str(job.get('provider') or '') != 'connect':
        return False
    if not runtime_flag('AI_AGENTS_ENABLED'):
        return False
    tenant = str(job.get('tenant_id') or '')
    channel = str(job.get('channel_id') or '')
    conversation = str(job.get('conversation_id') or '')
    from tenant_ai_group_coordination import group_jid
    bound_agent = None if group_jid(conversation) else job.get('agent_id')
    with (nullcontext(connection) if connection is not None else _connect()) as conn:
        if connection is None:
            ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT s.enabled,s.emergency_stop,s.operating_mode,t.status
              FROM saas_ai_agent_settings s JOIN saas_tenants t ON t.id=s.tenant_id
              WHERE s.tenant_id=%s::uuid FOR SHARE OF s,t''', (tenant,))
            settings = cur.fetchone() or {}
            cur.execute('''SELECT 1 FROM saas_tenant_integrations i JOIN saas_connect_channels c
              ON c.tenant_id=i.tenant_id AND c.connect_channel_id=%s::uuid AND c.active=TRUE
              WHERE i.tenant_id=%s::uuid AND i.provider='connect' AND i.status='connected'
                AND NOT ai_legacy_blocked(i.tenant_id,c.connect_channel_id::text,%s,%s)
                AND EXISTS (SELECT 1 FROM saas_connect_conversations route
                  WHERE route.tenant_id=i.tenant_id AND route.connect_channel_id=c.connect_channel_id
                    AND route.conversation_id=%s AND (%s::bigint IS NULL OR route.lead_id=%s))
                AND NOT EXISTS (SELECT 1 FROM saas_ai_conversation_state bound
                  WHERE bound.tenant_id=i.tenant_id AND bound.provider='connect'
                    AND bound.channel_id=c.connect_channel_id::text AND bound.conversation_id=%s
                    AND bound.agent_assignment_id IS NOT NULL AND %s::uuid IS NOT NULL
                    AND bound.agent_id IS DISTINCT FROM %s::uuid)
              FOR SHARE OF i,c''',
                        (channel, tenant, conversation, job.get('event_fingerprint'), conversation, job.get('lead_id'), job.get('lead_id'),
                         conversation, bound_agent, bound_agent))
            connected = bool(cur.fetchone())
            tenant_active = tenant_status_allows_ai(settings.get('status'), 'connect')
            if settings.get('operating_mode') == 'active':
                result = bool(tenant_active and connected and settings.get('enabled') is True
                              and settings.get('emergency_stop') is not True)
            else:
                result = bool(tenant_active and connected and settings.get('enabled') is True and settings.get('emergency_stop') is not True
                              and test_allowed(cur, tenant, channel, conversation, agent_id=job.get('agent_id')))
            from tenant_ai_group_coordination import group_jid, authority, job_allowed
            if group_jid(conversation):
                result = result and bool(authority(cur, tenant, channel, conversation, job.get('agent_id')))
                if job.get('agent_id'):
                    result = result and job_allowed(cur, job)
        if connection is None:
            conn.commit()
    return result


def enqueue(tenant: str, *, channel_id: str, conversation_id: str, lead_id: int,
            message_id: str, event_id: str, text: str, message_type: str = 'text', is_group=False, provider_message_id=None, connection=None) -> int:
    """Queue one inbound Connect message for all matching tenant agents.

    The webhook event table + job primary key make Connect retries and Evolution
    duplicate events harmless.  No model call occurs in this transaction.
    """
    channel_id = channel_scope('connect', channel_id)
    fingerprint = hashlib.sha256(('connect:' + str(message_id or event_id)).encode('utf-8')).hexdigest()
    queued = 0
    with (nullcontext(connection) if connection is not None else _connect()) as conn:
        if connection is None:
            ensure_schema(conn)
        with conn.cursor() as cur:
            lock_dialog(cur, tenant, 'connect', channel_id, conversation_id)
            cur.execute('SELECT operating_mode,enabled,emergency_stop FROM saas_ai_agent_settings WHERE tenant_id=%s::uuid', (tenant,))
            settings = cur.fetchone() or {}
            test_mode = str(settings.get('operating_mode') or 'test') != 'active'
            payload = {'message_id': str(message_id or event_id), 'channel_id': str(channel_id),
                       'conversation_id': str(conversation_id), 'text': str(text or '')[:8000],
                       'message_type': str(message_type or 'text')[:40], 'test_mode': test_mode,
                       # Filled after the durable conversation-state update.
                       # A phone is available only for the first inbound event
                       # in a person-to-person WhatsApp conversation.
                       'first_inquiry': False, 'phone': ''}
            from tenant_ai_group_coordination import group_jid, admit
            group_fence = None
            if is_group and not group_jid(conversation_id):
                return 0
            if group_jid(conversation_id):
                group_fence = admit(cur, tenant, channel_id, conversation_id, provider_message_id, message_id)
                if not group_fence:
                    return 0
                payload.update(group_fence)
            cur.execute('''INSERT INTO saas_tenant_webhook_events
              (tenant_id,fingerprint,provider,external_id,event_type,payload,state,available_at)
              VALUES(%s::uuid,%s,'connect',%s,'incoming_message',%s::jsonb,'queued',now())
              ON CONFLICT(tenant_id,fingerprint) DO NOTHING RETURNING fingerprint''',
                        (tenant, fingerprint, str(message_id or event_id), _json(payload)))
            if not cur.fetchone():
                cur.execute("SELECT payload FROM saas_tenant_webhook_events WHERE tenant_id=%s::uuid AND fingerprint=%s", (tenant, fingerprint))
                previous = (cur.fetchone() or {}).get('payload') or {}
                if previous.get('channel_id') != channel_id or previous.get('conversation_id') != conversation_id:
                    raise ValueError('Connect event scope mismatch')
                if connection is None:
                    conn.commit()
                return 0
            if not allowed({'provider':'connect','tenant_id':tenant,'channel_id':channel_id,'conversation_id':conversation_id,'lead_id':lead_id,'event_fingerprint':fingerprint}, connection=conn):
                reason = ('ai_agents_server_disabled' if not runtime_flag('AI_AGENTS_ENABLED') else
                          'company_ai_disabled' if settings.get('enabled') is not True else
                          'emergency_stop' if settings.get('emergency_stop') else 'connect_admission_blocked')
                cur.execute("UPDATE saas_tenant_webhook_events SET state='skipped',completed_at=now(),error_code=%s WHERE tenant_id=%s::uuid AND fingerprint=%s",
                            (reason, tenant, fingerprint))
                if connection is None:
                    conn.commit()
                return 0
            cur.execute('''UPDATE saas_tenant_webhook_events SET state='superseded',completed_at=now(),error_code='newer_inbound'
              WHERE tenant_id=%s::uuid AND provider='connect' AND state IN ('queued','processing')
                AND payload->>'conversation_id'=%s AND payload->>'channel_id'=%s AND fingerprint<>%s''', (tenant, conversation_id, channel_id, fingerprint))
            from tenant_ai_testing_scope import clause
            cur.execute('''SELECT a.id,a.scopes,a.delay_seconds,a.enabled,a.schedule,a.mode FROM saas_ai_agents a
              WHERE a.role='primary' AND a.tenant_id=%s::uuid'''+clause('a','connect')+' ORDER BY a.created_at,a.id', (tenant,))
            tenant_agents = cur.fetchall()
            # Explicit assignment is durable across Resume. Never silently
            # replace it with another matching agent on the next inbound.
            cur.execute('''SELECT agent_id FROM saas_ai_conversation_state
              WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s
                AND conversation_id=%s AND agent_assignment_id IS NOT NULL''',
                (tenant, channel_id, conversation_id))
            bound = cur.fetchone()
            if bound and not group_fence:
                tenant_agents = [a for a in tenant_agents if str(a['id']) == str(bound['agent_id'])]
            if group_fence:
                tenant_agents = [a for a in tenant_agents if str(a['id']) == group_fence['primary_agent_id']]
            # Manual mode is human-only in Active Mode. Test Mode keeps its
            # explicitly bound draft workflow for validating an agent.
            enabled_agents = [row for row in tenant_agents if row.get('enabled') is True
                              and (test_mode or row.get('mode') == 'automatic')]
            channel_agents = [row for row in enabled_agents if scope_matches(row.get('scopes'), channel_id)]
            agents = [row for row in enabled_agents if scope_matches(row.get('scopes'), channel_id, is_group=is_group)]
            from tenant_ai_agent_schedule import current_candidates
            agents = current_candidates(agents)
            if not agents:
                reason = ('agent_not_found' if not tenant_agents else 'agent_disabled' if not enabled_agents else
                          'group_messages_not_allowed' if is_group and channel_agents else 'no_connect_scope')
                cur.execute("UPDATE saas_tenant_webhook_events SET state='skipped',completed_at=now(),error_code=%s WHERE tenant_id=%s::uuid AND fingerprint=%s", (reason, tenant, fingerprint))
            if not agents:
                if connection is None:
                    conn.commit()
                return queued
            # ``new_inquiry`` must mean the first customer message actually
            # stored for this Connect conversation, not merely the first
            # event received after an agent happened to be configured.  The
            # CRM copy is visible in this transaction and is committed
            # together with the job; no browser-provided flag is trusted.
            cur.execute('''SELECT count(*) AS inbound_count FROM saas_crm_messages
              WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND channel=%s
                AND direction='incoming' AND external_id<>'' ''',
                        (tenant, int(lead_id), 'connect:' + str(channel_id)))
            inbound_count = int((cur.fetchone() or {}).get('inbound_count') or 0)
            first_inquiry = inbound_count == 1
            # Human Mode and Pause intentionally survive subsequent customer
            # messages.  A manager must explicitly resume the dialogue; an
            # inbound message cannot silently revive the agent.
            cur.execute('''INSERT INTO saas_ai_conversation_state
              (tenant_id,provider,conversation_id,channel_id,agent_id,state,first_inbound_message_id,last_inbound_message_id)
              VALUES(%s::uuid,'connect',%s,%s,%s::uuid,'active',%s,%s)
              ON CONFLICT(tenant_id,provider,channel_id,conversation_id) DO UPDATE SET
                agent_id=EXCLUDED.agent_id,last_inbound_message_id=EXCLUDED.last_inbound_message_id,
                revision=saas_ai_conversation_state.revision+1,updated_at=now()
              WHERE saas_ai_conversation_state.state='active'
              RETURNING first_inbound_message_id''',
                        (tenant, conversation_id, channel_id, agents[0]['id'], payload['message_id'], payload['message_id']))
            state = cur.fetchone()
            if not state:
                cur.execute("UPDATE saas_tenant_webhook_events SET state='skipped',completed_at=now(),error_code='conversation_human_or_paused' WHERE tenant_id=%s::uuid AND fingerprint=%s",
                            (tenant, fingerprint))
                if connection is None:
                    conn.commit()
                return 0
            from tenant_ai_legacy_permit import renew
            renew(cur,tenant,channel_id,conversation_id)
            payload['first_inquiry'] = first_inquiry
            if payload['first_inquiry']:
                payload['phone'] = _whatsapp_phone(conversation_id)
            cur.execute('UPDATE saas_tenant_webhook_events SET payload=%s::jsonb WHERE tenant_id=%s::uuid AND fingerprint=%s',
                        (_json(payload), tenant, fingerprint))
            from tenant_ai_pilot import processing_allowed
            if not processing_allowed({'provider':'connect','tenant_id':tenant,'channel_id':channel_id,
                    'conversation_id':conversation_id,'agent_id':agents[0]['id'],'event_fingerprint':fingerprint}, connection=conn):
                cur.execute("UPDATE saas_tenant_webhook_events SET state='skipped',completed_at=now(),error_code='pilot_scope_blocked' WHERE tenant_id=%s::uuid AND fingerprint=%s",
                    (tenant, fingerprint))
                if connection is None:
                    conn.commit()
                return 0
            for agent in agents:
                delay = max(0, min(int(agent.get('delay_seconds') or 0), 300))
                cur.execute('''INSERT INTO saas_ai_agent_jobs
                  (tenant_id,agent_id,provider,conversation_id,lead_id,talk_id,chat_id,channel_id,event_fingerprint,due_at,state)
                  VALUES(%s::uuid,%s::uuid,'connect',%s,%s,'',%s,%s,%s,now()+(%s*interval '1 second'),'queued')
                  ON CONFLICT(tenant_id,agent_id,provider,scope_channel_id,conversation_id) DO UPDATE SET
                    lead_id=EXCLUDED.lead_id,chat_id=EXCLUDED.chat_id,channel_id=EXCLUDED.channel_id,
                    event_fingerprint=EXCLUDED.event_fingerprint,revision=saas_ai_agent_jobs.revision+1,
                    state='queued',due_at=EXCLUDED.due_at,lease_token=NULL,lease_until=NULL,attempts=0,error_code='',updated_at=now()''',
                            (tenant, agent['id'], conversation_id, int(lead_id), conversation_id, channel_id, fingerprint, delay))
                queued += 1
        if connection is None:
            conn.commit()
    return queued


def finish_sent_after_reply_handoff(job: dict, reservation: dict, message_id: str) -> bool:
    """Persist an accepted Connect reply and atomically hand the chat to staff.

    Connect's send lock guarantees that the message was accepted only while
    the dialogue was active.  Recording the handoff in the same receipt
    transaction closes the old gap where a ``handoff_after_reply`` trigger
    could send a message but leave the AI active after a process restart.
    """
    from tenant_support_inbox_store import handoff_ticket, summarize_created, handoff_recorded
    ticket = None
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            lock_dialog(cur,job['tenant_id'],'connect',job['channel_id'],job['conversation_id'])
            recorded = handoff_recorded(cur, job)
            cur.execute("UPDATE saas_ai_agent_runs SET state='completed',result=%s::jsonb,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND state='generating'",
                        (_json({'send_state': 'accepted', 'message_id': str(message_id)}),
                         job['tenant_id'], reservation['run_id']))
            cur.execute("UPDATE saas_ai_agent_jobs SET state='sent',lease_token=NULL,lease_until=NULL,error_code='',updated_at=now() WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND provider='connect' AND conversation_id=%s AND channel_id=%s AND lease_token=%s AND revision=%s",
                        (job['tenant_id'], job['agent_id'], job['conversation_id'], job_scope(job), job['lease_token'], job['revision']))
            cur.execute("UPDATE saas_tenant_webhook_events SET state='completed',completed_at=now(),error_code='' WHERE tenant_id=%s::uuid AND fingerprint=%s",
                        (job['tenant_id'], job['event_fingerprint']))
            # The send transaction may already have committed this handoff.
            # A repeated callback must never undo a subsequent explicit Resume.
            if recorded:
                conn.commit()
                return True
            from tenant_ai_group_coordination import stop
            lock_dialog(cur,job['tenant_id'],'connect',job['channel_id'],job['conversation_id'])
            stop(cur,job['tenant_id'],job['conversation_id'],reason='trigger_reply_sent_then_handoff')
            # Do not overwrite a real operator takeover that won the race.
            cur.execute("UPDATE saas_ai_conversation_state SET state='human',reason='trigger_reply_sent_then_handoff',revision=revision+1,updated_at=now() WHERE tenant_id=%s::uuid AND provider='connect' AND conversation_id=%s AND channel_id=%s AND state='active' RETURNING revision",
                        (job['tenant_id'], job['conversation_id'], job_scope(job)))
            changed = cur.fetchone()
            ticket = handoff_ticket(cur, job, 'trigger_reply_sent_then_handoff')
            if changed:
                cur.execute("UPDATE saas_tenant_webhook_events SET state='skipped',completed_at=now(),error_code='trigger_reply_sent_then_handoff' WHERE tenant_id=%s::uuid AND fingerprint IN (SELECT event_fingerprint FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND provider='connect' AND conversation_id=%s AND channel_id=%s AND state IN ('queued','processing'))",
                            (job['tenant_id'], job['tenant_id'], job['conversation_id'], job_scope(job)))
                cur.execute("UPDATE saas_ai_agent_jobs SET state='skipped',revision=revision+1,lease_token=NULL,lease_until=NULL,error_code='trigger_reply_sent_then_handoff',updated_at=now() WHERE tenant_id=%s::uuid AND provider='connect' AND conversation_id=%s AND channel_id=%s AND state IN ('queued','processing')",
                            (job['tenant_id'], job['conversation_id'], job_scope(job)))
        conn.commit()
    summarize_created(job['tenant_id'], ticket)
    return True


def history(tenant: str, lead_id: int, channel_id: str) -> list[dict]:
    """Read tenant-local Connect history; never falls back to Kommo."""
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT * FROM (
              SELECT m.external_id,m.direction,m.body,m.message_type,m.media_url,m.happened_at,m.id,
                COALESCE(v.state,'') AS voice_state,
                NOT EXISTS (SELECT 1 FROM saas_connect_conversations c
                  JOIN saas_ai_conversation_state cs ON cs.tenant_id=c.tenant_id
                    AND cs.provider='connect' AND cs.conversation_id=c.conversation_id AND cs.channel_id=c.connect_channel_id::text
                  WHERE c.tenant_id=m.tenant_id AND 'connect:' || c.connect_channel_id::text=m.channel
                    AND c.lead_id=m.kommo_lead_id AND m.created_at<=cs.resumed_at) AS voice_eligible,
                CASE WHEN v.state='completed' THEN m.body ELSE '' END AS transcript
              FROM saas_crm_messages m LEFT JOIN saas_connect_voice_receipts v
                ON v.tenant_id=m.tenant_id AND 'connect:' || v.channel_id::text=m.channel AND v.external_id=m.external_id
              WHERE m.tenant_id=%s::uuid AND m.kommo_lead_id=%s
                AND m.channel=%s ORDER BY m.happened_at DESC,m.id DESC LIMIT 80
              ) recent ORDER BY happened_at,id''', (tenant, int(lead_id), 'connect:' + channel_id))
            rows = cur.fetchall()
    return [{'external_id': str(row.get('external_id') or '').removeprefix('connect:'),
             'direction': str(row.get('direction') or ''), 'body': str(row.get('body') or ''),
             'message_type': str(row.get('message_type') or 'text'), 'media_url': str(row.get('media_url') or ''),
             'has_attachment': bool(row.get('media_url')) or str(row.get('message_type') or 'text') != 'text',
             'happened_at': row['happened_at'].isoformat() if row.get('happened_at') else '',
             'transcript': str(row.get('transcript') or ''), 'voice_state': str(row.get('voice_state') or ''),
             'voice_eligible': row.get('voice_eligible') is not False,
             'channel': channel_id} for row in rows]


def mark_operator_reply(tenant: str, lead_id: int, channel_id: str, conversation_id: str, *, connection=None, actor=None) -> None:
    """Human replies stop pending work atomically with the caller's event receipt."""
    with (nullcontext(connection) if connection is not None else _connect()) as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            channel_id = lock_dialog(cur, tenant, "connect", channel_id, conversation_id)
            cur.execute("SELECT 1 FROM saas_connect_conversations WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND conversation_id=%s AND lead_id=%s", (tenant, channel_id, conversation_id, int(lead_id)))
            if not cur.fetchone():
                raise ValueError("Connect dialogue route mismatch")
            from tenant_ai_group_coordination import stop
            stop(cur, tenant, conversation_id, actor=actor)
            cur.execute('''INSERT INTO saas_ai_conversation_state(tenant_id,provider,conversation_id,channel_id,state,reason)
              VALUES(%s::uuid,'connect',%s,%s,'human','manager_replied')
              ON CONFLICT(tenant_id,provider,channel_id,conversation_id) DO UPDATE SET state='human',reason='manager_replied',
                revision=saas_ai_conversation_state.revision+1,updated_at=now()''', (tenant, conversation_id, channel_id))
            cur.execute('''UPDATE saas_ai_agent_jobs SET state='skipped',lease_token=NULL,lease_until=NULL,
              error_code='manager_replied',updated_at=now() WHERE tenant_id=%s::uuid AND provider='connect'
                AND conversation_id=%s AND channel_id=%s AND state IN ('queued','processing')''', (tenant, conversation_id, channel_id))
        if connection is None:
            conn.commit()


def group_context(job, rows, through):
    """Read-only shared group evidence; admission/transcription/send stay channel-local."""
    from tenant_ai_group_coordination import group_jid
    if not group_jid(job.get('conversation_id')):
        return rows
    from tenant_connect_group_history import page
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute('''SELECT 1 FROM saas_connect_conversations WHERE tenant_id=%s::uuid
              AND connect_channel_id=%s::uuid AND conversation_id=%s AND lead_id=%s''',
              (job['tenant_id'],job['channel_id'],job['conversation_id'],job['lead_id']))
            if not cur.fetchone(): return []
            merged=page(cur,job['tenant_id'],job['conversation_id'],limit=80,through=through)
    local={r['external_id']:r for r in rows}
    result=[]
    for row in reversed(merged):
        raw=row.get('raw') or {};external=str(row['external_id']).removeprefix('connect:')
        transcript=(raw.get('voice_transcription') or {})
        cached=str(row.get('body') or '') if transcript.get('status')=='completed' else ''
        own=local.get(external,{})
        result.append({'external_id':external,'body':str(row.get('body') or ''),
          'direction':'outgoing' if raw.get('ai_authored') is True or any(s['direction']=='outgoing' for s in row['sources']) else row['direction'],
          'message_type':row['message_type'],'media_url':row.get('media_url') or '',
          'happened_at':row['happened_at'].isoformat() if row.get('happened_at') else '',
          'transcript':own.get('transcript') or cached,
          'author_name':str(row.get('author_name') or raw.get('participantName') or '')[:180],
          'channel': str(job['channel_id']) if any(str(s['channel_id'])==str(job['channel_id']) for s in row['sources']) else str(row['source_channel_id']),
          'photo_message_id': next((str(s['external_id']).removeprefix('connect:') for s in row['sources'] if str(s['channel_id'])==str(job['channel_id']) and s.get('external_id')),None),
          'document_message_id': next((str(s['external_id']).removeprefix('connect:') for s in row['sources'] if str(s['channel_id'])==str(job['channel_id']) and s.get('external_id')),None),
          'source_channels':list(dict.fromkeys(str(s['channel_name'])[:80] for s in row['sources']))[:3]})
    return result
