"""Read-only, exact-route material scope shared by case UI, consultation and AI.

Callers retain their existing authorization. Public sharing selects its own
explicit subset; it never changes this internal material scope.
"""
import hashlib
import json
from tenant_linear_policy import TenantLinearError, identifier

MEDIA_TYPES = ('image', 'video', 'audio', 'voice', 'document')
SNAPSHOT_LIMIT = 50
_NON_BOUNDARY_EVENTS = ('support-media-picker:%', 'public-case-selection:%',
                        'consultation-attachment-selection:%')


def load_scope(cur, tenant, ticket_id, channel_id=None, conversation_id=None):
    ticket_id = identifier(ticket_id)
    cur.execute('''SELECT t.id,t.channel_id::text AS channel_id,t.conversation_id,
        t.history_scope,t.status,c.lead_id
      FROM saas_support_tickets t JOIN saas_connect_conversations c
        ON c.tenant_id=t.tenant_id AND c.connect_channel_id=t.channel_id
          AND c.conversation_id=t.conversation_id
      WHERE t.tenant_id=%s::uuid AND t.id=%s::uuid''', (tenant, ticket_id))
    row = cur.fetchone()
    if not row or (channel_id is not None and str(row['channel_id']) != str(channel_id)) or (
            conversation_id is not None and row['conversation_id'] != conversation_id):
        raise TenantLinearError('Обращение недоступно в этом диалоге.', 404)
    kind = row['history_scope']
    if kind == 'legacy':
        # Existing intentional handoff episodes remain bounded. Old attachment
        # and share selections were additions, not requests to hide history.
        cur.execute('''SELECT EXISTS(SELECT 1 FROM saas_support_ticket_messages
          WHERE tenant_id=%s::uuid AND ticket_id=%s::uuid
            AND event_key NOT LIKE %s AND event_key NOT LIKE %s
            AND event_key NOT LIKE %s) AS bounded''',
            (tenant, ticket_id, *_NON_BOUNDARY_EVENTS))
        kind = 'selected' if (cur.fetchone() or {}).get('bounded') else 'conversation'
    return {'tenant_id': str(tenant), 'ticket_id': ticket_id,
            'channel_id': str(row['channel_id']), 'conversation_id': row['conversation_id'],
            'lead_id': int(row['lead_id']), 'kind': kind, 'status': row['status']}


def predicate(scope):
    sql = '''m.tenant_id=%s::uuid AND m.kommo_lead_id=%s AND m.channel=%s
      AND m.direction IN ('incoming','outgoing')'''
    args = [scope['tenant_id'], scope['lead_id'], 'connect:' + scope['channel_id']]
    if scope['kind'] != 'conversation':
        sql += ''' AND EXISTS(SELECT 1 FROM saas_support_ticket_messages cm
          WHERE cm.tenant_id=m.tenant_id AND cm.ticket_id=%s::uuid AND cm.message_id=m.id)'''
        args.append(scope['ticket_id'])
    return sql, args


def messages(cur, scope, limit=80, before=None):
    """Return the newest bounded page chronologically; never download media."""
    limit = max(1, min(int(limit), 1000))
    where, args = predicate(scope)
    if before:
        where += ''' AND (COALESCE(m.happened_at,m.created_at),m.id)<(
          SELECT COALESCE(happened_at,created_at),id FROM saas_crm_messages
          WHERE tenant_id=%s::uuid AND kommo_lead_id=%s AND channel=%s AND id=%s::uuid)'''
        args.extend([scope['tenant_id'], scope['lead_id'], 'connect:' + scope['channel_id'], identifier(before)])
    cur.execute('''SELECT m.id::text AS id,m.external_id,m.direction,m.author_name,m.body,m.message_type,
        COALESCE(m.happened_at,m.created_at) AS happened_at,m.created_at,m.channel,m.raw,m.media_url
      FROM saas_crm_messages m WHERE ''' + where + '''
      ORDER BY COALESCE(m.happened_at,m.created_at) DESC,m.id DESC LIMIT %s''', (*args, limit))
    return list(reversed(cur.fetchall()))


