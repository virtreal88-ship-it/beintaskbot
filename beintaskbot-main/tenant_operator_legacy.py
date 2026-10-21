"""Conservative admin recovery; never queues work, resumes or merges states."""
import hashlib
import json
from tenant_platform import _connect
from tenant_linear_policy import TenantLinearError
from tenant_operator_access import access, assert_route
from tenant_operator_assignment import identity
from tenant_ai_agent_connect import scope_matches
from tenant_ai_dialog_scope import lock_dialog
from tenant_ai_agents_store import _audit


def admin(cur, profile):
    tenant, actor, policy = access(cur, profile, 'dialogs')
    if policy.profile.get('role') not in {'owner', 'admin'} or not policy.allows('ai_agents_manage'):
        raise TenantLinearError('Восстановление доступно администратору с правом управления AI.', 403)
    return tenant, actor, policy


def inspect(cur, profile, data, writing=False):
    tenant, actor, policy = admin(cur, profile)
    channel, conversation, lead = identity(data)
    if writing:
        lock_dialog(cur, tenant, 'connect', channel, conversation)
    route = assert_route(cur, tenant, policy, 'connect', conversation, channel, lead)
    cur.execute("SELECT * FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='connect' AND conversation_id=%s ORDER BY channel_id" + (' FOR UPDATE' if writing else ''), (tenant, conversation))
    states = [dict(r) for r in cur.fetchall()]
    legacy = next((r for r in states if r['channel_id'] == ''), None)
    result = {'allowed': False, 'reason': 'no_legacy_state', 'resume_required': True}
    if not legacy:
        return result, None, tenant, actor
    cur.execute('''SELECT c.connect_channel_id,c.lead_id,ch.name AS channel_name,ch.active AS channel_active
      FROM saas_connect_conversations c JOIN saas_connect_channels ch
        ON ch.tenant_id=c.tenant_id AND ch.connect_channel_id=c.connect_channel_id
      WHERE c.tenant_id=%s::uuid AND c.conversation_id=%s ORDER BY c.connect_channel_id''', (tenant, conversation))
    routes = [dict(r) for r in cur.fetchall()]
    # Counts are read-only provenance shown to the reviewer.  They are scoped
    # to the exact tenant, conversation, channel and persisted lead route.
    cur.execute('''SELECT c.connect_channel_id::text AS channel_id,
        count(m.id)::integer AS message_count,
        count(m.id) FILTER (WHERE m.direction='incoming')::integer AS incoming_count,
        count(m.id) FILTER (WHERE m.direction='outgoing')::integer AS outgoing_count
      FROM saas_connect_conversations c LEFT JOIN saas_crm_messages m
        ON m.tenant_id=c.tenant_id AND m.kommo_lead_id=c.lead_id
        AND m.channel='connect:'||c.connect_channel_id::text
      WHERE c.tenant_id=%s::uuid AND c.conversation_id=%s
      GROUP BY c.connect_channel_id ORDER BY c.connect_channel_id''',(tenant,conversation))
    message_counts={str(row['channel_id']):dict(row) for row in cur.fetchall()}
    cur.execute("SELECT channel_id,lead_id,agent_id,state FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND provider='connect' AND conversation_id=%s ORDER BY channel_id,agent_id", (tenant, conversation))
    jobs = [dict(r) for r in cur.fetchall()]
    cur.execute("""SELECT d.id,d.state,COALESCE(e.payload->>'channel_id','') AS channel_id FROM saas_ai_agent_drafts d
      JOIN saas_tenant_webhook_events e ON e.tenant_id=d.tenant_id AND e.fingerprint=d.source_fingerprint
      WHERE d.tenant_id=%s::uuid AND e.provider='connect' AND d.chat_id=%s
        AND d.state IN ('pending','sending','unknown') ORDER BY d.id""", (tenant, conversation))
    drafts = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT COALESCE(payload->>'channel_id','') AS channel_id,count(*)::integer AS event_count FROM saas_tenant_webhook_events WHERE tenant_id=%s::uuid AND provider='connect' AND payload->>'conversation_id'=%s GROUP BY 1 ORDER BY 1", (tenant, conversation))
    events = [dict(r) for r in cur.fetchall()]
    cur.execute('SELECT id,role,scopes FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid', (tenant, legacy.get('agent_id')))
    agent = dict(cur.fetchone() or {})
    cur.execute("SELECT resolved_channel,original->>'agent_id' AS agent_id FROM saas_ai_channel_legacy_state WHERE tenant_id=%s::uuid AND provider='connect' AND conversation_id=%s", (tenant, conversation))
    archive = dict(cur.fetchone() or {})
    reason = ''
    if conversation.endswith('@g.us') or route.get('conversation_kind') == 'group':
        reason = 'group_requires_review'
    elif len(routes) != 1:
        reason = 'multiple_channels'
    elif len(states) != 1:
        reason = 'conflicting_state'
    elif archive and (archive.get('resolved_channel') not in {'', channel} or archive.get('agent_id') != str(legacy.get('agent_id') or '')):
        reason = 'conflicting_evidence'
    elif not agent or agent.get('role') != 'primary' or not scope_matches(agent.get('scopes'), channel):
        reason = 'legacy_agent_scope_mismatch'
    elif not jobs:
        reason = 'insufficient_evidence'
    elif any(str(j['channel_id'] or '') != channel or j['lead_id'] != lead or str(j['agent_id']) != str(legacy['agent_id']) for j in jobs) or any(e['channel_id'] != channel for e in events):
        reason = 'conflicting_evidence'
    elif any(j['state'] not in {'sent', 'skipped', 'failed'} for j in jobs) or drafts:
        reason = 'outstanding_work'
    evidence = {'states': states, 'routes': routes, 'jobs': jobs, 'drafts': drafts, 'events': events,
                'messages': message_counts, 'agent': agent, 'archive': archive}
    token = hashlib.sha256(json.dumps(evidence, default=str, sort_keys=True).encode()).hexdigest()
    result.update(allowed=not reason, reason=reason or 'unique_verified_route', expected_token=token,
                  route_count=len(routes), scoped_state_count=len(states)-1,
                  legacy_state=legacy['state'], legacy_agent_id=str(legacy.get('agent_id') or ''),
                  channels=[{'channel_id':str(row['connect_channel_id']),'lead_id':int(row['lead_id']),
                    'name':str(row.get('channel_name') or ''),'active':row.get('channel_active') is True,
                    'message_count':int(message_counts.get(str(row['connect_channel_id']),{}).get('message_count') or 0),
                    'incoming_count':int(message_counts.get(str(row['connect_channel_id']),{}).get('incoming_count') or 0),
                    'outgoing_count':int(message_counts.get(str(row['connect_channel_id']),{}).get('outgoing_count') or 0),
                    'job_count':sum(str(job.get('channel_id') or '')==str(row['connect_channel_id']) for job in jobs),
                    'event_count':sum(str(event.get('channel_id') or '')==str(row['connect_channel_id']) for event in events),
                    'draft_count':sum(str(draft.get('channel_id') or '')==str(row['connect_channel_id']) for draft in drafts)} for row in routes],
                  evidence={'job_count':len(jobs),'draft_count':len(drafts),'event_channel_count':len(events),
                    'route_count':len(routes),'message_count':sum(int(x.get('message_count') or 0) for x in message_counts.values())})
    return result, legacy, tenant, actor


