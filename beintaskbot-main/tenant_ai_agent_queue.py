"""Durable, deduplicated Kommo inbound events and per-conversation drafts."""
import hashlib
import re
from urllib.parse import urlsplit

from tenant_ai_agents_schema import ensure_schema
from tenant_platform import TenantPlatformError, _connect, _json
from tenant_ai_agent_admission import allowed_event


def _account_domain(data, fallback=''):
    account = data.get('account') if isinstance(data.get('account'), dict) else {}
    value = account.get('subdomain') or account.get('domain') or data.get('account[subdomain]') or data.get('account[domain]') or ''
    raw = str(value).strip().lower()
    if not raw:
        raw = str(fallback or '').strip().lower()
    if not raw:
        return ''
    parsed = urlsplit(raw if '://' in raw else 'https://' + raw)
    host = (parsed.hostname or '').rstrip('.')
    if '.' not in host and re.fullmatch(r'[a-z0-9-]{1,63}', host):
        host += '.kommo.com'
    if not host.endswith('.kommo.com') or not re.fullmatch(r'[a-z0-9.-]+\.kommo\.com', host):
        return ''
    return host


def _scope_matches(scopes, pipeline, stage, channel):
    if not isinstance(scopes, list):
        return False
    for scope in scopes:
        if not isinstance(scope, dict) or scope.get('provider', 'kommo') != 'kommo':
            continue
        if str(scope.get('pipeline_id') or '') != str(pipeline) or str(scope.get('stage_id') or '') != str(stage):
            continue
        selected_channel = str(scope.get('channel_id') or '').strip().casefold()
        if not selected_channel or selected_channel == str(channel or '').strip().casefold():
            return True
    return False