def metadata(cur, scope):
    where, args = predicate(scope)
    cur.execute('''SELECT count(*) AS message_count,
        count(*) FILTER(WHERE m.message_type IN ('image','audio','voice','video','document')) AS media_count
      FROM saas_crm_messages m WHERE ''' + where, tuple(args))
    counts = cur.fetchone()
    cur.execute('''SELECT m.id,COALESCE(m.happened_at,m.created_at) AS at
      FROM saas_crm_messages m WHERE ''' + where + '''
      ORDER BY COALESCE(m.happened_at,m.created_at),m.id LIMIT 1''', tuple(args))
    first = cur.fetchone()
    cur.execute('''SELECT m.id FROM saas_crm_messages m WHERE ''' + where + '''
      ORDER BY COALESCE(m.happened_at,m.created_at) DESC,m.id DESC LIMIT 1''', tuple(args))
    last = cur.fetchone()
    earlier = False
    if first:
        cur.execute('''SELECT EXISTS(SELECT 1 FROM saas_crm_messages m
          WHERE m.tenant_id=%s::uuid AND m.kommo_lead_id=%s AND m.channel=%s
            AND m.direction IN ('incoming','outgoing')
            AND (COALESCE(m.happened_at,m.created_at),m.id)<(%s::timestamptz,%s::uuid)) AS earlier''',
            (scope['tenant_id'], scope['lead_id'], 'connect:' + scope['channel_id'], first['at'], first['id']))
        earlier = bool((cur.fetchone() or {}).get('earlier'))
    return {'kind': scope['kind'], 'message_count': int(counts['message_count']),
            'media_count': int(counts['media_count']),
            'first_message_id': str(first['id']) if first else None,
            'last_message_id': str(last['id']) if last else None,
            'initial_limit': SNAPSHOT_LIMIT if scope['kind'] == 'snapshot' else None,
            'has_earlier_source': earlier}


def fingerprint(cur, scope, rows=None):
    """Bind a generated summary to exact consumed rows and case boundaries."""
    rows = messages(cur, scope, limit=80) if rows is None else rows
    value = {'scope': metadata(cur, scope), 'rows': [
        {key: row.get(key) for key in ('id','external_id','direction','body','message_type','happened_at','raw')}
        for row in rows]}
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def selection(cur, tenant, ticket_id, channel_id, conversation_id, message_ids):
    """Validate a contiguous <=30-message subset of an authorized case route."""
    scope = load_scope(cur, tenant, ticket_id, channel_id, conversation_id)
    return _selection(cur, scope, message_ids, 30)


def _selection(cur, scope, message_ids, maximum):
    if not isinstance(message_ids, (list, tuple)) or not 1 <= len(message_ids) <= maximum:
        raise TenantLinearError('Выберите ограниченный фрагмент обращения.', 400)
    keys = [identifier(value) for value in message_ids]
    if len(set(keys)) != len(keys):
        raise TenantLinearError('Сообщения фрагмента повторяются.', 400)
    where, args = predicate(scope)
    cur.execute('''SELECT m.id::text AS id,m.external_id,m.direction,m.author_name,m.body,m.message_type,
        COALESCE(m.happened_at,m.created_at) AS happened_at,m.created_at,m.channel,m.raw,m.media_url,
        COALESCE(m.happened_at,m.created_at) AS ordered_at
      FROM saas_crm_messages m WHERE ''' + where + ''' AND m.id=ANY(%s::uuid[])
      ORDER BY COALESCE(m.happened_at,m.created_at),m.id''', (*args, keys))
    rows = cur.fetchall()
    if len(rows) != len(keys) or [str(row['id']) for row in rows] != keys:
        raise TenantLinearError('Выбранные сообщения недоступны или изменился их порядок.', 409)
    cur.execute('''SELECT m.id FROM saas_crm_messages m WHERE ''' + where + '''
      AND (COALESCE(m.happened_at,m.created_at),m.id)>=(%s::timestamptz,%s::uuid)
      AND (COALESCE(m.happened_at,m.created_at),m.id)<=(%s::timestamptz,%s::uuid)
      ORDER BY COALESCE(m.happened_at,m.created_at),m.id LIMIT %s''',
      (*args, rows[0]['ordered_at'], rows[0]['id'], rows[-1]['ordered_at'], rows[-1]['id'], maximum + 1))
    if [str(row['id']) for row in cur.fetchall()] != keys:
        raise TenantLinearError('Выберите непрерывный фрагмент обращения.', 409)
    return rows
