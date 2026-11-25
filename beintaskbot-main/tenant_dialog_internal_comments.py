"""Staff-only dialogue comments. Never write customer message or read-cursor tables."""
from uuid import UUID

from psycopg.errors import LockNotAvailable, QueryCanceled, UndefinedTable

from tenant_linear_policy import TenantLinearError


MAX_LENGTH = 2000
PAGE_LIMIT = 60


def provision_schema(cur):
    cur.execute("""CREATE TABLE IF NOT EXISTS saas_dialog_internal_comments (
        tenant_id UUID NOT NULL,
        id UUID NOT NULL,
        provider TEXT NOT NULL CONSTRAINT dialog_comments_source_check CHECK (provider IN ('connect', 'kommo', 'operator_widget')),
        channel_id TEXT NOT NULL,
        conversation_id TEXT NOT NULL,
        lead_id BIGINT NOT NULL,
        actor_id BIGINT NOT NULL,
        author_name TEXT NOT NULL,
        note TEXT NOT NULL CHECK (char_length(note) BETWEEN 1 AND 2000),
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (tenant_id, id)
    )""")
    cur.execute("""DO $$ BEGIN
        IF EXISTS (SELECT FROM pg_constraint
            WHERE conrelid='saas_dialog_internal_comments'::regclass
              AND conname='saas_dialog_internal_comments_provider_check') THEN
            ALTER TABLE saas_dialog_internal_comments
              DROP CONSTRAINT saas_dialog_internal_comments_provider_check,
              ADD CONSTRAINT dialog_comments_source_check
                CHECK (provider IN ('connect', 'kommo', 'operator_widget'));
        END IF;
    END $$""")
    cur.execute("""CREATE INDEX IF NOT EXISTS saas_dialog_internal_comments_route_idx
        ON saas_dialog_internal_comments
        (tenant_id, provider, channel_id, conversation_id, lead_id, created_at DESC, id DESC)""")


def _event(row):
    return {'id': str(row['id']), 'note': row['note'], 'at': row['created_at'].isoformat(),
            'employee_name': row['author_name']}


def _scope(tenant, provider, channel, conversation, lead):
    # A Kommo dialogue is keyed by its deal. Its latest CRM conversation can change.
    return (tenant, provider, channel if provider == 'connect' else '',
            conversation if provider == 'connect' else '', lead)


def save(profile, data):
    from tenant_dialog_access import require_source, scope
    from tenant_operator_access import access, assert_route
    from tenant_platform import _connect

    if set(data) != {'provider', 'channel_id', 'conversation_id', 'lead_id', 'request_id', 'note'}:
        raise TenantLinearError('Некорректный запрос.', 400)
    provider, channel, conversation = (data[key] for key in ('provider', 'channel_id', 'conversation_id'))
    lead = int(data['lead_id'])
    ident = str(UUID(data['request_id']))
    note = data['note'].strip() if isinstance(data['note'], str) else ''
    if provider == 'operator_widget':
        from tenant_operator_widget_queue import authorized
        if not note or len(note)>MAX_LENGTH or '\x00' in note or any(0xD800<=ord(c)<=0xDFFF for c in note):
            raise TenantLinearError('Укажите корректный комментарий до 2000 символов.',400)
        with _connect() as conn, conn.cursor() as cur:
            row,actor,policy=authorized(cur,profile,data,update=True,lock=True)
            if row['state']!='accepted' or row['operator_id']!=actor:
                raise TenantLinearError('Сначала примите диалог.',409)
            keys=(str(row['tenant_id']),provider,str(row['instance']),row['identity'],row['chat_id'])
            name=str(policy.profile.get('display_name') or '').strip()[:120]
            return {'comment':save_authorized(cur,keys,ident,actor,name,note)}
    if (provider not in ('connect', 'kommo') or not isinstance(channel, str) or not isinstance(conversation, str)
            or provider == 'connect' and (not channel or not conversation)
            or len(channel) > 200 or len(conversation) > 220 or lead <= 0 or not 0 < len(note) <= MAX_LENGTH
            or '\x00' in note or any(0xD800 <= ord(char) <= 0xDFFF for char in note)):
        raise TenantLinearError('Укажите корректный комментарий до 2000 символов.', 400)
    with _connect() as conn, conn.cursor() as cur:
        tenant, actor, policy = access(cur, profile, 'dialogs', action='update')
        if scope(policy, 'view') == 'none':
            raise TenantLinearError('Диалог недоступен.', 403)
        require_source(cur, tenant, policy, provider, channel, 'view')
        assert_route(cur, tenant, policy, provider, conversation, channel, lead, action='update')
        if provider == 'kommo':
            from tenant_operator_dialogs import projection, runtime_flag
            sql, params = projection(cur, policy, include_contact_names=False)
            cur.execute(sql + """SELECT 1 FROM visible WHERE provider=%s
                AND channel_id=%s AND conversation_id=%s AND lead_id=%s""",
                (tenant, tenant, *params, runtime_flag('AI_AGENTS_ENABLED'), provider, channel, conversation, lead))
            if not cur.fetchone():
                raise TenantLinearError('Диалог недоступен.', 404)
        keys = _scope(tenant, provider, channel, conversation, lead)
        name = str(policy.profile.get('display_name') or '').strip()[:120]
        result = save_authorized(cur, keys, ident, actor, name, note)
        conn.commit()
    return {'comment': result}


