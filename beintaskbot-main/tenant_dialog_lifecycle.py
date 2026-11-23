"""Explicit, source-local operator episodes. Disabled until additive migration is reviewed.

No inference from support tickets, AI assignments, role, phone, or group membership.
"""
import hashlib
import json
import uuid
import time
from tenant_linear_policy import TenantLinearError


def enabled(cur, tenant):
    from tenant_dialog_rollout import guard
    return guard(cur, tenant).enforced


def acceptance_enabled(cur, tenant):
    from tenant_dialog_rollout import guard
    state = guard(cur, tenant)
    return state.enforced and state.admission_enabled


def migrate(cur):
    """Explicit deployment preflight only. No automatic DDL or business-row backfill."""
    from tenant_dialog_rollout import migrate as migrate_rollout
    migrate_rollout(cur)
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_dialog_episodes (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id), id UUID NOT NULL,
      channel_id UUID NOT NULL, conversation_id TEXT NOT NULL, lead_id BIGINT NOT NULL,
      accepted_by BIGINT NOT NULL, operator_id BIGINT NOT NULL,
      accepted_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), closed_at TIMESTAMPTZ,
      case_id UUID, PRIMARY KEY(tenant_id,id),
      FOREIGN KEY(tenant_id,channel_id,conversation_id) REFERENCES saas_connect_conversations(tenant_id,connect_channel_id,conversation_id),
      FOREIGN KEY(tenant_id,operator_id) REFERENCES saas_tenant_members(tenant_id,telegram_id))''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_dialog_workflows (
      tenant_id UUID NOT NULL,channel_id UUID NOT NULL,conversation_id TEXT NOT NULL,
      id UUID NOT NULL,state TEXT NOT NULL CHECK(state IN ('waiting','accepted','closed','dismissed')),
      operator_id BIGINT, revision BIGINT NOT NULL DEFAULT 0,
      closed_at TIMESTAMPTZ, last_inbound_id TEXT, updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
      PRIMARY KEY(tenant_id,channel_id,conversation_id),
      FOREIGN KEY(tenant_id,channel_id,conversation_id) REFERENCES saas_connect_conversations(tenant_id,connect_channel_id,conversation_id),
      CHECK((state='waiting' AND operator_id IS NULL) OR (state<>'waiting' AND operator_id IS NOT NULL)))''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_dialog_lifecycle_receipts (
      tenant_id UUID NOT NULL,request_id UUID NOT NULL,actor_id BIGINT NOT NULL,
      input_hash TEXT NOT NULL,result JSONB NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,request_id))''')
    cur.execute('''CREATE INDEX IF NOT EXISTS saas_dialog_episode_route_idx ON saas_dialog_episodes
      (tenant_id,channel_id,conversation_id,accepted_at)''')

    cur.execute("""CREATE TABLE IF NOT EXISTS saas_dialog_episode_messages (
      tenant_id UUID NOT NULL,episode_id UUID NOT NULL,message_id UUID NOT NULL,
      recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
      PRIMARY KEY(tenant_id,episode_id,message_id), UNIQUE(tenant_id,message_id),
      FOREIGN KEY(tenant_id,episode_id) REFERENCES saas_dialog_episodes(tenant_id,id),
      FOREIGN KEY(message_id) REFERENCES saas_crm_messages(id))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS saas_dialog_episode_sends (
      tenant_id UUID NOT NULL,episode_id UUID NOT NULL,channel_id UUID NOT NULL,
      conversation_id TEXT NOT NULL,request_id UUID NOT NULL,external_id TEXT NOT NULL,
      PRIMARY KEY(tenant_id,request_id),UNIQUE(tenant_id,channel_id,external_id),
      FOREIGN KEY(tenant_id,episode_id) REFERENCES saas_dialog_episodes(tenant_id,id))""")

    from tenant_dialog_presence import migrate as presence_migrate
    presence_migrate(cur)


def record_dispatch(cur, tenant, channel, conversation, request_id, result):
    """Only a verified successful provider result inside the final send lock."""
    if not enabled(cur, tenant): return
    row=_row(cur,tenant,channel,conversation)
    if not row or row['state']!='accepted': return
    external=(result.get('message_id') or result.get('external_message_id')) if isinstance(result,dict) else result
    if not isinstance(external,str) or not external: return
    if isinstance(result,dict) and result.get('provider_accepted') is False: return
    cur.execute("""INSERT INTO saas_dialog_episode_sends(tenant_id,episode_id,channel_id,
      conversation_id,request_id,external_id) VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s::uuid,%s)
      ON CONFLICT DO NOTHING""",(tenant,row['id'],channel,conversation,request_id,external.removeprefix('connect:')))


def record_message(cur, tenant, channel, conversation, message_id):
    """Bind at durable message admission under route lock, never by timestamps.

    A send dispatched in an older episode stays there even if its local receipt
    is stored after close or after another episode has already been accepted.
    """
    if not enabled(cur, tenant): return
    from tenant_connect_dialog_equivalence import members, canonical
    conversation=canonical(cur,tenant,channel,conversation)
    leads=list(members(cur,tenant,channel,conversation).values())
    cur.execute("""SELECT id,external_id,direction FROM saas_crm_messages
      WHERE tenant_id=%s::uuid AND id=%s::uuid AND channel=%s
        AND kommo_lead_id=ANY(%s::bigint[])""",(tenant,message_id,'connect:'+channel,leads))
    message=cur.fetchone()
    if not message: return
    episode_id=None
    if message['direction']=='outgoing':
        cur.execute("""SELECT episode_id FROM saas_dialog_episode_sends WHERE tenant_id=%s::uuid
          AND channel_id=%s::uuid AND conversation_id=%s AND external_id=%s""",
          (tenant,channel,conversation,str(message['external_id']).removeprefix('connect:')))
        sent=cur.fetchone()
        if sent:episode_id=sent['episode_id']
        if episode_id is None:
            cur.execute("""SELECT e.id FROM saas_chat_send_receipts r
              JOIN saas_dialog_episodes e ON e.tenant_id=r.tenant_id
                AND e.id::text=r.route->>'lifecycle_episode_id'
              WHERE r.tenant_id=%s::uuid AND e.channel_id=%s::uuid
                AND e.conversation_id=%s AND r.lead_id=e.lead_id
                AND r.message_id IN (%s,%s) LIMIT 1""",
              (tenant,channel,conversation,str(message['external_id']),str(message['external_id']).removeprefix('connect:')))
            known=cur.fetchone()
            if known: episode_id=known['id']
        if episode_id is None:
            # Pre-rollout receipts have no episode to join. Their delayed echo
            # cannot be inferred to belong to the currently accepted episode.
            cur.execute("""SELECT 1 FROM saas_chat_send_receipts r
              WHERE r.tenant_id=%s::uuid AND r.lead_id=ANY(%s::bigint[])
                AND COALESCE(r.route->>'lifecycle_episode_id','')=''
                AND r.state IN ('preparing','sending','unknown') LIMIT 1""",(tenant,leads))
            if cur.fetchone():return
        if episode_id is None:
            # A late unknown send may belong to an older episode. Never guess
            # that an unmatched echo belongs to the currently accepted one.
            cur.execute("""SELECT 1 FROM saas_chat_send_receipts r
              JOIN saas_dialog_episodes e ON e.tenant_id=r.tenant_id
                AND e.id::text=r.route->>'lifecycle_episode_id'
              WHERE r.tenant_id=%s::uuid AND e.channel_id=%s::uuid
                AND e.conversation_id=%s AND r.lead_id=e.lead_id
                AND r.state IN ('preparing','sending','unknown') LIMIT 1""",(tenant,channel,conversation))
            if cur.fetchone(): return
    if episode_id is None:
        row=_row(cur,tenant,channel,conversation)
        if row and row['state']=='accepted':episode_id=row['id']
    if episode_id:
        cur.execute("""INSERT INTO saas_dialog_episode_messages(tenant_id,episode_id,message_id)
          VALUES(%s::uuid,%s::uuid,%s::uuid) ON CONFLICT DO NOTHING""",(tenant,episode_id,message_id))


def _row(cur, tenant, channel, conversation):
    from tenant_connect_dialog_equivalence import canonical
    conversation=canonical(cur,tenant,channel,conversation)
    cur.execute('''SELECT * FROM saas_dialog_workflows WHERE tenant_id=%s::uuid
      AND channel_id=%s::uuid AND conversation_id=%s''', (tenant, channel, conversation))
    return cur.fetchone()


def visibility_sql(cur, policy, provider_expr='routes.provider', channel_expr='routes.channel_id', conversation_expr='routes.conversation_id', *, allow_closed_archive=False, owner_overview=False):
    if not enabled(cur, policy.profile['tenant_id']): return 'TRUE'
    # The caller's independent tenant/source policy still gates every row.
    if owner_overview and policy.profile.get('role')=='owner': return 'TRUE'
    tenant = str(uuid.UUID(str(policy.profile['tenant_id'])))
    actor = int(policy.profile['telegram_id'])
    from tenant_dialog_presence import is_online, work_status_routing_sources
    sources = work_status_routing_sources(tenant)
    private_states = "('accepted')" if allow_closed_archive else "('accepted','closed','dismissed')"
    ownership = f"""NOT EXISTS(SELECT 1 FROM saas_dialog_workflows dw
      WHERE dw.tenant_id='{tenant}'::uuid AND dw.channel_id::text={channel_expr}
        AND dw.conversation_id={conversation_expr} AND dw.state IN {private_states}
        AND dw.operator_id<>{actor})"""
    # Owner availability is informational. It must not hide the shared queue;
    # the normal ownership predicate still keeps another operator's active
    # dialogue out of the owner's personal view.
    if policy.profile.get('role') == 'owner' or not sources or is_online(cur,tenant,actor):
        return f"({provider_expr}<>'connect' OR {ownership})"
    # The literals have been normalized to UUIDs in work_status_routing_sources.
    selected = ",".join(f"'{source}'" for source in sorted(sources))
    archive = " OR dw.state IN ('closed','dismissed')" if allow_closed_archive else ''
    owned = f"""EXISTS(SELECT 1 FROM saas_dialog_workflows dw
      WHERE dw.tenant_id='{tenant}'::uuid AND dw.channel_id::text={channel_expr}
        AND dw.conversation_id={conversation_expr} AND dw.state<>'waiting'
        AND (dw.operator_id={actor}{archive}))"""
    return f"({provider_expr}<>'connect' OR ({ownership} AND ({channel_expr} NOT IN ({selected}) OR {owned})))"


def require_access(cur, tenant, actor, provider, channel, conversation, action='view', *, allow_closed_archive=False, owner_overview=False, owner=False):
    if not enabled(cur, tenant) or provider != 'connect': return
    # Serialize the final permission check with accept/close/transfer. The caller
    # retains this transaction until its protected read or network dispatch ends.
    from tenant_ai_dialog_scope import lock_dialog
    from tenant_connect_dialog_equivalence import canonical
    original_conversation = conversation
    conversation=canonical(cur,tenant,channel,conversation)
    lock_dialog(cur, tenant, provider, channel, conversation)
    # A merge may have won while we waited for this lock. Never follow the
    # new route while retaining a lock on the old one: restart authorization.
    if canonical(cur,tenant,channel,original_conversation) != conversation:
        raise TenantLinearError('Адрес диалога изменился. Обновите диалог.', 409)
    cur.execute('''SELECT 1 FROM saas_connect_conversations WHERE tenant_id=%s::uuid
      AND connect_channel_id=%s::uuid AND conversation_id=%s''', (tenant,channel,conversation))
    if not cur.fetchone():
        raise TenantLinearError('Адрес диалога изменился. Обновите диалог.', 409)
    row = _row(cur, tenant, channel, conversation)
    overview_read=(action=='view' and owner_overview and original_conversation==conversation)
    archive_read = (action == 'view' and allow_closed_archive and original_conversation == conversation
                    and is_shared_archive(row, actor))
    if (row and row['state'] != 'waiting' and int(row['operator_id']) != int(actor)
            and not (archive_read or overview_read)):
        raise TenantLinearError('Диалог недоступен: его принял другой сотрудник.', 404)
    if ((not row or row['state'] == 'waiting') and action == 'view' and not overview_read and not owner):
        from tenant_dialog_presence import is_online, work_status_routing_requested
        if work_status_routing_requested(tenant,channel) and not is_online(cur,tenant,actor,source_channel=channel):
            raise TenantLinearError('Новые диалоги доступны операторам в статусе «В сети».', 404)
    if action != 'view' and (not row or row['state'] != 'accepted'):
        raise TenantLinearError('Сначала примите диалог.', 409)
    return True  # Active protection; callers may revalidate their original route proof.


def is_shared_archive(workflow, actor):
    return bool(workflow and workflow.get('state') in {'closed','dismissed'} and workflow.get('operator_id') != int(actor))


def is_owner_observer(cur, tenant, actor, channel, workflow, *, online=None):
    """Owner overview preserves operator ownership, but ignores owner status."""
    state=(workflow or {}).get('state','waiting')
    if state=='accepted':
        return workflow.get('operator_id') != int(actor)
    if state=='waiting':
        # A waiting dialogue has no operator owner to protect. Owner status is
        # informational and does not turn the shared queue into read-only view.
        return False
    return False


def ai_blocked(cur, tenant, provider, channel, conversation):
    if not enabled(cur, tenant) or provider != 'connect': return False
    row = _row(cur, tenant, channel, conversation)
    return bool(row and row['state'] in {'accepted', 'closed', 'dismissed'})


def _return_unavailable_dialog_to_queue(cur, tenant, channel, conversation, message_id, event_at):
    from tenant_dialog_presence import _work_ready, is_online, work_status_routing_requested
    if not work_status_routing_requested(tenant,channel) or not _work_ready(cur):
        return False
    row = _row(cur,tenant,channel,conversation)
    if not row or row['state'] != 'accepted':
        return False
    if is_online(cur,tenant,row['operator_id'],source_channel=channel):
        return False
    cur.execute("""UPDATE saas_dialog_workflows w SET state='waiting',operator_id=NULL,
        id=%s::uuid,revision=w.revision+1,last_inbound_id=%s,updated_at=clock_timestamp()
        FROM saas_dialog_episodes e
        WHERE w.tenant_id=%s::uuid AND w.channel_id=%s::uuid AND w.conversation_id=%s
          AND w.id=e.id AND e.tenant_id=w.tenant_id AND w.state='accepted' AND e.closed_at IS NULL
          AND w.last_inbound_id IS DISTINCT FROM %s
          AND %s::timestamptz>GREATEST(e.accepted_at,w.updated_at)
        RETURNING w.updated_at""",
        (str(uuid.uuid4()),str(message_id),tenant,channel,conversation,str(message_id),event_at))
    moved = cur.fetchone()
    if not moved:
        return False
    cur.execute("""UPDATE saas_dialog_episodes SET closed_at=%s
        WHERE tenant_id=%s::uuid AND id=%s::uuid AND closed_at IS NULL""",
        (moved['updated_at'],tenant,row['id']))
    return True


def note_inbound(cur, tenant, channel, conversation, message_id, event_at, *, is_replay=False):
    """Called only AFTER a fresh provider incoming message was actually inserted.

    Unknown timestamps, old history, echo and duplicate events cannot reopen.
    Opening a queue item does not create an episode or a case.
    """
    if not enabled(cur, tenant) or is_replay or not message_id or not event_at: return
    from tenant_ai_dialog_scope import lock_dialog
    from tenant_connect_dialog_equivalence import canonical
    conversation=canonical(cur,tenant,channel,conversation)
    lock_dialog(cur, tenant, 'connect', channel, conversation)
    if _return_unavailable_dialog_to_queue(cur,tenant,channel,conversation,message_id,event_at):
        return
    cur.execute("""UPDATE saas_dialog_workflows SET
      state=CASE WHEN state IN ('closed','dismissed') THEN 'waiting' ELSE state END,
      operator_id=CASE WHEN state IN ('closed','dismissed') THEN NULL ELSE operator_id END,
      id=CASE WHEN state IN ('closed','dismissed') THEN %s::uuid ELSE id END,
      revision=revision+1,last_inbound_id=%s,updated_at=clock_timestamp()
      WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id=%s
        AND last_inbound_id IS DISTINCT FROM %s
        AND (state NOT IN ('closed','dismissed') OR %s::timestamptz>closed_at)""",
      (str(uuid.uuid4()),str(message_id),tenant,channel,conversation,str(message_id),event_at))
    # Do not resume a manually paused AI or the group coordinator as a side effect.


def note_inbound_call(cur, tenant, channel, conversation, call_id):
    """A newly persisted live inbound call is a new queue item, not a message.

    The authenticated gateway calls this only on first insertion. A duplicate,
    ended call or call predating the latest close cannot reopen the dialog.
    Accepted dialogs retain their operator; acceptance still uses mutate().
    """
    if not enabled(cur, tenant): return
    from tenant_ai_dialog_scope import lock_dialog
    from tenant_connect_dialog_equivalence import canonical
    physical = conversation
    conversation = canonical(cur, tenant, channel, physical)
    lock_dialog(cur, tenant, 'connect', channel, conversation)
    if canonical(cur, tenant, channel, physical) != conversation: return
    cur.execute("""UPDATE saas_dialog_workflows w SET state='waiting',
      operator_id=NULL,id=%s::uuid,revision=w.revision+1,updated_at=clock_timestamp()
      FROM saas_browser_calls c
      WHERE w.tenant_id=%s::uuid AND w.channel_id=%s::uuid AND w.conversation_id=%s
        AND w.state IN ('closed','dismissed')
        AND c.id=%s::uuid AND c.tenant_id=w.tenant_id AND c.channel_id=w.channel_id
        AND c.conversation_id=%s AND c.direction='inbound' AND c.state='incoming'
        AND c.actor_id IS NULL AND c.created_at>w.closed_at""",
      (str(uuid.uuid4()),tenant,channel,conversation,str(call_id),physical))
    # Keep message history, completed episodes and manually paused AI untouched.


def _targets(cur, tenant, policy, channel, actor, *, filter_presence=True):
    from tenant_scoped_access_store import read_access_profile
    from tenant_policy import TenantPolicy
    from tenant_dialog_access import require_source
    from tenant_dialog_presence import is_online, routing_enabled
    routing = filter_presence and routing_enabled(cur,tenant,channel)
    cur.execute('SELECT telegram_id,display_name FROM saas_tenant_members WHERE tenant_id=%s::uuid AND active=TRUE ORDER BY telegram_id', (tenant,))
    members=cur.fetchall(); result=[]
    for member in members:
        target=int(member['telegram_id'])
        if target==actor: continue
        profile=read_access_profile(cur,tenant,target,lock=True)
        if not profile: continue
        p=TenantPolicy({**profile,'tenant_id':tenant})
        if p.action_scope('dialogs','update') is None and not p.legacy_allows('dialogs'): continue
        try:
            require_source(cur,tenant,p,'connect',channel,'view')
            require_source(cur,tenant,p,'connect',channel,'update')
        except TenantLinearError: continue
        if routing and not is_online(cur,tenant,target,lock=True,_routing=routing,source_channel=channel): continue
        result.append({'employee_id':target,'name':member.get('display_name') or str(target)})
    return result


_UNSET_WORKFLOW=object()


def transfer_details(profile, params):
    """Return the recorded transfer of the current, visible source episode."""
    from tenant_operator_access import access, assert_route
    from tenant_platform import _connect

    if params.get('provider') != 'connect':
        return {'transfer': None}
    with _connect() as conn, conn.cursor() as cur:
        tenant, _, policy = access(cur, profile, 'dialogs')
        selected = assert_route(cur, tenant, policy, 'connect', params.get('conversation_id'),
                                str(params.get('channel_id') or ''), int(params['lead_id']), allow_merged=True)
        if not enabled(cur, tenant):
            return {'transfer': None}
        channel = str(selected['connect_channel_id'])
        conversation = selected['conversation_id']
        row = _row(cur, tenant, channel, conversation)
        if not row or row['revision'] < 2:
            return {'transfer': None}
        cur.execute("""SELECT sender.display_name AS from_name, recipient.display_name AS to_name, event.created_at
          FROM saas_tenant_audit_events event
          LEFT JOIN saas_tenant_members sender ON sender.tenant_id=event.tenant_id
            AND sender.telegram_id=event.actor_telegram_id
          LEFT JOIN saas_tenant_members recipient ON recipient.tenant_id=event.tenant_id
            AND recipient.telegram_id::text=event.payload->>'operator_id'
          WHERE event.tenant_id=%s::uuid AND event.action='dialog_transfer'
            AND event.entity_type='dialog_episode' AND event.entity_id=%s
            AND event.payload->>'channel_id'=%s AND event.payload->>'conversation_id'=%s
          ORDER BY event.created_at DESC, event.id DESC LIMIT 1""",
          (tenant, str(row['id']), channel, conversation))
        event = cur.fetchone()
        if not event:
            return {'transfer': None}
        return {'transfer': {'from_name': event['from_name'], 'to_name': event['to_name'],
                             'at': event['created_at'].isoformat() if event['created_at'] else None}}


def _manual_start_exempt_from_operator_routing(tenant, channel, policy):
    # Closed history is already restricted to its current owner. An explicit
    # restart by that employee is not a claim from the shared waiting queue.
    # Keep existing legacy admission and operator availability unchanged.
    from tenant_dialog_presence import work_status_routing_requested
    role = policy.profile.get('role') if policy else None
    return bool(role and role != 'operator' and work_status_routing_requested(tenant, channel))


def decorate(cur, tenant, actor, dialog, *, details=True, allow_closed_archive=False, owner_observer=False, _rollout=None, _workflow=_UNSET_WORKFLOW, _last_inbound=None, _can_update=False, _has_inbound=None, _online=None, _policy=None):
    # Internal projection snapshot only, never authorization for a mutation.
    # Listing passes state read under its retained shared transaction barrier.
    from tenant_dialog_rollout import guard
    rollout=_rollout if _rollout is not None else guard(cur,tenant)
    if not rollout.enforced or dialog.get('provider')!='connect': return dialog
    row=_row(cur,tenant,dialog['channel_id'],dialog['conversation_id']) if _workflow is _UNSET_WORKFLOW else _workflow
    state=(row or {}).get('state','waiting')
    own=bool(row and row.get('operator_id')==actor)
    shared_archive=allow_closed_archive and is_shared_archive(row, actor)
    if _policy and _policy.profile.get('role') == 'owner':
        online=True
    elif _online is None:
        needs_presence=(state=='waiting' and bool(dialog.get('can_send'))) or state in {'closed','dismissed'}
        if needs_presence:
            from tenant_dialog_presence import is_online
            online=is_online(cur,tenant,actor,source_channel=dialog['channel_id'])
        else:
            online=False
    else:
        online=bool(_online)
    # Internal list-preview token only; mutation always revalidates the message.
    last={'id':_last_inbound} if _last_inbound else {}
    if details:
        cur.execute("""SELECT id::text AS id FROM saas_crm_messages WHERE tenant_id=%s::uuid
          AND channel=%s AND kommo_lead_id=%s AND direction='incoming'
          ORDER BY created_at DESC,id DESC LIMIT 1""",(tenant,'connect:'+str(dialog['channel_id']),dialog['lead_id']))
        last=cur.fetchone() or {}
    dialog['lifecycle_enabled']=True
    dialog['lifecycle']={'id':str(row['id']) if row else None,'state':state,
        'revision':int(row['revision']) if row else 0,'operator_id':row.get('operator_id') if row else None,
        'can_accept':state=='waiting' and rollout.admission_enabled and online and bool(dialog.get('can_send')),
        'can_start':state in {'closed','dismissed'} and rollout.admission_enabled and (own or shared_archive)
            and _can_update is True and (own or dialog.get('can_send') is True)
            and (online or (own and dialog.get('channel_active') is True
                and _manual_start_exempt_from_operator_routing(tenant,dialog['channel_id'],_policy))),
        'can_dismiss':state=='waiting' and rollout.admission_enabled and _can_update is True and bool(last),
        'last_inbound_id':last.get('id'),
        'has_inbound':bool(last) if details else _has_inbound,
        'can_close':state=='accepted' and own,
        'can_transfer':own and (state=='accepted' or (
            state in {'closed','dismissed'} and _can_update is True and dialog.get('channel_active') is True)),
        'can_submit_case':state=='closed' and own}
    if owner_observer:
        dialog['owner_overview_read_only']=True
        for key in ('can_accept','can_start','can_dismiss','can_close','can_transfer','can_submit_case'):
            dialog['lifecycle'][key]=False
    if shared_archive or owner_observer:
        dialog.update(archive_read_only=True, can_use_copilot=False, can_edit_display_name=False,
                      controls_allowed=False, available_actions=[], resume_allowed=False,
                      unread_count=0, handoff_active=False)
        for key in ('group_coordination','agent_assignment','group_assignment_preview','handoff_assignee_name',
                    'handoff_reason','handoff_event_id','handoff_source','assignee_name','assigned_to',
                    'agent_id','agent_name','agent_scopes','agent_schedule','latest_job_error','latest_job_state',
                    'last_message_id','last_message_direction','last_message_type','last_message_text',
                    'last_message_preview','last_message_at','read_through_message_id','avatar_status'):
            dialog.pop(key,None)
    if details:
        if row and state in {'accepted', 'closed'}:
            # Transfer changes the owner, not the original acceptance actor.
            cur.execute("""SELECT NULLIF(BTRIM(member.display_name), '') AS accepted_by_name
              FROM saas_dialog_episodes episode
              JOIN saas_tenant_members member ON member.tenant_id=episode.tenant_id
                AND member.telegram_id=episode.accepted_by
              WHERE episode.tenant_id=%s::uuid AND episode.channel_id=%s::uuid
                AND episode.conversation_id=%s AND episode.id=%s::uuid""",
                (tenant, dialog['channel_id'], dialog['conversation_id'], str(row['id'])))
            accepted_by = (cur.fetchone() or {}).get('accepted_by_name')
            if accepted_by:
                dialog['lifecycle']['accepted_by_name'] = accepted_by
        if state=='accepted' and own:
            import tenant_dialog_company_completion as completion
            required=completion.enabled(tenant)
            if completion.available(tenant) and (required or completion.can_preview(_policy)):
                readiness=completion.status(cur,tenant,dialog['channel_id'],dialog['conversation_id'],
                    identity_conflict=dialog.get('identity_conflict') is True)
                dialog['lifecycle']['company_completion']={
                    'status':readiness,'required':required,
                    'can_choose':readiness in {'missing','archived'} and _can_update and completion.can_choose(_policy),
                    'can_skip':_can_update is True and (readiness=='missing' or (not required and readiness=='archived')),
                }
            elif completion.available(tenant) and _can_update is True:
                # Deliberately emit the same menu whether a hidden company is
                # present or absent; a later choice must not overwrite one.
                dialog['lifecycle']['company_completion']={
                    'status':'choice_available','required':False,
                    'can_choose':completion.can_choose(_policy),'can_skip':True,
                }
        if shared_archive or owner_observer:
            dialog['can_send']=False
            dialog['send_block_reason']='dialog_accept_required'
            return dialog
        cur.execute("""SELECT id::text AS id,accepted_at,closed_at,case_id::text AS case_id
          FROM saas_dialog_episodes WHERE tenant_id=%s::uuid AND channel_id=%s::uuid
            AND conversation_id=%s AND operator_id=%s AND closed_at IS NOT NULL
          ORDER BY closed_at DESC LIMIT 20""",(tenant,dialog['channel_id'],dialog['conversation_id'],actor))
        completed=[{key:(value.isoformat() if hasattr(value,'isoformat') else value)
            for key,value in episode.items()} for episode in cur.fetchall()]
        dialog['lifecycle']['completed_episodes']=completed
        if dialog['lifecycle']['can_submit_case']:
            dialog['lifecycle']['can_submit_case']=any(episode['id']==str(row['id']) for episode in completed)
    dialog['can_send']=bool(dialog.get('can_send') and own and state=='accepted')
    if not dialog['can_send']: dialog['send_block_reason']='dialog_accept_required'
    return dialog



def mutate(profile, payload):
    """Retry only a proven rolled-back completion transaction, never ambiguity.

    PostgreSQL 40P01 aborts the transaction. _mutate_once has left its connection
    context (and rolled back) before this handler runs. Each retry repeats all
    authorization/CAS checks with the SAME request ID and original tokens.
    This tolerates a startup-DDL collision; it does not solve migration ordering.
    """
    from psycopg.errors import DeadlockDetected
    attempts=3 if payload.get('action') in {'close','dismiss'} else 1
    for attempt in range(attempts):
        try:
            return _mutate_once(profile,payload)
        except DeadlockDetected:
            if attempt+1==attempts:raise
            time.sleep(0.15*(attempt+1))


def _mutate_once(profile, payload):
    if payload.get('action')=='case':
        from tenant_dialog_episode_cases import submit
        return submit(profile,payload)
    from tenant_platform import _connect
    from tenant_operator_access import access, _resolve_connect_route
    from tenant_dialog_access import require_source
    from tenant_ai_dialog_scope import lock_dialog
    action=payload.get('action')
    if action not in {'accept','start','close','transfer','case','dismiss'}: raise TenantLinearError('Неизвестное действие.')
    request_id=str(uuid.UUID(str(payload.get('request_id'))))
    revision=payload.get('expected_revision')
    if type(revision) is not int or revision<0: raise TenantLinearError('Обновите диалог.',409)
    digest=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    with _connect() as conn:
        with conn.cursor() as cur:
            tenant = str(uuid.UUID(str(profile.get('tenant_id'))))
            if not enabled(cur, tenant):
                raise TenantLinearError('Новая очередь диалогов ещё не включена.',409)
            if action in {'accept','start','dismiss'} and not acceptance_enabled(cur, tenant):
                raise TenantLinearError('Приём новых диалогов приостановлен.',409)
            tenant,actor,policy=access(cur,profile,action='update')
            if payload.get('provider')!='connect': raise TenantLinearError('Выберите источник Connect.')
            route=_resolve_connect_route(cur,tenant,str(payload.get('conversation_id') or ''),str(payload.get('channel_id') or ''),int(payload['lead_id']),allow_merged=True)
            channel,conversation=str(route['connect_channel_id']),route['conversation_id']
            from tenant_dialog_presence import is_online, work_status_routing_requested
            target = None
            eligible = True
            transfer_source_active = False
            owner_only_start = False
            if action == 'start' and _manual_start_exempt_from_operator_routing(tenant,channel,policy):
                owner_only_start = (policy.profile.get('role') != 'owner'
                                    and not is_online(cur,tenant,actor,lock=True,source_channel=channel))
                # Preserve the active exact-source requirement formerly implicit
                # in is_online. Keep this shared lock through the transition.
                cur.execute('''SELECT 1 FROM saas_connect_channels
                    WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid
                    AND active FOR SHARE''',(tenant,channel))
                if not cur.fetchone():
                    raise TenantLinearError('Источник недоступен.',403)
            elif (policy.profile.get('role') != 'owner'
                  and (action in {'accept','start'}
                       or (action == 'dismiss' and work_status_routing_requested(tenant,channel)))):
                eligible = is_online(cur,tenant,actor,lock=True,source_channel=channel)
            if action == 'transfer':
                target=payload.get('target_employee_id')
                # Preserve all existing locked membership/source checks, but
                # acquire them BEFORE the route lock to avoid lock inversion.
                if type(target) is not int or target not in {m['employee_id'] for m in _targets(cur,tenant,policy,channel,actor,filter_presence=False)}:
                    raise TenantLinearError('Сотрудник не имеет доступа к этому источнику.',403)
                eligible = is_online(cur,tenant,target,lock=True,source_channel=channel)
                cur.execute('''SELECT 1 FROM saas_connect_channels WHERE tenant_id=%s::uuid
                    AND connect_channel_id=%s::uuid AND active FOR SHARE''',(tenant,channel))
                transfer_source_active = bool(cur.fetchone())
            require_source(cur,tenant,policy,'connect',channel,'view')
            require_source(cur,tenant,policy,'connect',channel,'update')
            start_source_ready = False
            if action == 'start':
                # Match the existing send projection without locking the
                # integration after the source: configuration takes those
                # locks in the reverse order. Send revalidates live readiness.
                cur.execute("""SELECT 1 FROM saas_connect_channels ch JOIN saas_tenants t
                  ON t.id=ch.tenant_id AND t.status='active'
                  JOIN saas_tenant_integrations i ON i.tenant_id=t.id
                    AND i.provider='connect' AND i.status='connected'
                  WHERE ch.tenant_id=%s::uuid AND ch.connect_channel_id=%s::uuid
                    AND ch.active""",(tenant,channel))
                start_source_ready = bool(cur.fetchone())
            lock_dialog(cur,tenant,'connect',channel,conversation)
            fresh_route=_resolve_connect_route(cur,tenant,str(payload.get('conversation_id') or ''),str(payload.get('channel_id') or ''),int(payload['lead_id']),allow_merged=True)
            if (str(fresh_route['connect_channel_id']),fresh_route['conversation_id'],int(fresh_route['lead_id'])) != (channel,conversation,int(route['lead_id'])):
                raise TenantLinearError('Адрес диалога изменился. Обновите диалог.',409)
            cur.execute('SELECT * FROM saas_dialog_lifecycle_receipts WHERE tenant_id=%s::uuid AND request_id=%s::uuid',(tenant,request_id))
            receipt=cur.fetchone()
            if receipt:
                if receipt['actor_id']!=actor or receipt['input_hash']!=digest: raise TenantLinearError('Запрос уже использован.',409)
                require_access(cur,tenant,actor,'connect',channel,conversation,'view',owner=policy.profile.get('role')=='owner')
                return receipt['result']
            # Keep membership/presence locks acquired BEFORE the route lock,
            # but only new mutations require Online. A retry still needs the
            # same actor, identical payload and current view authorization.
            if not eligible:
                message = ('Сотрудник сейчас не в статусе «Онлайн».' if action=='transfer' else
                           'Установите статус «Онлайн», чтобы принять диалог.')
                raise TenantLinearError(message,403)
            if {'company_id','company_choice'} & payload.keys() and action!='close':
                raise TenantLinearError('Выбор компании доступен только при завершении диалога.',422)
            bound_on_close=None
            row=_row(cur,tenant,channel,conversation)
            if (int(row['revision']) if row else 0)!=revision: raise TenantLinearError('Диалог уже изменился. Обновите его.',409)
            if action=='dismiss':
                if row and row['state']!='waiting': raise TenantLinearError('Диалог уже принят или закрыт.',409)
                from tenant_dialog_company_completion import enabled as company_completion_enabled
                # Old bulk clients emit the same dismiss action as the old
                # individual control. A fresh action from the updated individual
                # control opts in explicitly; committed receipts replay above.
                if company_completion_enabled(tenant) and payload.get('individual_no_reply') is not True:
                    raise TenantLinearError('Обновите страницу и отметьте этот диалог отдельно как «Не требует ответа».',422)
                cur.execute("""SELECT id::text AS id FROM saas_crm_messages WHERE tenant_id=%s::uuid
                  AND channel=%s AND kommo_lead_id=%s AND direction='incoming'
                  ORDER BY created_at DESC,id DESC LIMIT 1""",(tenant,'connect:'+channel,route['lead_id']))
                last=cur.fetchone() or {}
                if not last.get('id') or payload.get('message_id')!=last['id']:
                    raise TenantLinearError('Появилось новое сообщение. Обновите диалог.',409)
                cur.execute("""INSERT INTO saas_dialog_workflows(tenant_id,channel_id,conversation_id,id,state,operator_id,revision,closed_at,last_inbound_id)
                  VALUES(%s::uuid,%s::uuid,%s,%s::uuid,'dismissed',%s,1,clock_timestamp(),%s)
                  ON CONFLICT(tenant_id,channel_id,conversation_id) DO UPDATE SET state='dismissed',
                  operator_id=EXCLUDED.operator_id,revision=saas_dialog_workflows.revision+1,
                  closed_at=clock_timestamp(),last_inbound_id=EXCLUDED.last_inbound_id,updated_at=clock_timestamp()""",
                  (tenant,channel,conversation,str(uuid.uuid4()),actor,last['id']))
            elif action in {'accept','start'}:
                if action=='start':
                    require_access(cur,tenant,actor,'connect',channel,conversation,'view',allow_closed_archive=True,
                                   owner=policy.profile.get('role')=='owner')
                    if not row or row['state'] not in {'closed','dismissed'}:
                        raise TenantLinearError('Диалог уже изменился или недоступен.',409)
                    if is_shared_archive(row,actor):
                        if (payload.get('conversation_id') != conversation or str(payload.get('channel_id')) != channel
                                or int(payload['lead_id']) != int(route['lead_id'])):
                            raise TenantLinearError('Диалог недоступен.',404)
                        if owner_only_start:
                            raise TenantLinearError('Установите статус «Онлайн», чтобы принять диалог.',403)
                        if not start_source_ready:
                            raise TenantLinearError('Источник недоступен.',403)
                elif row and row['state']!='waiting':
                    raise TenantLinearError('Диалог уже принят или завершён.',409)
                episode_id=str(uuid.uuid4())
                cur.execute('''INSERT INTO saas_dialog_episodes(tenant_id,id,channel_id,conversation_id,lead_id,accepted_by,operator_id)
                  VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s)''',(tenant,episode_id,channel,conversation,route['lead_id'],actor,actor))
                cur.execute('''INSERT INTO saas_dialog_workflows(tenant_id,channel_id,conversation_id,id,state,operator_id,revision)
                  VALUES(%s::uuid,%s::uuid,%s,%s::uuid,'accepted',%s,1)
                  ON CONFLICT(tenant_id,channel_id,conversation_id) DO UPDATE SET id=EXCLUDED.id,
                  state='accepted',operator_id=EXCLUDED.operator_id,revision=saas_dialog_workflows.revision+1,
                  closed_at=NULL,updated_at=clock_timestamp()''',(tenant,channel,conversation,episode_id,actor))
                # Admission + actual send recheck this episode, without touching
                # existing group-global human/paused settings or other channels.
                cur.execute('''UPDATE saas_ai_agent_jobs SET state='skipped',revision=revision+1,
                  lease_token=NULL,lease_until=NULL,error_code='operator_accepted',updated_at=now()
                  WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s
                    AND conversation_id=%s AND state IN ('queued','processing')''',(tenant,channel,conversation))
            else:
                if not row or row.get('operator_id')!=actor: raise TenantLinearError('Диалог недоступен.',404)
                if action=='transfer':
                    if row['state'] not in {'accepted','closed','dismissed'}:
                        raise TenantLinearError('Диалог недоступен для передачи.',409)
                    if row['state']!='accepted' and not transfer_source_active:
                        raise TenantLinearError('Источник недоступен.',403)
                    cur.execute('UPDATE saas_dialog_workflows SET operator_id=%s,revision=revision+1,updated_at=clock_timestamp() WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id=%s',(target,tenant,channel,conversation))
                    # A handed-off completed dialogue keeps its original episode
                    # and case attribution. Only current dialogue ownership moves.
                    if row['state']=='accepted':
                        cur.execute('UPDATE saas_dialog_episodes SET operator_id=%s WHERE tenant_id=%s::uuid AND id=%s::uuid',(target,tenant,row['id']))
                else:
                    if row['state']!='accepted': raise TenantLinearError('Диалог уже завершён.',409)
                    from tenant_dialog_company_completion import require_on_close
                    bound_on_close=require_on_close(cur,tenant,actor,channel,conversation,payload,policy=policy)
                    cur.execute("UPDATE saas_dialog_workflows SET state='closed',revision=revision+1,closed_at=clock_timestamp(),updated_at=clock_timestamp() WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id=%s RETURNING closed_at",(tenant,channel,conversation))
                    closed_at=cur.fetchone()['closed_at']
                    cur.execute('UPDATE saas_dialog_episodes SET closed_at=%s WHERE tenant_id=%s::uuid AND id=%s::uuid',(closed_at,tenant,row['id']))
            current=_row(cur,tenant,channel,conversation)
            result={'lifecycle':{k:str(current[k]) if k=='id' else current[k] for k in ('id','state','operator_id','revision')}}
            cur.execute('''INSERT INTO saas_dialog_lifecycle_receipts(tenant_id,request_id,actor_id,input_hash,result)
              VALUES(%s::uuid,%s::uuid,%s,%s,%s::jsonb)''',(tenant,request_id,actor,digest,json.dumps(result)))
            cur.execute('''INSERT INTO saas_tenant_audit_events(id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
              VALUES(%s::uuid,%s::uuid,%s,%s,'dialog_episode',%s,%s::jsonb)''',(str(uuid.uuid4()),tenant,actor,'dialog_'+action,str(payload.get('episode_id') or current['id']),json.dumps({'channel_id':channel,'conversation_id':conversation,'operator_id':current['operator_id'],'revision':current['revision'],**({'message_id':payload['message_id']} if action=='dismiss' else {}),**({'company_bound_on_close':bound_on_close} if bound_on_close else {}),**({'company_choice':'none'} if action=='close' and payload.get('company_choice')=='none' else {})})))
        conn.commit()
    return result


def record_reconciled_dispatch(cur,tenant,actor,lead,request_id,channel,conversation,result):
    """Bind a verified status result to its original durable send episode.

    Internal reconciliation only: callers retain current source grants and have
    verified the provider's exact request/author/source/input receipt contract.
    This does not authorize a provider send or change the active operator.
    """
    if not enabled(cur, tenant):return
    external=result.get('message_id') if isinstance(result,dict) else None
    if not isinstance(external,str) or not external or result.get('provider_accepted') is not True:
        raise TenantLinearError('Подтверждённая отправка не найдена.',409)
    cur.execute("""SELECT e.id FROM saas_chat_send_receipts sr
      JOIN saas_dialog_episodes e ON e.tenant_id=sr.tenant_id AND e.id::text=sr.route->>'lifecycle_episode_id'
      WHERE sr.tenant_id=%s::uuid AND sr.actor_id=%s AND sr.lead_id=%s AND sr.request_id=%s::uuid
        AND sr.state IN ('sending','unknown','accepted') AND e.channel_id=%s::uuid
        AND e.conversation_id=%s AND e.lead_id=sr.lead_id FOR SHARE OF sr,e""",
      (tenant,actor,lead,request_id,channel,conversation))
    episode=cur.fetchone()
    if not episode:raise TenantLinearError('Исходное обращение отправки не найдено.',409)
    external=external.removeprefix('connect:')
    cur.execute('SELECT episode_id,channel_id,conversation_id,external_id FROM saas_dialog_episode_sends WHERE tenant_id=%s::uuid AND request_id=%s::uuid',(tenant,request_id))
    previous=cur.fetchone()
    if previous and (str(previous['episode_id'])!=str(episode['id']) or str(previous['channel_id'])!=str(channel)
                     or previous['conversation_id']!=conversation or previous['external_id']!=external):
        raise TenantLinearError('Подтверждение отправки изменилось.',409)
    cur.execute("""INSERT INTO saas_dialog_episode_sends(tenant_id,episode_id,channel_id,conversation_id,request_id,external_id)
      VALUES(%s::uuid,%s::uuid,%s::uuid,%s,%s::uuid,%s) ON CONFLICT DO NOTHING""",
      (tenant,episode['id'],channel,conversation,request_id,external))
