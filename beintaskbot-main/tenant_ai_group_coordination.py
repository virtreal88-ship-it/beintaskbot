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


def lock_group(cur, tenant, conversation):
    if group_jid(conversation):
        cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                    ('ai-group:' + json.dumps([str(tenant), 'connect', conversation]),))


def read(cur, tenant, conversation):
    cur.execute("SELECT * FROM saas_ai_group_coordination WHERE tenant_id=%s::uuid AND provider='connect' AND group_jid=%s", (tenant, conversation))
    return dict(cur.fetchone() or {})


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
    cur.execute("SELECT 1 FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='connect' AND conversation_id=%s AND channel_id=''", (tenant, conversation))
    if cur.fetchone():
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
        with conn.cursor() as cur:
            tenant, actor = _authorize(cur, profile)
            lock_group(cur, tenant, conversation)
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