def save_authorized(cur, keys, ident, actor, name, note):
    """Insert or recover a note after the caller has authorized its exact route."""
    cur.execute("""INSERT INTO saas_dialog_internal_comments
        (tenant_id, provider, channel_id, conversation_id, lead_id, id, actor_id, author_name, note)
        VALUES (%s::uuid,%s,%s,%s,%s,%s::uuid,%s,%s,%s)
        ON CONFLICT (tenant_id,id) DO NOTHING RETURNING id,author_name,note,created_at""",
        (*keys, ident, actor, name, note))
    row = cur.fetchone()
    if row is None:
        cur.execute("""SELECT id,author_name,note,created_at FROM saas_dialog_internal_comments
            WHERE tenant_id=%s::uuid AND provider=%s AND channel_id=%s AND conversation_id=%s
            AND lead_id=%s AND id=%s::uuid AND actor_id=%s AND note=%s""",
            (*keys, ident, actor, note))
        row = cur.fetchone()
        if row is None:
            raise TenantLinearError('Запрос уже использован.', 409)
    return _event(row)


def page(conn, cur, tenant, provider, channel, conversation, lead, **window):
    """Called only after the exact dialogue history has passed current authorization."""
    from tenant_dialog_operator_timeline import history_window

    try:
        with conn.transaction():
            cur.execute("""SELECT current_setting('statement_timeout') AS previous,setting::integer AS milliseconds
                FROM pg_settings WHERE name='statement_timeout'""")
            setting = cur.fetchone()
            cur.execute("SELECT set_config('statement_timeout',%s,true)", (f"{min(setting['milliseconds'] or 200, 200)}ms",))
            stamps = history_window(cur, tenant, conversation, **window)
            rows = []
            if stamps is not None:
                lower, upper = stamps
                cur.execute("""SELECT id,author_name,note,created_at FROM saas_dialog_internal_comments
                    WHERE tenant_id=%s::uuid AND provider=%s AND channel_id=%s AND conversation_id=%s AND lead_id=%s
                    AND (%s::timestamptz IS NULL OR created_at>=%s::timestamptz)
                    AND (%s::timestamptz IS NULL OR created_at<%s::timestamptz)
                    ORDER BY created_at DESC,id DESC LIMIT %s""",
                    (*_scope(tenant, provider, channel, conversation, lead), lower, lower, upper, upper, PAGE_LIMIT+1))
                rows = cur.fetchall()
            cur.execute("SELECT set_config('statement_timeout',%s,true)", (setting['previous'],))
    except (UndefinedTable, LockNotAvailable, QueryCanceled):
        return {}
    shown = rows[:PAGE_LIMIT]
    result = {'internal_comments': [_event(row) for row in reversed(shown)]}
    if len(rows) > PAGE_LIMIT:
        result['internal_comments_omitted_at'] = shown[-1]['created_at'].isoformat()
    return result