def ingest_kommo_messages(data, rows, *, fallback_domain='', with_reason=False):
    """Commit eligible incoming event(s) before the existing webhook returns 2xx.

    Only identifiers and source metadata are queued. The worker re-reads the
    exact Talk over the existing tenant OAuth connection before using content.
    """
    def result(count, reason):
        return (count, reason) if with_reason else count

    domain = _account_domain(data if isinstance(data, dict) else {}, fallback_domain)
    bound_domain = _account_domain({}, fallback_domain)
    if bound_domain and domain != bound_domain:
        return result(0, 'account_domain_mismatch')
    if not domain or not isinstance(rows, list):
        return result(0, 'invalid_event')
    normalized = []
    for row in rows:
        if not isinstance(row, dict) or str(row.get('type') or 'incoming').strip().lower() == 'outgoing':
            continue
        if str(row.get('entity_type') or row.get('element_type') or 'lead').strip().lower() not in {'lead', 'leads', '2', ''}:
            continue
        try:
            lead_id = int(row.get('entity_id') or row.get('element_id') or 0)
            talk_id = str(int(row.get('talk_id') or 0))
        except (TypeError, ValueError):
            continue
        message_id = str(row.get('id') or row.get('msgid') or row.get('message_id') or '').strip()[:500]
        chat_id = str(row.get('chat_id') or '').strip()[:160]
        try:
            contact_id = int(row.get('contact_id') or row.get('contactId') or 0)
        except (TypeError, ValueError):
            continue
        if lead_id <= 0 or contact_id <= 0 or int(talk_id) <= 0 or not chat_id or not message_id:
            continue
        normalized.append({'lead_id': lead_id, 'talk_id': talk_id, 'chat_id': chat_id,
                           'contact_id': contact_id,
                           'message_id': message_id,
                           'channel': str(row.get('origin') or '').strip().lower()[:120],
                           'created_at': str(row.get('created_at') or '')[:40]})
    if not normalized:
        return result(0, 'invalid_event')
    queued = 0
    reasons = set()
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT i.tenant_id,t.status AS tenant_status,cfg.auto_send_enabled FROM saas_tenant_integrations i
              JOIN saas_tenants t ON t.id=i.tenant_id
              JOIN saas_ai_agent_settings cfg ON cfg.tenant_id=i.tenant_id AND cfg.enabled=TRUE
                AND cfg.auto_send_enabled=TRUE AND cfg.emergency_stop=FALSE
                AND (cfg.operating_mode IS NULL OR cfg.operating_mode='active')
              WHERE i.provider='kommo' AND i.status='connected' AND i.account_domain=%s
              FOR SHARE OF i,t''', (domain,))
            connections = cur.fetchall()
            if len(connections) > 1:
                raise TenantPlatformError('Ambiguous Kommo account mapping')
            if not connections or connections[0]['tenant_status'] != 'active':
                return result(0, 'company_settings_unavailable')
            tenant = str(connections[0]['tenant_id'])
            cur.execute('''SELECT a.id,a.scopes,a.delay_seconds,a.mode,a.schedule FROM saas_ai_agents a
              WHERE a.role='primary' AND a.tenant_id=%s::uuid AND a.enabled=TRUE ORDER BY a.created_at,a.id''', (tenant,))
            agents = cur.fetchall()
            # Customer messages reach the worker only when the company owner
            # and server permit auto-send; Manual agents stay staff-initiated.
            agents = [agent for agent in agents if agent.get('mode') == 'automatic']
            for event in normalized:
                if not allowed_event(cur,tenant,domain,event):
                    reasons.add('company_or_integration_blocked')
                    continue
                # Webhook payloads do not consistently include channel/origin.
                # Queue stage/channel candidates and let the worker verify the
                # exact Talk and live deal against Kommo before using the AI.
                matches = [agent for agent in agents if isinstance(agent['scopes'],list) and any(
                    isinstance(scope,dict) and scope.get('provider', 'kommo') == 'kommo' for scope in agent['scopes'])]
                if not matches:
                    reasons.add('no_enabled_agent')
                fingerprint = hashlib.sha256(('kommo:' + event['message_id']).encode('utf-8')).hexdigest()
                payload = {key: event[key] for key in ('lead_id','contact_id','talk_id','chat_id','message_id','channel','created_at')}
                cur.execute('''INSERT INTO saas_tenant_webhook_events
                  (tenant_id,fingerprint,provider,external_id,event_type,payload,state,available_at)
                  VALUES(%s::uuid,%s,'kommo',%s,'incoming_message',%s::jsonb,'queued',now())
                  ON CONFLICT(tenant_id,fingerprint) DO NOTHING RETURNING fingerprint''',
                  (tenant, fingerprint, event['message_id'], _json(payload)))
                if not cur.fetchone():
                    reasons.add('duplicate_event')
                    continue
                from tenant_ai_pilot import processing_allowed
                if not processing_allowed({'provider':'kommo','tenant_id':tenant,'channel_id':event['channel'],
                        'conversation_id':event['chat_id'],'event_fingerprint':fingerprint}, connection=conn):
                    cur.execute("UPDATE saas_tenant_webhook_events SET state='skipped',completed_at=now(),error_code='pilot_scope_blocked' WHERE tenant_id=%s::uuid AND fingerprint=%s",
                        (tenant, fingerprint))
                    reasons.add('pilot_scope_blocked')
                    continue
                cur.execute('''UPDATE saas_tenant_webhook_events SET state='superseded',completed_at=now()
                  WHERE tenant_id=%s::uuid AND provider='kommo' AND state IN ('queued','processing')
                    AND payload->>'chat_id'=%s AND fingerprint<>%s''', (tenant,event['chat_id'],fingerprint))
                from tenant_ai_agent_schedule import current_candidates
                matches = current_candidates(matches)
                matched_ids = {str(agent['id']) for agent in matches}
                cur.execute('''SELECT agent_id FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND provider='kommo'
                  AND conversation_id=%s AND state IN ('queued','processing')''', (tenant,event['chat_id']))
                previous_jobs = cur.fetchall()
                for old_job in previous_jobs:
                    if str(old_job['agent_id']) not in matched_ids:
                        cur.execute('''UPDATE saas_ai_agent_jobs SET state='skipped',revision=revision+1,
                          event_fingerprint=%s,error_code='new_message_out_of_scope',lease_token=NULL,lease_until=NULL,
                          updated_at=now() WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND provider='kommo' AND conversation_id=%s''',
                          (fingerprint,tenant,old_job['agent_id'],event['chat_id']))
                if not matches:
                    cur.execute("UPDATE saas_tenant_webhook_events SET state='skipped',completed_at=now(),error_code='no_matching_agent' WHERE tenant_id=%s::uuid AND fingerprint=%s",
                                (tenant,fingerprint))
                    continue
                for agent in matches:
                    delay = max(40, min(int(agent['delay_seconds']), 300))
                    cur.execute('''INSERT INTO saas_ai_conversation_state
                      (tenant_id,provider,conversation_id,agent_id,state,first_inbound_message_id,last_inbound_message_id)
                      VALUES(%s::uuid,'kommo',%s,%s::uuid,'active',%s,%s)
                      ON CONFLICT(tenant_id,provider,channel_id,conversation_id) DO UPDATE SET
                        agent_id=EXCLUDED.agent_id,
                        last_inbound_message_id=EXCLUDED.last_inbound_message_id,
                        first_inbound_message_id=CASE WHEN saas_ai_conversation_state.first_inbound_message_id='' THEN EXCLUDED.first_inbound_message_id ELSE saas_ai_conversation_state.first_inbound_message_id END,
                        revision=saas_ai_conversation_state.revision+1,updated_at=now()
                      WHERE saas_ai_conversation_state.state='active' ''',
                      (tenant,event['chat_id'],agent['id'],event['message_id'],event['message_id']))
                    cur.execute('''INSERT INTO saas_ai_agent_jobs
                      (tenant_id,agent_id,provider,conversation_id,lead_id,talk_id,chat_id,channel_id,
                       event_fingerprint,due_at,state)
                      VALUES(%s::uuid,%s,'kommo',%s,%s,%s,%s,%s,%s,now()+(%s*interval '1 second'),'queued')
                      ON CONFLICT(tenant_id,agent_id,provider,scope_channel_id,conversation_id) DO UPDATE SET
                        lead_id=EXCLUDED.lead_id,talk_id=EXCLUDED.talk_id,chat_id=EXCLUDED.chat_id,
                        channel_id=EXCLUDED.channel_id,event_fingerprint=EXCLUDED.event_fingerprint,
                        revision=saas_ai_agent_jobs.revision+1,state='queued',due_at=EXCLUDED.due_at,
                        lease_token=NULL,lease_until=NULL,attempts=0,error_code='',updated_at=now()''',
                      (tenant, agent['id'], event['chat_id'], event['lead_id'], event['talk_id'], event['chat_id'],
                       event['channel'], fingerprint, delay))
                    queued += 1
        if queued:
            reasons.add('queued')
        conn.commit()
    reason_order = ('queued', 'no_enabled_agent', 'duplicate_event')
    reason = next((item for item in reason_order if item in reasons), 'no_eligible_event')
    return result(queued, reason)


def observe_kommo_outgoing(data, rows, *, fallback_domain=''):
    """Pause on a manager message and reconcile a matched agent send receipt."""
    domain=_account_domain(data if isinstance(data,dict) else {},fallback_domain)
    if not domain or not isinstance(rows,list):
        return 0
    outgoing=[]
    for row in rows:
        if not isinstance(row,dict) or str(row.get('type') or '').strip().lower()!='outgoing':
            continue
        chat=str(row.get('chat_id') or '').strip()[:160]
        ident=str(row.get('id') or row.get('msgid') or row.get('message_id') or '').strip()[:500]
        try:
            event={'lead_id':int(row.get('entity_id') or row.get('element_id') or 0),
              'contact_id':int(row.get('contact_id') or row.get('contactId') or 0),
              'talk_id':str(int(row.get('talk_id') or 0)),'chat_id':chat,
              'channel':str(row.get('origin') or '').strip().lower()}
        except (TypeError,ValueError):
            continue
        if chat and ident and event['lead_id']>0 and event['contact_id']>0 and int(event['talk_id'])>0 and event['channel']:
            outgoing.append((event,ident,str(row.get('text') or '')[:8000]))
    if not outgoing:
        return 0
    changed=0
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT i.tenant_id FROM saas_tenant_integrations i JOIN saas_tenants t ON t.id=i.tenant_id
              JOIN saas_ai_agent_settings s ON s.tenant_id=i.tenant_id AND s.enabled=TRUE
              WHERE i.provider='kommo' AND i.status='connected' AND i.account_domain=%s AND t.status='active'
              FOR SHARE OF i,t''',(domain,))
            tenants=cur.fetchall()
            if len(tenants)>1:
                raise TenantPlatformError('Ambiguous Kommo account mapping')
            if not tenants:
                return 0
            tenant=str(tenants[0]['tenant_id'])
            for event,message_id,text in outgoing:
                if not allowed_event(cur,tenant,domain,event):
                    continue
                chat=event['chat_id']
                from tenant_ai_dialog_scope import lock_dialog
                lock_dialog(cur,tenant,'kommo','',chat)
                # A webhook can race the POST response. Exact body + chat + a
                # sending receipt is used only to avoid mistaking our own reply
                # for a human takeover; arbitrary outgoing messages still pause.
                digest=hashlib.sha256(text.strip().encode('utf-8')).hexdigest()
                cur.execute('''SELECT request_id,state,route FROM saas_chat_send_receipts WHERE tenant_id=%s::uuid
                  AND actor_id=0 AND (message_id=%s OR (state IN ('sending','unknown') AND route->>'chat_id'=%s AND route->>'text_hash'=%s))
                  ORDER BY created_at DESC LIMIT 1 FOR UPDATE''',(tenant,message_id,chat,digest))
                receipt=cur.fetchone()
                if receipt:
                    unresolved=receipt['state'] in {'sending','unknown'}
                    cur.execute("UPDATE saas_chat_send_receipts SET state='accepted',message_id=%s,updated_at=now() WHERE tenant_id=%s::uuid AND actor_id=0 AND request_id=%s::uuid AND state IN ('sending','unknown')",(message_id,tenant,receipt['request_id']))
                    route=receipt.get('route') or {}
                    if route.get('event_fingerprint'):
                        cur.execute("UPDATE saas_ai_agent_runs SET state='completed',result=jsonb_build_object('send_state','accepted','message_id',%s),updated_at=now() WHERE tenant_id=%s::uuid AND event_fingerprint=%s AND revision=%s AND state IN ('generating','unknown')",(message_id,tenant,route['event_fingerprint'],route.get('revision')))
                        cur.execute("UPDATE saas_ai_agent_jobs SET state='sent',lease_token=NULL,lease_until=NULL,error_code='',updated_at=now() WHERE tenant_id=%s::uuid AND event_fingerprint=%s AND revision=%s AND state IN ('processing','unknown')",(tenant,route['event_fingerprint'],route.get('revision')))
                        cur.execute("UPDATE saas_tenant_webhook_events SET state='completed',completed_at=now(),error_code='' WHERE tenant_id=%s::uuid AND fingerprint=%s",(tenant,route['event_fingerprint']))
                    cur.execute("UPDATE saas_ai_conversation_state SET last_agent_message_id=%s,updated_at=now() WHERE tenant_id=%s::uuid AND provider='kommo' AND conversation_id=%s",(message_id,tenant,chat))
                    # Recover the atomic reply-and-stop transition if Kommo
                    # accepted the POST but the worker died before committing it.
                    if unresolved and route.get('handoff_after_reply'):
                        cur.execute("UPDATE saas_ai_conversation_state SET state='human',reason='trigger_reply_sent_then_handoff',revision=revision+1,updated_at=now() WHERE tenant_id=%s::uuid AND provider='kommo' AND conversation_id=%s AND channel_id='' AND state='active' AND revision=%s RETURNING revision",(tenant,chat,route.get('conversation_revision')))
                        if cur.fetchone():
                            cur.execute("UPDATE saas_ai_agent_runs SET state='superseded',updated_at=now() WHERE tenant_id=%s::uuid AND state='generating' AND event_fingerprint IN (SELECT event_fingerprint FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND provider='kommo' AND conversation_id=%s)",(tenant,tenant,chat))
                            cur.execute("UPDATE saas_tenant_webhook_events SET state='skipped',completed_at=now(),error_code='human_handoff' WHERE tenant_id=%s::uuid AND fingerprint IN (SELECT event_fingerprint FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND provider='kommo' AND conversation_id=%s AND state IN ('queued','processing'))",(tenant,tenant,chat))
                            cur.execute("UPDATE saas_ai_agent_jobs SET state='skipped',revision=revision+1,lease_token=NULL,lease_until=NULL,error_code='human_handoff',updated_at=now() WHERE tenant_id=%s::uuid AND provider='kommo' AND conversation_id=%s AND state IN ('queued','processing')",(tenant,chat))
                    changed+=1
                    continue
                cur.execute('''UPDATE saas_ai_conversation_state SET state='human',reason='manager_replied',revision=revision+1,updated_at=now()
                  WHERE tenant_id=%s::uuid AND provider='kommo' AND conversation_id=%s AND state='active' RETURNING revision''',(tenant,chat))
                if cur.fetchone():
                    cur.execute('''UPDATE saas_ai_agent_runs SET state='superseded',updated_at=now() WHERE tenant_id=%s::uuid
                      AND event_fingerprint IN (SELECT event_fingerprint FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND provider='kommo' AND conversation_id=%s)
                      AND state='generating' ''',(tenant,tenant,chat))
                    cur.execute("UPDATE saas_tenant_webhook_events SET state='skipped',completed_at=now(),error_code='manager_replied' WHERE tenant_id=%s::uuid AND fingerprint IN (SELECT event_fingerprint FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND provider='kommo' AND conversation_id=%s AND state IN ('queued','processing'))",(tenant,tenant,chat))
                    cur.execute("UPDATE saas_ai_agent_jobs SET state='skipped',revision=revision+1,lease_token=NULL,lease_until=NULL,error_code='manager_replied',updated_at=now() WHERE tenant_id=%s::uuid AND provider='kommo' AND conversation_id=%s AND state IN ('queued','processing')",(tenant,chat))
                    changed+=1
        conn.commit()
    return changed
