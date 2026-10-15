"""Connect provider adapter for the existing durable AI Agent queue.

This module never reads Connect's database or trusts browser supplied routing.
It is called only after the signed Connect webhook was accepted and the message
was durably written into the tenant's own CRM history.
"""
from __future__ import annotations

import hashlib

from tenant_ai_agents_schema import ensure_schema
from tenant_ai_agent_runtime_flags import enabled as runtime_flag
from tenant_platform import _connect, _json


def scope_matches(scopes, channel_id: str) -> bool:
    """A Connect scope is explicit; legacy Kommo scopes never bleed across."""
    for scope in scopes if isinstance(scopes, list) else []:
        if not isinstance(scope, dict):
            continue
        if str(scope.get('provider') or '').casefold() != 'connect':
            continue
        selected = str(scope.get('channel_id') or '').casefold()
        if not selected or selected == str(channel_id or '').casefold():
            return True
    return False


def test_allowed(cur, tenant: str, channel_id: str, conversation_id: str) -> bool:
    cur.execute('''SELECT enabled,emergency_stop,verified,channel_id,conversation_id
      FROM saas_ai_agent_connect_test_mode WHERE tenant_id=%s::uuid FOR SHARE''', (tenant,))
    row = cur.fetchone() or {}
    return bool(row.get('enabled') is True and row.get('verified') is True
                and row.get('emergency_stop') is not True
                and str(row.get('channel_id') or '') == str(channel_id)
                and str(row.get('conversation_id') or '') == str(conversation_id))


