"""Personal notification ledger reads; no transports, subscriptions or AI calls."""
import base64
from datetime import datetime
import json
from urllib.parse import urlencode

from tenant_ai_agent_access import tenant_status_allows_management
from tenant_linear_policy import TenantLinearError, identifier
from tenant_platform import _connect, _read_tenant_workflow
from tenant_policy import TenantPolicy


def _access(cur, profile):
    if not isinstance(profile, dict):
        raise TenantLinearError('Нет доступа.', 403)
    tenant = identifier(profile.get('tenant_id'))
    try:
        actor = int(profile.get('telegram_id'))
    except (TypeError, ValueError):
        raise TenantLinearError('Нет доступа.', 403) from None
    cur.execute('''SELECT m.*,t.status AS tenant_status,t.modules,t.onboarding
      FROM saas_tenant_members m JOIN saas_tenants t ON t.id=m.tenant_id
      WHERE m.tenant_id=%s::uuid AND m.telegram_id=%s FOR SHARE OF m,t''', (tenant, actor))
    member = cur.fetchone()
    if not member or member.get('active') is not True or not tenant_status_allows_management(member.get('tenant_status')):
        raise TenantLinearError('Нет доступа к активной компании.', 403)
    return tenant, actor, TenantPolicy({**member, 'tenant_id': tenant,
        'workflow': _read_tenant_workflow(cur, tenant)})


def _parameter_bytes(column):
    """Compare a query value exactly as URLSearchParams decodes it.

    Encode literal characters and percent escapes to hex rather than decoding
    arbitrary bytes as UTF-8. Malformed or invalid UTF-8 escapes cannot abort a
    whole notification read, and double encoding never matches a different JID.
    The SQL column expression is fixed by this module, never supplied by users.
    """
    # Literal percent signs in parametrized SQL are parsed by psycopg before
    # PostgreSQL sees the regex. Construct them in SQL to keep both drivers
    # on the same exact URL-decoding predicate.
    return """(SELECT string_agg(CASE WHEN part[1] ~ ('^' || chr(37) || '[0-9A-Fa-f]{2}$')
      THEN lower(substring(part[1] from 2))
      ELSE encode(convert_to(replace(part[1],'+',' '),'UTF8'),'hex') END,
      '' ORDER BY part_order)
      FROM regexp_matches(""" + column + """, '(' || chr(37) || '[0-9A-Fa-f]{2}|.)', 'g')
        WITH ORDINALITY AS query_parts(part,part_order))"""


