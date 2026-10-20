"""Idempotent automatic Kommo replies with a fenced human-handoff option."""
import asyncio
import hashlib
import json
from datetime import datetime, timezone

from tenant_ai_agents_schema import ensure_schema
from tenant_ai_dialog_scope import lock_dialog
from tenant_chat_send_schema import ensure_schema as ensure_receipt_schema
from tenant_linear_policy import TenantLinearError
from tenant_platform import _connect, _json


def _blocked(reason):
    error = TenantLinearError('AI cavabı göndərilmədi; dialoq vəziyyətini yoxlayın.', 409)
    error.ai_agent_send_phase = 'final_guard'
    error.ai_agent_send_reason = reason
    return error


async def send_agent(job, reservation, text, kommo_request, wait_slot, logger):
    """Send once, and commit accepted state/handoff only after provider acceptance.

    A durable `sending` receipt is committed before the external POST. Unknown
    outcomes are never retried automatically; a matching Kommo echo reconciles
    the receipt after a worker crash.
    """
    del wait_slot  # The shared tenant Kommo request applies its own rate limit.
    if job.get('provider') != 'kommo':
        raise _blocked('provider_scope_changed')
    from tenant_ai_pilot import automatic_send_allowed
    if not automatic_send_allowed({**job, 'route': reservation.get('route') or {}}):
        raise _blocked('pilot_scope_or_competing_handler')
    from tenant_ai_agent_runtime_flags import enabled as runtime_flag
    if not runtime_flag('AI_AGENTS_AUTO_SEND_ENABLED'):
        raise _blocked('automatic_send_disabled')
    tenant, chat = str(job['tenant_id']), str(job['chat_id'])
    text = str(text or '')[:8000]
    run_id = str(reservation['run_id'])
    route = dict(reservation.get('route') or {})
    source_id = str(route.get('message_id') or '')
    route.update({'event_fingerprint': str(job['event_fingerprint']),
                  'revision': int(job['revision']), 'run_id': run_id,
                  'handoff_after_reply': bool(route.get('handoff_after_reply')),
                  'text_hash': hashlib.sha256(text.strip().encode('utf-8')).hexdigest()})
    input_hash = hashlib.sha256(_json([tenant, job['agent_id'], job['event_fingerprint'],
                                      job['revision'], text]).encode('utf-8')).hexdigest()

    # Re-read the live Talk immediately before reserving the one permitted POST.
    from tenant_ai_agent_schedule import next_open
    from tenant_ai_agent_run_store import defer_job
    open_at = next_open(route.get('schedule') or {})
    if open_at:
        await asyncio.to_thread(defer_job, job, open_at, reservation)
        return {'state': 'deferred'}
    from tenant_chat_reader import read_messages
    live = await read_messages(tenant, {'talk_id': int(job['talk_id']), 'chat_id': chat}, kommo_request)
    try:
        source_at = datetime.fromisoformat(str(route.get('source_happened_at')).replace('Z', '+00:00'))
        if source_at.tzinfo is None:
            source_at = source_at.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        raise _blocked('source_time_unavailable')
    advanced = False
    for row in live or []:
        nested = row.get('message') if isinstance(row, dict) and isinstance(row.get('message'), dict) else {}
        ident = str(nested.get('id') or nested.get('msgid') or row.get('id') or row.get('msgid') or '')
        if not ident or ident == source_id:
            continue
        created = nested.get('created_at') or row.get('created_at') or nested.get('timestamp') or row.get('timestamp')
        try:
            stamp = datetime.fromtimestamp(int(created), timezone.utc) if str(created).isdigit() else datetime.fromisoformat(str(created).replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            advanced = advanced or stamp >= source_at
        except (TypeError, ValueError, OSError):
            advanced = True
    def _message_id(row):
        nested = row.get('message') if isinstance(row, dict) and isinstance(row.get('message'), dict) else {}
        return str(nested.get('id') or nested.get('msgid') or row.get('id') or row.get('msgid') or '')
    if not source_id or not any(_message_id(m) == source_id for m in live or []) or advanced:
        raise _blocked('conversation_advanced')

    ensure_conn = _connect
    # Reserve durably so an echo can recover an accepted send if the worker dies.
    with ensure_conn() as conn:
        ensure_schema(conn)
        ensure_receipt_schema(conn)
        with conn.cursor() as cur:
            lock_dialog(cur, tenant, 'kommo', '', chat)
            cur.execute('''SELECT j.state,j.lease_token,j.revision,s.enabled,s.operating_mode,s.auto_send_enabled,
                s.emergency_stop,s.updated_at AS settings_version,a.enabled AS agent_enabled,
                a.version AS agent_version,t.status AS tenant_status,i.updated_at AS config_version
              FROM saas_ai_agent_jobs j JOIN saas_ai_agents a ON a.tenant_id=j.tenant_id AND a.id=j.agent_id
              JOIN saas_ai_agent_settings s ON s.tenant_id=j.tenant_id JOIN saas_tenants t ON t.id=j.tenant_id
              LEFT JOIN saas_tenant_integrations i ON i.tenant_id=j.tenant_id AND i.provider='chat_ai'
              WHERE j.tenant_id=%s::uuid AND j.agent_id=%s::uuid AND j.provider='kommo'
                AND j.conversation_id=%s AND j.scope_channel_id='' FOR UPDATE OF j,a,s''',
              (tenant, job['agent_id'], chat))
            current = cur.fetchone()
            cur.execute("SELECT state,revision FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='kommo' AND conversation_id=%s AND channel_id='' FOR UPDATE", (tenant, chat))
            state = cur.fetchone()
            if (not current or current['state'] != 'processing' or current['lease_token'] != job['lease_token']
                    or current['revision'] != job['revision'] or not state or state['state'] != 'active'
                    or state['revision'] != reservation.get('conversation_revision')
                    or current['enabled'] is not True or current['agent_enabled'] is not True
                    or current['emergency_stop'] is True or current['auto_send_enabled'] is not True
                    or current['operating_mode'] != 'active' or current['tenant_status'] != 'active'
                    or str(current['settings_version']) != str(reservation.get('settings_version'))
                    or str(current['agent_version']) != str(reservation.get('agent_version'))
                    or str(current['config_version']) != str(reservation.get('config_version'))):
                raise _blocked('permission_or_revision_changed')
            cur.execute('''SELECT state,message_id FROM saas_chat_send_receipts
              WHERE tenant_id=%s::uuid AND actor_id=0 AND request_id=%s::uuid FOR UPDATE''', (tenant, run_id))
            receipt = cur.fetchone()
            if receipt:
                if receipt['state'] == 'accepted':
                    conn.commit()
                    return {'state': 'accepted', 'message_id': receipt['message_id'], 'cached': True}
                raise _blocked('send_receipt_' + str(receipt['state']))
            cur.execute('''INSERT INTO saas_chat_send_receipts
              (tenant_id,actor_id,request_id,lead_id,input_hash,state,route)
              VALUES(%s::uuid,0,%s::uuid,%s,%s,'sending',%s::jsonb)''',
              (tenant, run_id, int(job['lead_id']), input_hash, _json(route)))
        conn.commit()

    # Hold the dialogue and receipt locks across the final guard and single POST.
    # Incoming handoff/pause changes therefore linearize before or after this send.
    with ensure_conn() as conn:
        ensure_schema(conn)
        ensure_receipt_schema(conn)
        with conn.cursor() as cur:
            lock_dialog(cur, tenant, 'kommo', '', chat)
            cur.execute('''SELECT j.state,j.lease_token,j.revision,s.enabled,s.operating_mode,s.auto_send_enabled,
                s.emergency_stop,s.updated_at AS settings_version,a.enabled AS agent_enabled,
                a.version AS agent_version,t.status AS tenant_status,i.updated_at AS config_version
              FROM saas_ai_agent_jobs j JOIN saas_ai_agents a ON a.tenant_id=j.tenant_id AND a.id=j.agent_id
              JOIN saas_ai_agent_settings s ON s.tenant_id=j.tenant_id JOIN saas_tenants t ON t.id=j.tenant_id
              LEFT JOIN saas_tenant_integrations i ON i.tenant_id=j.tenant_id AND i.provider='chat_ai'
              WHERE j.tenant_id=%s::uuid AND j.agent_id=%s::uuid AND j.provider='kommo'
                AND j.conversation_id=%s AND j.scope_channel_id='' FOR UPDATE OF j,a,s''',
              (tenant, job['agent_id'], chat))
            current = cur.fetchone()
            cur.execute('''SELECT state,revision FROM saas_ai_conversation_state
              WHERE tenant_id=%s::uuid AND provider='kommo' AND conversation_id=%s AND channel_id='' FOR UPDATE''', (tenant, chat))
            state = cur.fetchone()
            cur.execute('''SELECT state FROM saas_chat_send_receipts WHERE tenant_id=%s::uuid
              AND actor_id=0 AND request_id=%s::uuid FOR UPDATE''', (tenant, run_id))
            receipt = cur.fetchone()
            if not receipt or receipt['state'] != 'sending':
                raise _blocked('send_receipt_changed')
            if (not current or current['state'] != 'processing' or current['lease_token'] != job['lease_token']
                    or current['revision'] != job['revision'] or current['enabled'] is not True
                    or current['agent_enabled'] is not True or current['emergency_stop'] is True
                    or current['auto_send_enabled'] is not True or current['operating_mode'] != 'active'
                    or current['tenant_status'] != 'active'
                    or str(current['settings_version']) != str(reservation.get('settings_version'))
                    or str(current['agent_version']) != str(reservation.get('agent_version'))
                    or str(current['config_version']) != str(reservation.get('config_version'))
                    or not state or state['state'] != 'active'
                    or state['revision'] != reservation.get('conversation_revision')):
                cur.execute("UPDATE saas_chat_send_receipts SET state='blocked',updated_at=now() WHERE tenant_id=%s::uuid AND actor_id=0 AND request_id=%s::uuid AND state='sending'", (tenant, run_id))
                conn.commit()
                raise _blocked('conversation_stopped')
            if not runtime_flag('AI_AGENTS_AUTO_SEND_ENABLED'):
                cur.execute("UPDATE saas_chat_send_receipts SET state='blocked',updated_at=now() WHERE tenant_id=%s::uuid AND actor_id=0 AND request_id=%s::uuid AND state='sending'", (tenant, run_id))
                conn.commit()
                raise _blocked('automatic_send_disabled')
            open_at = next_open(route.get('schedule') or {})
            if open_at:
                cur.execute("UPDATE saas_chat_send_receipts SET state='blocked',updated_at=now() WHERE tenant_id=%s::uuid AND actor_id=0 AND request_id=%s::uuid AND state='sending'", (tenant, run_id))
                conn.commit()
                await asyncio.to_thread(defer_job, job, open_at, reservation)
                return {'state': 'deferred'}
            try:
                response = await kommo_request(tenant, 'POST',
                    f"talks/{job['talk_id']}/send_message", json_body={'text': text})
                message_id = response.get('id') or response.get('message_id') if isinstance(response, dict) else None
                if not isinstance(message_id, (str, int)) or not str(message_id).strip():
                    raise RuntimeError('Provider acceptance has no message id')
                message_id = str(message_id).strip()[:500]
            except BaseException:
                # Keep a durable no-retry marker. Provider errors and cancellation
                # are not proof that the remote side rejected the message.
                cur.execute("UPDATE saas_chat_send_receipts SET state='unknown',updated_at=now() WHERE tenant_id=%s::uuid AND actor_id=0 AND request_id=%s::uuid AND state='sending'", (tenant, run_id))
                conn.commit()
                raise

            cur.execute("UPDATE saas_chat_send_receipts SET state='accepted',message_id=%s,updated_at=now() WHERE tenant_id=%s::uuid AND actor_id=0 AND request_id=%s::uuid AND state='sending'", (message_id, tenant, run_id))
            cur.execute('''INSERT INTO saas_crm_messages
              (id,tenant_id,kommo_lead_id,external_id,direction,channel,author_name,body,message_type,raw)
              VALUES(gen_random_uuid(),%s::uuid,%s,%s,'outgoing','kommo','AI Agent',%s,'text',%s::jsonb)
              ON CONFLICT(tenant_id,external_id) DO NOTHING''',
              (tenant, int(job['lead_id']), message_id, text,
               _json({'status': 'accepted', 'run_id': run_id, 'event_fingerprint': job['event_fingerprint']})))
            cur.execute("UPDATE saas_ai_conversation_state SET last_agent_message_id=%s,updated_at=now() WHERE tenant_id=%s::uuid AND provider='kommo' AND conversation_id=%s AND channel_id=''", (message_id, tenant, chat))
            cur.execute("UPDATE saas_ai_agent_runs SET state='completed',result=%s::jsonb,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND state='generating'", (_json({'send_state': 'accepted', 'message_id': message_id}), tenant, run_id))
            cur.execute("UPDATE saas_ai_agent_jobs SET state='sent',lease_token=NULL,lease_until=NULL,error_code='',updated_at=now() WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND provider='kommo' AND conversation_id=%s AND scope_channel_id='' AND lease_token=%s AND revision=%s", (tenant, job['agent_id'], chat, job['lease_token'], job['revision']))
            cur.execute("UPDATE saas_tenant_webhook_events SET state='completed',completed_at=now(),error_code='' WHERE tenant_id=%s::uuid AND fingerprint=%s", (tenant, job['event_fingerprint']))
            if route['handoff_after_reply']:
                cur.execute("UPDATE saas_ai_conversation_state SET state='human',reason='trigger_reply_sent_then_handoff',revision=revision+1,updated_at=now() WHERE tenant_id=%s::uuid AND provider='kommo' AND conversation_id=%s AND channel_id='' AND state='active'", (tenant, chat))
                cur.execute("UPDATE saas_ai_agent_runs SET state='superseded',updated_at=now() WHERE tenant_id=%s::uuid AND state='generating' AND event_fingerprint IN (SELECT event_fingerprint FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND provider='kommo' AND conversation_id=%s)", (tenant, tenant, chat))
                cur.execute("UPDATE saas_tenant_webhook_events SET state='skipped',completed_at=now(),error_code='human_handoff' WHERE tenant_id=%s::uuid AND fingerprint IN (SELECT event_fingerprint FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND provider='kommo' AND conversation_id=%s AND state IN ('queued','processing'))", (tenant, tenant, chat))
                cur.execute("UPDATE saas_ai_agent_jobs SET state='skipped',revision=revision+1,lease_token=NULL,lease_until=NULL,error_code='human_handoff',updated_at=now() WHERE tenant_id=%s::uuid AND provider='kommo' AND conversation_id=%s AND state IN ('queued','processing')", (tenant, chat))
                cur.execute('''INSERT INTO saas_tenant_notifications(id,tenant_id,telegram_id,event_key,title,body,target_url)
                  SELECT gen_random_uuid(),m.tenant_id,m.telegram_id,%s,'AI: əməkdaşın müdaxiləsi tələb olunur',%s,%s
                  FROM saas_tenant_members m WHERE m.tenant_id=%s::uuid AND m.active=TRUE AND m.role IN ('owner','admin')
                    AND NOT EXISTS (SELECT 1 FROM saas_tenant_notifications n WHERE n.tenant_id=m.tenant_id AND n.telegram_id=m.telegram_id AND n.event_key=%s)''',
                  ('ai_handoff:'+chat+':'+job['event_fingerprint'], 'AI cavabı göndərildi; əməkdaşın davam etməsi tələb olunur.', '/app?view=deals&deal_id='+str(job['lead_id']), tenant, 'ai_handoff:'+chat+':'+job['event_fingerprint']))
        conn.commit()
    logger.info('AI agent message accepted: tenant=%s agent=%s receipt=%s', tenant, job['agent_id'], 'accepted')
    return {'state': 'accepted', 'message_id': message_id}
