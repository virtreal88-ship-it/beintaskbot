"""Shared operator episodes for an exact tenant and WhatsApp group.

Each participating operator owns a separate physical workflow route. The
existing dialogue lock already serializes these routes on the group JID.
"""
import os
import re
import uuid

from tenant_ai_group_coordination import group_jid
from tenant_linear_policy import TenantLinearError


MEMBER_ACTIONS = {'join_group', 'leave_group', 'transfer_group'}


def migrate(cur):
    cur.execute("""ALTER TABLE saas_dialog_episodes
      ADD COLUMN IF NOT EXISTS group_leader_channel_id UUID
      CHECK (group_leader_channel_id IS NULL OR conversation_id ~ '^[0-9]{5,22}(-[0-9]{5,22})?@g[.]us$')""")
    cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS saas_dialog_group_open_idx
      ON saas_dialog_episodes(tenant_id,conversation_id)
      WHERE group_leader_channel_id IS NOT NULL AND closed_at IS NULL""")
    cur.execute("""CREATE INDEX IF NOT EXISTS saas_dialog_group_latest_idx
      ON saas_dialog_episodes(tenant_id,conversation_id,accepted_at DESC)
      WHERE group_leader_channel_id IS NOT NULL""")
    cur.execute("""CREATE TABLE IF NOT EXISTS saas_dialog_group_participants (
      tenant_id UUID NOT NULL, id UUID NOT NULL, episode_id UUID NOT NULL,
      channel_id UUID NOT NULL, conversation_id TEXT NOT NULL, actor_id BIGINT NOT NULL,
      account_jid TEXT NOT NULL, account_lid TEXT NOT NULL DEFAULT '',
      generation TEXT NOT NULL, gateway_id UUID NOT NULL,
      joined_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), left_at TIMESTAMPTZ,
      PRIMARY KEY(tenant_id,id),
      FOREIGN KEY(tenant_id,episode_id) REFERENCES saas_dialog_episodes(tenant_id,id),
      FOREIGN KEY(tenant_id,actor_id) REFERENCES saas_tenant_members(tenant_id,telegram_id),
      FOREIGN KEY(tenant_id,channel_id,conversation_id)
        REFERENCES saas_connect_conversations(tenant_id,connect_channel_id,conversation_id))""")
    cur.execute("ALTER TABLE saas_dialog_group_participants ADD COLUMN IF NOT EXISTS account_lid TEXT NOT NULL DEFAULT ''")
    for column in ('channel_id', 'actor_id', 'account_jid'):
        cur.execute(f"""CREATE UNIQUE INDEX IF NOT EXISTS saas_dialog_group_active_{column}_idx
          ON saas_dialog_group_participants(tenant_id,episode_id,{column}) WHERE left_at IS NULL""")
    cur.execute("""CREATE INDEX IF NOT EXISTS saas_dialog_group_participants_episode_idx
      ON saas_dialog_group_participants(tenant_id,episode_id,channel_id,actor_id)""")
    cur.execute("""CREATE INDEX IF NOT EXISTS saas_dialog_group_legacy_open_idx
      ON saas_dialog_workflows(tenant_id,conversation_id) WHERE state='accepted'""")
    cur.execute("SELECT to_regclass('saas_chat_send_receipts') IS NOT NULL AS ready")
    if cur.fetchone()['ready']:
        cur.execute("""CREATE INDEX IF NOT EXISTS saas_dialog_group_send_provider_idx
          ON saas_chat_send_receipts(tenant_id,(route->>'group_provider_id')) WHERE route ? 'group_provider_id'""")


def source_available(mode):
    """Collaboration is native-source scoped, not selected by a group pilot."""
    return bool(os.environ.get('BEINTASKBOT_UNIFIED_WHATSAPP_SEND') == '1'
                and mode.get('enabled') and mode.get('active') and not mode.get('paused')
                and mode.get('capture_enabled') and mode.get('gateway_enabled'))


def source_selection(modes):
    return {'sources': [channel for channel, mode in modes.items() if source_available(mode)],
            'native_markers': list(modes)}


def ready(cur):
    cur.execute("SELECT to_regclass('saas_dialog_group_participants') IS NOT NULL AS ready")
    return cur.fetchone()['ready']


def snapshots(cur, tenant, conversations, *, sources=(), native_markers=()):
    groups = sorted({chat for chat in conversations if group_jid(chat)})
    if not groups or not ready(cur):
        return {}
    cur.execute("""SELECT requested.chat,latest.id::text AS id,latest.operator_id,
        latest.group_leader_channel_id::text AS leader_source,latest.closed_at,
        EXISTS(SELECT 1 FROM saas_connect_conversations c WHERE c.tenant_id=%s::uuid
          AND c.conversation_id=requested.chat AND c.connect_channel_id=ANY(%s::uuid[]))
        AND NOT EXISTS(SELECT 1 FROM saas_connect_conversations c
          JOIN saas_connect_channels ch USING(tenant_id,connect_channel_id)
          WHERE c.tenant_id=%s::uuid AND c.conversation_id=requested.chat AND ch.active
            AND NOT (c.connect_channel_id=ANY(%s::uuid[]))) AS configured,
        CASE WHEN latest.closed_at IS NULL THEN (
          SELECT jsonb_agg(jsonb_build_object('id',p.id,'channel_id',p.channel_id,
            'actor_id',p.actor_id,'account_jid',p.account_jid,'generation',p.generation,
            'gateway_id',p.gateway_id,'name',m.display_name) ORDER BY p.joined_at,p.id)
          FROM saas_dialog_group_participants p
          JOIN saas_tenant_members m ON m.tenant_id=p.tenant_id AND m.telegram_id=p.actor_id
          WHERE p.tenant_id=%s::uuid AND p.episode_id=latest.id AND p.left_at IS NULL
        ) END AS participants,
        EXISTS(SELECT 1 FROM saas_dialog_workflows w
          WHERE w.tenant_id=%s::uuid AND w.conversation_id=requested.chat
            AND w.state='accepted' AND w.id IS DISTINCT FROM latest.id) AS legacy_active
      FROM unnest(%s::text[]) AS requested(chat)
      LEFT JOIN LATERAL (
        SELECT id,operator_id,group_leader_channel_id,closed_at FROM saas_dialog_episodes
        WHERE tenant_id=%s::uuid AND conversation_id=requested.chat AND group_leader_channel_id IS NOT NULL
        ORDER BY (closed_at IS NULL) DESC,accepted_at DESC,id DESC LIMIT 1
      ) latest ON TRUE""",(tenant, list(sources), tenant, list(native_markers), tenant, tenant, groups, tenant))
    result={}
    for row in cur.fetchall():
        if row['id'] or row['configured']:
            result[row['chat']]=row
    return result


def snapshot(cur, tenant, conversation, **selection):
    return snapshots(cur, tenant, [conversation], **selection).get(conversation)


def active(group):
    return bool(group and group['id'] and group['closed_at'] is None)


def revision(group, workflow):
    return f"{group['id'] or '-'}:{int(active(group))}:{int(group['legacy_active'])}:{(workflow or {}).get('revision', 0)}"


def read_revision(profile, channel, conversation, lead_id):
    from psycopg.errors import LockNotAvailable
    from tenant_dialog_lifecycle import _row, enabled
    from tenant_operator_access import access, assert_route
    from tenant_platform import _connect

    try:
        if str(uuid.UUID(channel)) != channel or not group_jid(conversation) or not 0 < int(lead_id) < 2**63:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise TenantLinearError('Диалог не найден.',404) from None
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute("SET LOCAL lock_timeout = '250ms'")
            tenant,_,policy=access(cur,profile,'dialogs')
            assert_route(cur,tenant,policy,'connect',conversation,channel,int(lead_id))
            from tenant_whatsapp_native_rollout import modes
            group = (snapshot(cur,tenant,conversation,**source_selection(modes(cur,tenant,conversation=conversation)))
                     if enabled(cur,tenant) else None)
            return {'revision':revision(group,_row(cur,tenant,channel,conversation)) if group else ''}
    except LockNotAvailable:
        return {}


def participant(group, actor, channel=None):
    return next((p for p in group.get('participants') or []
                 if int(p['actor_id']) == int(actor) and (channel is None or p['channel_id'] == channel)), None)


def queue_has_inbound(group, workflow, has_inbound):
    if not group or not group['closed_at'] or group['legacy_active']:
        return has_inbound
    # Only note_inbound can reopen an idle route after a shared close.
    return bool(workflow and workflow['last_inbound_id'] and workflow['updated_at'] > group['closed_at'])


def can_restart_waiting(group, workflow):
    return bool(group and group['configured'] and group['closed_at'] and not group['legacy_active']
                and (workflow or {}).get('state', 'waiting') == 'waiting'
                and not queue_has_inbound(group, workflow, True))


def keeps_assignment(cur, tenant, conversation, workflow):
    if not workflow or workflow['state'] != 'accepted' or not group_jid(conversation):
        return False
    group = snapshot(cur, tenant, conversation)
    return active(group) and group['id'] == str(workflow['id'])


def ensure_online(cur, tenant, actor, policy, channel):
    from tenant_dialog_presence import is_online
    if policy.profile.get('role') != 'owner' and not is_online(cur, tenant, actor, lock=True, source_channel=channel):
        raise TenantLinearError('Установите статус «В сети», чтобы работать в группе.',403)


def native_route(cur, tenant, channel, conversation, lead):
    from tenant_connect_transport import connect_route
    from tenant_platform import TenantPlatformError
    from tenant_whatsapp_native_groups import membership_error
    try:
        route = connect_route(cur, tenant, lead)
    except TenantPlatformError:
        route = None
    if not route or not route.get('native') or (route['channel_id'],route['conversation_id']) != (channel,conversation):
        raise membership_error('native_group_session_unavailable')
    return route


def verified_route(cur, tenant, actor, policy, channel, conversation, lead):
    from tenant_whatsapp_native_groups import require_membership
    require_permissions(cur,tenant,policy,channel)
    ensure_online(cur, tenant, actor, policy, channel)
    route = native_route(cur, tenant, channel, conversation, lead)
    return {**route['native'],'account_lid':require_membership(route)}


def enroll(cur, tenant, episode, actor, channel, conversation, binding):
    cur.execute("""INSERT INTO saas_dialog_group_participants
      (tenant_id,id,episode_id,channel_id,conversation_id,actor_id,account_jid,account_lid,generation,gateway_id)
      VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s::uuid)""",
      (tenant,str(uuid.uuid4()),episode,channel,conversation,actor,binding['scope']['account'],
       binding['account_lid'],binding['scope']['generation'],binding['gateway_id']))


def open_episode(cur, tenant, episode, actor, channel, conversation, binding):
    cur.execute("""UPDATE saas_dialog_episodes SET group_leader_channel_id=%s::uuid
      WHERE tenant_id=%s::uuid AND id=%s::uuid""",(channel,tenant,episode))
    enroll(cur, tenant, episode, actor, channel, conversation, binding)
    cur.execute("""UPDATE saas_ai_agent_jobs SET state='skipped',revision=revision+1,lease_token=NULL,
      lease_until=NULL,error_code='operator_accepted',updated_at=now()
      WHERE tenant_id=%s::uuid AND provider='connect' AND conversation_id=%s
        AND state IN ('queued','processing')""",(tenant,conversation))


def require_permissions(cur, tenant, policy, channel):
    from tenant_dialog_access import require_source
    for action in ('view','update'):
        scope = policy.action_scope('dialogs',action)
        if scope == 'none' or scope is None and not policy.legacy_allows('dialogs'):
            raise TenantLinearError('Нет доступа к диалогам этого источника.',403)
        require_source(cur,tenant,policy,'connect',channel,action)


def prepare_transfer(cur, tenant, group, target):
    from tenant_scoped_access_store import read_access_profile
    from tenant_policy import TenantPolicy

    peer = participant(group or {}, target) if type(target) is int and 0 < target < 2**63 else None
    if not active(group) or not peer:
        raise TenantLinearError('Этот сотрудник больше не подключён к группе.',409)
    member = read_access_profile(cur, tenant, target, lock=True)
    if not member or member['active'] is not True:
        raise TenantLinearError('Этот сотрудник больше не имеет доступа к группе.',403)
    policy = TenantPolicy({**member,'tenant_id':tenant})
    channel = peer['channel_id']
    require_permissions(cur,tenant,policy,channel)
    ensure_online(cur,tenant,target,policy,channel)
    return peer


def transition(cur, *, tenant, actor, policy, channel, conversation, lead, workflow, group, payload, peer):
    """Apply one transition under the existing group lock and route grants."""
    from tenant_dialog_lifecycle import require_access, is_shared_archive

    action = payload['action']
    if not active(group) or payload.get('expected_group_episode_id') != group['id']:
        raise TenantLinearError('Работа в группе изменилась. Обновите диалог.',409)
    episode = group['id']
    own = participant(group, actor, channel)
    leader = actor == group['operator_id'] and channel == group['leader_source']
    successor = None
    if action == 'join_group':
        require_access(cur,tenant,actor,'connect',channel,conversation,'view',owner=policy.profile.get('role')=='owner')
        if participant(group, actor) or any(p['channel_id']==channel for p in group['participants']):
            raise TenantLinearError('Этот сотрудник или номер уже подключён к группе.',409)
        if workflow and (workflow['state']=='accepted' or is_shared_archive(workflow,actor)):
            raise TenantLinearError('Сначала выберите доступный источник группы.',409)
        binding = verified_route(cur,tenant,actor,policy,channel,conversation,lead)
        if any(p['account_jid']==binding['scope']['account'] for p in group['participants']):
            raise TenantLinearError('Этот номер WhatsApp уже подключён к группе.',409)
        enroll(cur,tenant,episode,actor,channel,conversation,binding)
        cur.execute("""INSERT INTO saas_dialog_workflows(tenant_id,channel_id,conversation_id,id,state,operator_id,revision)
          VALUES(%s::uuid,%s::uuid,%s,%s::uuid,'accepted',%s,0)
          ON CONFLICT(tenant_id,channel_id,conversation_id) DO UPDATE SET id=EXCLUDED.id,
          state='accepted',operator_id=EXCLUDED.operator_id,closed_at=NULL""",(tenant,channel,conversation,episode,actor))
    else:
        if not own or not workflow or workflow['state']!='accepted' or str(workflow['id'])!=episode:
            raise TenantLinearError('Вы не подключены к этому эпизоду группы.',403)
        if action == 'leave_group':
            if len(group['participants']) == 1:
                raise TenantLinearError('Вы остались последним участником. Завершите диалог.',409)
            cur.execute("""UPDATE saas_dialog_group_participants SET left_at=clock_timestamp()
              WHERE tenant_id=%s::uuid AND id=%s::uuid AND left_at IS NULL RETURNING left_at""",(tenant,own['id']))
            left_at=cur.fetchone()['left_at']
            cur.execute("""UPDATE saas_dialog_workflows SET state='closed', closed_at=%s,
              revision=revision+1,updated_at=%s
              WHERE tenant_id=%s::uuid AND channel_id=%s::uuid AND conversation_id=%s""",
              (left_at,left_at,tenant,channel,conversation))
            if leader:
                successor=next(p for p in group['participants'] if p['id'] != own['id'])
        elif action == 'transfer_group':
            if not leader or peer['actor_id']==actor or participant(group,peer['actor_id'])!=peer:
                raise TenantLinearError('Ведение группы или подключение коллеги изменилось. Обновите диалог.',409)
            cur.execute("""SELECT lead_id FROM saas_connect_conversations WHERE tenant_id=%s::uuid
              AND connect_channel_id=%s::uuid AND conversation_id=%s""",(tenant,peer['channel_id'],conversation))
            peer_route = cur.fetchone()
            binding = native_route(cur,tenant,peer['channel_id'],conversation,peer_route['lead_id']) if peer_route else None
            check_binding(peer,binding)
            from tenant_whatsapp_native_groups import require_membership
            require_membership(binding)
            successor=peer
    if successor:
        cur.execute("""UPDATE saas_dialog_episodes SET operator_id=%s,group_leader_channel_id=%s::uuid
          WHERE tenant_id=%s::uuid AND id=%s::uuid""",(successor['actor_id'],successor['channel_id'],tenant,episode))
    cur.execute("""UPDATE saas_dialog_workflows SET revision=revision+1,updated_at=clock_timestamp()
      WHERE tenant_id=%s::uuid AND conversation_id=%s AND id=%s::uuid AND state='accepted'""",
      (tenant,conversation,episode))
    return successor if action == 'leave_group' else None


def close_episode(cur, tenant, group, conversation, closed_at):
    cur.execute("""UPDATE saas_dialog_workflows SET state='closed',closed_at=%s,updated_at=%s,revision=revision+1
      WHERE tenant_id=%s::uuid AND conversation_id=%s AND id=%s::uuid AND state='accepted'""",
      (closed_at,closed_at,tenant,conversation,group['id']))
    cur.execute("""UPDATE saas_dialog_group_participants SET left_at=%s
      WHERE tenant_id=%s::uuid AND episode_id=%s::uuid AND left_at IS NULL""",
      (closed_at,tenant,group['id']))


def check_binding(member, route):
    binding = route.get('native') if route else None
    if not binding or (binding['scope']['account'],binding['scope']['generation'],binding['gateway_id']) != (
            member['account_jid'],member['generation'],member['gateway_id']):
        raise TenantLinearError('Подключение номера WhatsApp изменилось. Выйдите и подключитесь заново.',409)


def send_context(cur, tenant, actor, policy, route):
    group = snapshot(cur, tenant, route['conversation_id'])
    if not active(group):
        return {}
    channel = route['channel_id']
    require_permissions(cur,tenant,policy,channel)
    own = participant(group,actor,channel)
    if not own:
        raise TenantLinearError('Подключитесь к группе со своего номера.',409)
    check_binding(own,route)
    ensure_online(cur,tenant,actor,policy,channel)
    return {'group_participant_id':own['id'],'group_channel_id':channel}


def decorate(dialog, actor, group, workflow, *, source, admission, can_update, online):
    """Only expose an operator's own route and the names of joined colleagues."""
    if not group:
        return
    state = dialog['lifecycle']
    ready_source = source_available(source)
    shared = active(group)
    if shared or ready_source and group['configured']:
        # An owner observing someone else's route must never take over a
        # collaborative group, including a source-local episode still draining.
        state['can_takeover'] = False
    if dialog.get('archive_read_only'):
        return
    if ready_source and (group['configured'] or shared):
        dialog['group_work_revision'] = revision(group, workflow)
    if group['legacy_active']:
        if ready_source and group['configured']:
            state['can_accept'] = state['can_start'] = False
        return
    if not shared and not ready_source:
        return
    if not active(group):
        if group['closed_at'] and state['state']=='waiting':
            state['has_inbound'] = queue_has_inbound(group, workflow, state['has_inbound'])
            if not state['has_inbound']:
                state['can_accept'] = False
                if can_restart_waiting(group, workflow):
                    state.update(state='closed',can_start=bool(admission and can_update and online),
                                 group_restart_episode_id=group['id'])
        state['can_accept'] = bool(ready_source and state['can_accept'])
        state['can_start'] = bool(ready_source and state['can_start'])
        state['group_available'] = bool(ready_source and group['configured'])
        return
    channel = dialog['channel_id']
    own = participant(group,actor,channel)
    leader = bool(own and actor==group['operator_id'] and channel==group['leader_source'])
    others = [{'employee_id':str(p['actor_id']),'name':p['name'] or ''}
              for p in group['participants'] if int(p['actor_id']) != actor]
    alone = len(group['participants']) == 1
    left = bool(workflow and str(workflow['id'])==group['id'] and workflow['state']=='closed'
                and workflow['operator_id']==actor)
    state.update(can_accept=False,can_start=False,can_takeover=False,can_transfer=False,
                 can_close=bool(leader and alone and can_update),can_dismiss=bool(leader and state['can_dismiss']))
    if left:
        state['can_submit_case'] = False
    state['group_work'] = {
        'id':group['id'], 'role':'leader' if leader else 'participant' if own else None,
        'can_join':bool(admission and can_update and online and ready_source and not participant(group,actor)
                        and state['state'] in {'waiting','closed','dismissed'}),
        'can_leave':bool(own and not alone and can_update),
        'left':left,
        'colleagues':others if own else [],
    }
    if own or state['group_work']['can_join']:
        state['group_work']['number'] = '+'+(own or source)['account_jid'].split('@',1)[0]
    dialog['can_send'] = bool(own and can_update and online and ready_source and state['state']=='accepted')
    if own and not dialog['can_send']:
        dialog['send_block_reason'] = 'group_offline' if not online else 'channel_inactive'