def _eligible(cur, tenant, actor, policy):
    """One predicate for rows, unread count and read mutation, including revoked access.

    CTE inlining lets the unread count use the existing personal/read_at index;
    pagination never returns the employee's full notification history.
    """
    modules = ('tasks', 'deals', 'customers', 'dialogs', 'support', 'hot_orders',
               'linear', 'finance', 'employees', 'settings', 'integrations',
               'ai_agents', 'night_report')
    module_case = 'CASE p.module ' + ' '.join(
        "WHEN '" + module + "' THEN " + ('TRUE' if policy.allows(module) else 'FALSE')
        for module in modules) + " WHEN 'account' THEN TRUE WHEN 'approvals' THEN " + (
        'TRUE' if policy.privileged and any(policy.allows(m) for m in ('tasks', 'deals', 'hot_orders')) else 'FALSE') + ' ELSE FALSE END'
    cur.execute("SELECT to_regclass('saas_dialog_consultation_replies') IS NOT NULL AS replies, to_regclass('saas_support_tickets') IS NOT NULL AS tickets, to_regclass('saas_connect_dialog_links') IS NOT NULL AS links")
    ready = cur.fetchone() or {}
    consult_joins = ''
    consult_columns = 'NULL::text AS reply_id,NULL::text AS consultation_id,NULL::text AS source_channel,NULL::text AS source_conversation,NULL::bigint AS source_lead,NULL::text AS case_id'
    consult_guard = 'FALSE'
    if ready.get('replies'):
        consult_joins = '''LEFT JOIN saas_dialog_consultation_replies r ON r.tenant_id=n.tenant_id
          AND r.id::text=CASE WHEN split_part(n.event_key,':',1)='dialog_consultation' THEN split_part(n.event_key,':',2) ELSE '' END
          LEFT JOIN saas_internal_consultations c ON c.tenant_id=r.tenant_id AND c.id=r.consultation_id
          LEFT JOIN saas_connect_conversations v ON v.tenant_id=c.tenant_id
            AND v.connect_channel_id=c.channel_id AND v.conversation_id=c.conversation_id
          LEFT JOIN saas_connect_channels ch ON ch.tenant_id=v.tenant_id AND ch.connect_channel_id=v.connect_channel_id'''
        consult_columns = 'r.id::text AS reply_id,c.id::text AS consultation_id,c.channel_id::text AS source_channel,c.conversation_id AS source_conversation,v.lead_id AS source_lead,c.case_id::text AS case_id'
        consult_guard = ('TRUE' if policy.allows('dialogs') else 'FALSE') + ''' AND r.id IS NOT NULL AND v.lead_id IS NOT NULL AND ch.active=TRUE'''
        consult_guard += ' AND (c.case_id IS NULL OR ' + ('TRUE' if policy.allows('support') else 'FALSE') + ')'
        if ready.get('links'):
            consult_guard += ''' AND NOT EXISTS(SELECT 1 FROM saas_connect_dialog_links dl
              WHERE dl.tenant_id=c.tenant_id AND dl.channel_id=c.channel_id AND dl.source_id=c.conversation_id AND dl.active)'''
    ticket_guard = "p.target_ticket=''"
    if ready.get('tickets'):
        ticket_guard += ''' OR EXISTS(SELECT 1 FROM saas_support_tickets st
          WHERE st.tenant_id=p.tenant_id AND st.id::text=p.target_ticket)'''
    dialog_guard = '''(p.target_lead='' AND p.target_channel='' AND p.target_conversation='') OR EXISTS(
      SELECT 1 FROM saas_connect_conversations cv JOIN saas_connect_channels cc
        ON cc.tenant_id=cv.tenant_id AND cc.connect_channel_id=cv.connect_channel_id
      WHERE cv.tenant_id=p.tenant_id AND cv.lead_id::text=p.target_lead
        AND cv.connect_channel_id::text=p.target_channel AND p.target_conversation<>'' AND cc.active=TRUE
        AND encode(convert_to(cv.conversation_id,'UTF8'),'hex')=''' + _parameter_bytes('p.target_conversation') + ')'
    deal_scope, deal_args = [], []
    if policy.profile.get('role') == 'owner':
        deal_scope.append('TRUE')
    else:
        for item in policy.pipeline_scope():
            clause = 'd.pipeline_id=%s'; deal_args.append(item['pipeline_id'])
            if item['status_ids']:
                clause += ' AND d.status_id=ANY(%s::bigint[])'; deal_args.append(item['status_ids'])
            deal_scope.append('(' + clause + ')')
    deal_guard = '''p.target_deal='' OR EXISTS(SELECT 1 FROM saas_crm_deals d
      WHERE d.tenant_id=p.tenant_id AND d.kommo_lead_id::text=p.target_deal AND d.deleted_at IS NULL AND (''' + (' OR '.join(deal_scope) or 'FALSE') + '))'
    sql = '''WITH personal AS NOT MATERIALIZED (
      SELECT n.*,''' + consult_columns + ''',
        split_part(n.event_key,':',1)='dialog_consultation' AS is_reply,
        (''' + consult_guard + ''') AS reply_allowed,
        COALESCE(substring(n.target_url from '[?&]view=([^&#]+)'),'') AS target_view,
        COALESCE(substring(n.target_url from '[?&]support_ticket_id=([^&#]+)'),'') AS target_ticket,
        COALESCE(substring(n.target_url from '[?&]lead_id=([^&#]+)'),'') AS target_lead,
        COALESCE(substring(n.target_url from '[?&]channel_id=([^&#]+)'),'') AS target_channel,
        COALESCE(substring(n.target_url from '[?&]conversation_id=([^&#]+)'),'') AS target_conversation,
        COALESCE(substring(n.target_url from '[?&]deal_id=([^&#]+)'),'') AS target_deal
      FROM saas_tenant_notifications n ''' + consult_joins + '''
      WHERE n.tenant_id=%s::uuid AND n.telegram_id=%s
    ), classified AS NOT MATERIALIZED (
      SELECT personal.*,CASE WHEN is_reply THEN 'dialogs'
        WHEN target_view<>'' THEN target_view
        WHEN event_key ~ '(^|:)task(_|:)' THEN 'tasks'
        WHEN event_key ~ '(^|:)hot_order(_|:)' THEN 'hot_orders'
        WHEN event_key ~ '(^|:)linear(_|:)' THEN 'linear'
        WHEN event_key ~ '^(ai_consultation|internal_consultation):' THEN 'support'
        WHEN event_key ~ '^(ai_handoff|new_lead):' THEN 'deals'
        ELSE 'account' END AS module FROM personal
    ), eligible AS NOT MATERIALIZED (
      SELECT p.* FROM classified p WHERE CASE WHEN p.is_reply THEN p.reply_allowed ELSE
        (p.target_url='' OR p.target_url ~ '^/app([?][^#[:cntrl:]]*)?$') AND (''' + module_case + ''')
        AND (p.target_view<>'support' OR (''' + ticket_guard + '''))
        AND (p.target_view<>'dialogs' OR (''' + dialog_guard + '''))
        AND (p.target_view NOT IN ('deals','customers') OR (''' + deal_guard + ''')) END
    ) '''
    return sql, [tenant, actor, *deal_args]