def options(profile, data):
    with _connect() as conn:
        with conn.cursor() as cur:
            return inspect(cur, profile, data)[0]


def recover(profile, data):
    if data.get('confirmed') is not True or not data.get('expected_token'):
        raise TenantLinearError('Требуется явное подтверждение администратора после проверки.', 409)
    channel, conversation, _ = identity(data)
    with _connect() as conn:
        # Authorize before requesting global evidence locks. Recheck membership
        # inside the write transaction; its snapshot must start after the locks.
        with conn.cursor() as cur:
            admin(cur, profile)
        conn.commit()
        with conn.cursor() as cur:
            # Reject concurrent changes to evidence instead of guessing. No retries
            # with a refreshed token: the administrator must review it again.
            cur.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE')
            cur.execute("SET LOCAL lock_timeout = '1500ms'")
            cur.execute("SET LOCAL statement_timeout = '5000ms'")
            # Existing ingestion uses READ COMMITTED. Prevent phantom routes or
            # outstanding work during this rare, short administrative transaction.
            # Lock before any row/advisory locks; contention fails closed.
            cur.execute('LOCK TABLE saas_connect_conversations,saas_crm_messages,saas_ai_agent_jobs,saas_ai_agent_drafts,saas_tenant_webhook_events IN SHARE MODE')
            result, legacy, tenant, actor = inspect(cur, profile, data, writing=True)
            if not result['allowed'] and result['reason']=='no_legacy_state':
                # A retried confirmation after a committed recovery is a no-op.
                # Require the same tenant/conversation/channel and the explicit
                # audit snapshot created by this recovery path.
                cur.execute('''SELECT s.state,s.revision FROM saas_ai_channel_legacy_state a
                  JOIN saas_ai_conversation_state s ON s.tenant_id=a.tenant_id AND s.provider='connect'
                    AND s.channel_id=%s AND s.conversation_id=a.conversation_id
                  WHERE a.tenant_id=%s::uuid AND a.provider='connect' AND a.conversation_id=%s
                    AND a.resolved_channel=%s AND a.reason='admin_verified_unique_route' ''',
                  (channel,tenant,conversation,channel))
                prior=cur.fetchone()
                if prior:
                    conn.commit()
                    return {'state':prior['state'],'revision':int(prior['revision']),'recovered':True,
                            'resume_required':True,'replayed':True}
            if not result['allowed'] or result['expected_token'] != data['expected_token']:
                raise TenantLinearError('Связь неоднозначна или данные изменились. Обновите диагностику.', 409)
            cur.execute('''INSERT INTO saas_ai_channel_legacy_state(tenant_id,provider,conversation_id,original,reason)
              VALUES(%s::uuid,'connect',%s,%s::jsonb,'admin_verified_unique_route') ON CONFLICT DO NOTHING''',
                        (tenant, conversation, json.dumps(legacy, default=str)))
            # Preserve the archived original and all inbound/outbound identifiers.
            # Active legacy state becomes paused; Human/Pause is never lifted.
            cur.execute('''UPDATE saas_ai_conversation_state SET channel_id=%s,
              state=CASE WHEN state='active' THEN 'paused' ELSE state END,
              reason=CASE WHEN state='active' THEN 'legacy_recovered_requires_resume' ELSE reason END,
              revision=revision+1,updated_at=now()
              WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id='' AND conversation_id=%s
              RETURNING state,revision''', (channel, tenant, conversation))
            saved = dict(cur.fetchone())
            cur.execute("UPDATE saas_ai_channel_legacy_state SET resolved_channel=%s,reason='admin_verified_unique_route' WHERE tenant_id=%s::uuid AND provider='connect' AND conversation_id=%s", (channel, tenant, conversation))
            _audit(cur, tenant, actor, 'ai_legacy_channel_recovered', 'conversation', conversation,
                   {'channel_id': channel, 'previous_revision': legacy['revision'], 'state': saved['state'], 'evidence_token': result['expected_token']})
        conn.commit()
    return {**saved, 'recovered': True, 'resume_required': True}