def join_options(cur, tenant, actor, policy, conversation, group, channels, modes):
    from tenant_dialog_presence import is_online

    remaining = [channel for channel in channels
                 if source_available(modes.get(channel,{}))
                 and not any(p['channel_id']==channel or p['account_jid']==modes[channel]['account_jid']
                             for p in group['participants'])]
    if not remaining or participant(group,actor):
        return []
    cur.execute("""SELECT c.connect_channel_id::text AS channel_id,c.lead_id,c.conversation_id,ch.name AS channel_name
      FROM saas_connect_conversations c JOIN saas_connect_channels ch USING(tenant_id,connect_channel_id)
      LEFT JOIN saas_dialog_workflows w ON w.tenant_id=c.tenant_id AND w.channel_id=c.connect_channel_id
        AND w.conversation_id=c.conversation_id
      WHERE c.tenant_id=%s::uuid AND c.conversation_id=%s AND c.connect_channel_id=ANY(%s::uuid[])
        AND (w.state IS NULL OR w.state='waiting' OR (w.state IN ('closed','dismissed') AND w.operator_id=%s))
      ORDER BY ch.name,c.connect_channel_id""",(tenant,conversation,remaining,actor))
    result=[]
    for route in cur.fetchall():
        channel=route['channel_id']
        try:
            require_permissions(cur,tenant,policy,channel)
        except TenantLinearError:
            continue
        if policy.profile.get('role')=='owner' or is_online(cur,tenant,actor,source_channel=channel):
            result.append({**route,'provider':'connect','channel_active':True,
                           'number':'+'+modes[channel]['account_jid'].split('@',1)[0]})
    return result