def allowed(job: dict) -> bool:
    if str(job.get('provider') or '') != 'connect':
        return False
    if not runtime_flag('AI_AGENTS_ENABLED'):
        return False
    tenant = str(job.get('tenant_id') or '')
    channel = str(job.get('channel_id') or '')
    conversation = str(job.get('conversation_id') or '')
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT s.enabled,s.emergency_stop,s.operating_mode,t.status
              FROM saas_ai_agent_settings s JOIN saas_tenants t ON t.id=s.tenant_id
              WHERE s.tenant_id=%s::uuid FOR SHARE OF s,t''', (tenant,))
            settings = cur.fetchone() or {}
            cur.execute('''SELECT 1 FROM saas_tenant_integrations i JOIN saas_connect_channels c
              ON c.tenant_id=i.tenant_id AND c.connect_channel_id=%s::uuid AND c.active=TRUE
              WHERE i.tenant_id=%s::uuid AND i.provider='connect' AND i.status='connected' FOR SHARE OF i,c''',
                        (channel, tenant))
            connected = bool(cur.fetchone())
            tenant_active = str(settings.get('status') or '') == 'active'
            if settings.get('operating_mode') == 'active':
                result = bool(tenant_active and connected and settings.get('enabled') is True
                              and settings.get('emergency_stop') is not True)
            else:
                result = bool(tenant_active and connected and settings.get('enabled') is True and settings.get('emergency_stop') is not True
                              and test_allowed(cur, tenant, channel, conversation))
        conn.commit()
    return result


def enqueue(tenant: str, *, channel_id: str, conversation_id: str, lead_id: int,
            message_id: str, event_id: str, text: str, message_type: str = 'text') -> int:
    """Queue one inbound Connect message for all matching tenant agents.

    The webhook event table + job primary key make Connect retries and Evolution
    duplicate events harmless.  No model call occurs in this transaction.
    """
    fingerprint = hashlib.sha256(('connect:' + str(message_id or event_id)).encode('utf-8')).hexdigest()
    queued = 0
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('SELECT operating_mode FROM saas_ai_agent_settings WHERE tenant_id=%s::uuid', (tenant,))
            test_mode = str((cur.fetchone() or {}).get('operating_mode') or 'test') != 'active'
            payload = {'message_id': str(message_id or event_id), 'channel_id': str(channel_id),
                       'conversation_id': str(conversation_id), 'text': str(text or '')[:8000],
                       'message_type': str(message_type or 'text')[:40], 'test_mode': test_mode}
            cur.execute('''INSERT INTO saas_tenant_webhook_events
              (tenant_id,fingerprint,provider,external_id,event_type,payload,state,available_at)
              VALUES(%s::uuid,%s,'connect',%s,'incoming_message',%s::jsonb,'queued',now())
              ON CONFLICT(tenant_id,fingerprint) DO NOTHING RETURNING fingerprint''',
                        (tenant, fingerprint, str(message_id or event_id), _json(payload)))
            if not cur.fetchone():
                conn.commit()
                return 0
            if not allowed({'provider':'connect','tenant_id':tenant,'channel_id':channel_id,'conversation_id':conversation_id}):
                reason = 'ai_agents_server_disabled' if not runtime_flag('AI_AGENTS_ENABLED') else 'connect_admission_blocked'
                cur.execute("UPDATE saas_tenant_webhook_events SET state='skipped',completed_at=now(),error_code=%s WHERE tenant_id=%s::uuid AND fingerprint=%s",
                            (reason, tenant, fingerprint))
                conn.commit()
                return 0
            cur.execute('''UPDATE saas_tenant_webhook_events SET state='superseded',completed_at=now(),error_code='newer_inbound'
              WHERE tenant_id=%s::uuid AND provider='connect' AND state IN ('queued','processing')
                AND payload->>'conversation_id'=%s AND fingerprint<>%s''', (tenant, conversation_id, fingerprint))
            cur.execute('''SELECT id,scopes,delay_seconds,enabled FROM saas_ai_agents
              WHERE tenant_id=%s::uuid ORDER BY created_at,id''', (tenant,))
            tenant_agents = cur.fetchall()
            enabled_agents = [row for row in tenant_agents if row.get('enabled') is True]
            agents = [row for row in enabled_agents if scope_matches(row.get('scopes'), channel_id)]
            if not agents:
                reason = 'agent_not_found' if not tenant_agents else 'agent_disabled' if not enabled_agents else 'no_connect_scope'
                cur.execute("UPDATE saas_tenant_webhook_events SET state='skipped',completed_at=now(),error_code=%s WHERE tenant_id=%s::uuid AND fingerprint=%s", (reason, tenant, fingerprint))
            for agent in agents:
                delay = max(0, min(int(agent.get('delay_seconds') or 0), 300))
                cur.execute('''INSERT INTO saas_ai_conversation_state
                  (tenant_id,provider,conversation_id,agent_id,state,first_inbound_message_id,last_inbound_message_id)
                  VALUES(%s::uuid,'connect',%s,%s::uuid,'active',%s,%s)
                  ON CONFLICT(tenant_id,provider,conversation_id) DO UPDATE SET
                    agent_id=EXCLUDED.agent_id,last_inbound_message_id=EXCLUDED.last_inbound_message_id,
                    revision=saas_ai_conversation_state.revision+1,updated_at=now()
                  WHERE saas_ai_conversation_state.state='active' ''',
                            (tenant, conversation_id, agent['id'], payload['message_id'], payload['message_id']))
                cur.execute('''INSERT INTO saas_ai_agent_jobs
                  (tenant_id,agent_id,provider,conversation_id,lead_id,talk_id,chat_id,channel_id,event_fingerprint,due_at,state)
                  VALUES(%s::uuid,%s::uuid,'connect',%s,%s,'',%s,%s,%s,now()+(%s*interval '1 second'),'queued')
                  ON CONFLICT(tenant_id,agent_id,provider,conversation_id) DO UPDATE SET
                    lead_id=EXCLUDED.lead_id,chat_id=EXCLUDED.chat_id,channel_id=EXCLUDED.channel_id,
                    event_fingerprint=EXCLUDED.event_fingerprint,revision=saas_ai_agent_jobs.revision+1,
                    state='queued',due_at=EXCLUDED.due_at,lease_token=NULL,lease_until=NULL,attempts=0,error_code='',updated_at=now()''',
                            (tenant, agent['id'], conversation_id, int(lead_id), conversation_id, channel_id, fingerprint, delay))
                queued += 1
        conn.commit()
    return queued


def history(tenant: str, lead_id: int, channel_id: str) -> list[dict]:
    """Read tenant-local Connect history; never falls back to Kommo."""
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT external_id,direction,body,message_type,media_url,happened_at
              FROM saas_crm_messages WHERE tenant_id=%s::uuid AND kommo_lead_id=%s
                AND channel=%s ORDER BY happened_at,id LIMIT 80''', (tenant, int(lead_id), 'connect:' + channel_id))
            rows = cur.fetchall()
    return [{'external_id': str(row.get('external_id') or '').removeprefix('connect:'),
             'direction': str(row.get('direction') or ''), 'body': str(row.get('body') or ''),
             'message_type': str(row.get('message_type') or 'text'), 'media_url': str(row.get('media_url') or ''),
             'has_attachment': bool(row.get('media_url')) or str(row.get('message_type') or 'text') != 'text',
             'happened_at': row['happened_at'].isoformat() if row.get('happened_at') else '',
             'channel': channel_id} for row in rows]


def mark_operator_reply(tenant: str, lead_id: int, channel_id: str, conversation_id: str) -> None:
    """Human replies stop pending Connect work before it can send."""
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''UPDATE saas_ai_conversation_state SET state='human',reason='manager_replied',
              revision=revision+1,updated_at=now() WHERE tenant_id=%s::uuid AND provider='connect'
                AND conversation_id=%s AND state='active' ''', (tenant, conversation_id))
            cur.execute('''UPDATE saas_ai_agent_jobs SET state='skipped',lease_token=NULL,lease_until=NULL,
              error_code='manager_replied',updated_at=now() WHERE tenant_id=%s::uuid AND provider='connect'
                AND conversation_id=%s AND state IN ('queued','processing')''', (tenant, conversation_id))
        conn.commit()