def _cursor(value, status):
    if not value:
        return None
    try:
        if not isinstance(value, str) or len(value) > 500:
            raise ValueError
        decoded = json.loads(base64.urlsafe_b64decode(value + '=' * (-len(value) % 4)))
        if set(decoded) != {'at', 'id', 'status'} or decoded['status'] != status:
            raise ValueError
        stamp = datetime.fromisoformat(decoded['at'].replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError
        return stamp.isoformat(), identifier(decoded['id'])
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        raise TenantLinearError('Обновите список уведомлений.', 400) from None


def _public(row):
    value = {key: str(row[key]) if row.get(key) is not None else None
             for key in ('id', 'event_key', 'created_at', 'read_at', 'reply_id')}
    value.update(title=str(row.get('title') or '')[:200], body=str(row.get('body') or '')[:1000],
                 target_url=str(row.get('target_url') or ''), kind='consultation' if row.get('is_reply') else row.get('module') or 'account')
    if row.get('is_reply'):
        value['consultation_id'] = str(row['consultation_id'])
        value['case_id'] = str(row['case_id']) if row.get('case_id') else None
        params = {'view': 'dialogs', 'open_dialog': '1', 'provider': 'connect',
                  'lead_id': row['source_lead'], 'channel_id': row['source_channel'],
                  'conversation_id': row['source_conversation'], 'consultation_reply_id': row['reply_id']}
        if row.get('case_id'):
            params['support_ticket_id'] = row['case_id']
        value['target_url'] = '/app?' + urlencode(params)
    return value


def listing(profile, status='unread', limit=20, before=''):
    if status not in {'unread', 'all'}:
        raise TenantLinearError('Некорректный фильтр уведомлений.', 400)
    try:
        limit = int(limit)
        if not 1 <= limit <= 50:
            raise ValueError
    except (ValueError, TypeError):
        raise TenantLinearError('Некорректный размер страницы.', 400) from None
    cursor = _cursor(before, status)
    with _connect() as conn:
        with conn.cursor() as cur:
            tenant, actor, policy = _access(cur, profile)
            sql, params = _eligible(cur, tenant, actor, policy)
            where = ["read_at IS NULL"] if status == 'unread' else ['TRUE']
            if cursor:
                where.append('(created_at,id)<(%s::timestamptz,%s::uuid)'); params += list(cursor)
            cur.execute(sql + '''SELECT page.*,totals.unread_count FROM
              (SELECT count(*) AS unread_count FROM eligible WHERE read_at IS NULL) totals
              LEFT JOIN LATERAL(SELECT * FROM eligible WHERE ''' + ' AND '.join(where) + '''
                ORDER BY created_at DESC,id DESC LIMIT %s) page ON TRUE''', (*params, limit + 1))
            rows = cur.fetchall()
            unread = int(rows[0]['unread_count']) if rows else 0
            rows = [row for row in rows if row.get('id')]
    next_before = None
    if len(rows) > limit:
        last = rows[limit - 1]
        raw = json.dumps({'at': str(last['created_at']), 'id': str(last['id']), 'status': status}).encode()
        next_before = base64.urlsafe_b64encode(raw).decode().rstrip('=')
    return {'notifications': [_public(row) for row in rows[:limit]], 'unread_count': unread,
            'next_before': next_before, 'status': status}


def mark_read(profile, notification_id):
    key = identifier(notification_id)
    with _connect() as conn:
        with conn.cursor() as cur:
            tenant, actor, policy = _access(cur, profile)
            sql, params = _eligible(cur, tenant, actor, policy)
            cur.execute(sql + 'SELECT id,reply_id,event_key FROM eligible WHERE id=%s::uuid', (*params, key))
            row = cur.fetchone()
            if not row:
                raise TenantLinearError('Уведомление недоступно.', 404)
            cur.execute('''UPDATE saas_tenant_notifications SET read_at=COALESCE(read_at,now())
              WHERE tenant_id=%s::uuid AND telegram_id=%s AND (id=%s::uuid OR (%s AND event_key=%s))
              RETURNING read_at''', (tenant, actor, key, bool(row.get('reply_id')), row['event_key']))
            stamp = cur.fetchone()['read_at']
            if row.get('reply_id'):
                cur.execute('''INSERT INTO saas_dialog_consultation_reads(tenant_id,reply_id,actor_id)
                  VALUES(%s::uuid,%s::uuid,%s) ON CONFLICT DO NOTHING''', (tenant, row['reply_id'], actor))
            cur.execute(sql + 'SELECT count(*) AS unread_count FROM eligible WHERE read_at IS NULL', tuple(params))
            unread = int(cur.fetchone()['unread_count'])
        conn.commit()
    return {'id': key, 'read_at': str(stamp), 'unread_count': unread}