def record_provider_id(cur, tenant, request, result):
    provider = result.get('provider_message_id') or result.get('external_message_id')
    if isinstance(provider,str) and re.fullmatch(r'[A-Za-z0-9_-]{5,200}',provider):
        cur.execute("""UPDATE saas_chat_send_receipts SET route=route||jsonb_build_object('group_provider_id',%s::text)
          WHERE tenant_id=%s::uuid AND request_id=%s::uuid AND route ? 'group_participant_id'
            AND (route->>'group_provider_id' IS NULL OR route->>'group_provider_id'=%s)""",
          (provider,tenant,request,provider))


def attach_senders(cur, tenant, actor, conversation, messages):
    """Attribute already-visible rows from exact, accepted group dispatches."""
    outgoing = {(str(m['source_channel_id']),str(m['external_id']).removeprefix('connect:')):m
                for m in messages if m['direction']=='outgoing' and m.get('source_channel_id')}
    def attribute(message,sender):
        message['sending_number']='+'+sender['account_jid'].split('@',1)[0]
        if sender['display_name']:
            message['author_name']=sender['display_name']

    if outgoing:
        channels, external_ids = zip(*outgoing)
        cur.execute("""SELECT s.channel_id::text AS channel_id,s.external_id,p.account_jid,m.display_name
          FROM unnest(%s::uuid[],%s::text[]) AS visible(channel_id,external_id)
          JOIN saas_dialog_episode_sends s USING(channel_id,external_id)
          JOIN saas_chat_send_receipts r ON r.tenant_id=s.tenant_id AND r.request_id=s.request_id
          JOIN saas_dialog_group_participants p ON p.tenant_id=s.tenant_id AND p.episode_id=s.episode_id
            AND p.id::text=r.route->>'group_participant_id' AND p.channel_id=s.channel_id
            AND p.conversation_id=s.conversation_id AND p.actor_id=r.actor_id
          JOIN saas_tenant_members m ON m.tenant_id=p.tenant_id AND m.telegram_id=p.actor_id
          WHERE s.tenant_id=%s::uuid AND s.conversation_id=%s""",(list(channels),list(external_ids),tenant,conversation))
        for sender in cur.fetchall():
            attribute(outgoing[(sender['channel_id'],sender['external_id'])],sender)

    # Use the authorized echo's sender (the phone, event-scoped alternate, or
    # the verified sending session's own LID) together with the dispatch ID.
    # The attribution does not expose the other copy or route.
    echoes={}
    for message in messages:
        raw=message.get('raw') or {}
        source=message.get('source_channel_id')
        peer=raw.get('participantJid') or ''
        alternate=(raw.get('participantAlt') or '') if peer.endswith('@lid') else ''
        provider=raw.get('externalMessageId') or ''
        if (message['direction']=='incoming' and source and raw.get('channelId')==source
                and raw.get('conversationId')==conversation and raw.get('transport')=='beintaskbot_whatsapp'
                and raw.get('fromMe') is False and re.fullmatch(r'[0-9a-f]{64}',raw.get('nativeEventId') or '')
                and re.fullmatch(r'[0-9]{5,22}@(s[.]whatsapp[.]net|lid)',peer)
                and (not alternate or re.fullmatch(r'[1-9][0-9]{5,21}@s[.]whatsapp[.]net',alternate))
                and re.fullmatch(r'[A-Za-z0-9_-]{5,200}',provider)):
            echoes[str(message['id'])]=(source,peer,alternate,provider,message)
    if not echoes:
        return
    sources, peers, alternates, providers, _ = zip(*echoes.values())
    cur.execute("""SELECT visible.id,p.account_jid,max(m.display_name) AS display_name
      FROM unnest(%s::uuid[],%s::uuid[],%s::text[],%s::text[],%s::text[]) visible(id,channel_id,sender,alternate,provider)
      JOIN saas_chat_send_receipts r ON r.tenant_id=%s::uuid
        AND r.route ? 'group_provider_id' AND r.route->>'group_provider_id'=visible.provider
      JOIN saas_dialog_episode_sends s ON s.tenant_id=r.tenant_id AND s.request_id=r.request_id
        AND s.conversation_id=%s
      JOIN saas_dialog_group_participants p ON p.tenant_id=s.tenant_id AND p.episode_id=s.episode_id
        AND p.id::text=r.route->>'group_participant_id' AND p.channel_id=s.channel_id
        AND p.conversation_id=s.conversation_id AND p.actor_id=r.actor_id
        AND (p.account_jid=visible.sender OR p.account_lid=visible.sender
             OR (p.account_lid='' AND p.account_jid=visible.alternate))
        AND (visible.alternate='' OR visible.alternate=p.account_jid)
      JOIN saas_tenant_members m ON m.tenant_id=p.tenant_id AND m.telegram_id=p.actor_id
      WHERE EXISTS(SELECT 1 FROM saas_dialog_group_participants reader
        WHERE reader.tenant_id=p.tenant_id AND reader.episode_id=p.episode_id
          AND reader.channel_id=visible.channel_id AND reader.actor_id=%s)
      GROUP BY visible.id,p.account_jid HAVING count(DISTINCT p.id)=1""",
      (list(echoes),list(sources),list(peers),list(alternates),list(providers),tenant,conversation,actor))
    for sender in cur.fetchall():
        attribute(echoes[str(sender['id'])][4],sender)
