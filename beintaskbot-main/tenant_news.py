"""Company-owned news review. Publishing writes the website only, never Telegram."""
import hashlib
import json
import uuid
from tenant_platform import _connect, _read_tenant_workflow
from tenant_policy import TenantPolicy
from tenant_linear_policy import identifier, TenantLinearError
from tenant_news_ai_schema import ensure_ai_schema as ensure_observer_schema
from tenant_news_policy import fields, public, cursor, position


def access(cur, session: dict) -> tuple[str, int]:
    if not session:
        raise TenantLinearError('İcazə yoxdur.', 403)
    tenant, user = identifier(session['tenant_id']), int(session['telegram_id'])
    cur.execute('''SELECT m.*,t.status AS tenant_status,t.modules FROM saas_tenant_members m
      JOIN saas_tenants t ON t.id=m.tenant_id WHERE m.tenant_id=%s::uuid AND m.telegram_id=%s FOR SHARE OF m,t''', (tenant, user))
    profile = cur.fetchone()
    if not profile or not profile['active'] or profile['tenant_status'] != 'active':
        raise TenantLinearError('İcazə yoxdur.', 403)
    profile = {**profile, 'tenant_id': tenant, 'workflow': _read_tenant_workflow(cur, tenant)}
    if not TenantPolicy(profile).allows('linear'):
        raise TenantLinearError('İcazə yoxdur.', 403)
    cur.execute("SELECT metadata FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='linear' FOR SHARE", (tenant,))
    connection = cur.fetchone() or {}
    binding = connection.get('metadata', {}).get('settings', {}).get('members', {}).get(str(user), {})
    if profile['role'] != 'owner' and binding.get('can_review_news') is not True:
        raise TenantLinearError('Xəbərləri təsdiqləməyə icazə yoxdur.', 403)
    # Archive/review stays accessible after Linear disconnect; no provider credential needed.
    return tenant, user


def listing(session: dict, *, status: str = 'pending', after: str | None = None) -> dict:
    if status not in {'pending', 'published', 'rejected'}:
        raise TenantLinearError('Status düzgün deyil.')
    point = position(after)
    with _connect() as conn:
        ensure_observer_schema(conn)
        with conn.cursor() as cur:
            tenant, user = access(cur, session)
            condition = ' AND (created_at,id)<(%s,%s::uuid)' if point else ''
            cur.execute('SELECT * FROM saas_linear_news WHERE tenant_id=%s::uuid AND status=%s'
                " AND COALESCE(published_at,created_at)>=now()-interval '90 days'" + condition +
                ' ORDER BY created_at DESC,id DESC LIMIT 31', (tenant, status, *(point or ())))
            rows = cur.fetchall()
    return {'tenant_id': tenant, 'user_id': user, 'news': [{**public(r), 'classification': (r.get('source') or {}).get('_news_ai', {})} for r in rows[:30]],
        'after': cursor(rows[29], 'created_at') if len(rows) > 30 else None}


def command(session: dict, data: dict) -> dict:
    action = data.get('action')
    if action not in {'publish', 'reject', 'create_publish'}:
        raise TenantLinearError('Əməliyyat düzgün deyil.')
    if action != 'reject' and data.get('confirm_publication') is not True:
        raise TenantLinearError('Saytda açıq dərc olunmasını təsdiqləyin.')
    content = fields(data) if action != 'reject' else None
    with _connect() as conn:
        ensure_observer_schema(conn)
        with conn.cursor() as cur:
            tenant, user = access(cur, session)
            if action == 'create_publish':
                request_id = identifier(data.get('request_id'))
                key = str(uuid.uuid5(uuid.UUID(tenant), 'news:' + str(user) + ':' + request_id))
            else:
                key = identifier(data.get('id'))
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('tenant-news:' + tenant + ':' + key,))
            cur.execute('''SELECT * FROM saas_linear_news WHERE tenant_id=%s::uuid AND id=%s::uuid
              AND COALESCE(published_at,created_at)>=now()-interval '90 days' FOR UPDATE''', (tenant, key))
            row = cur.fetchone()
            if action == 'create_publish':
                fingerprint = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                if row:
                    if row['source'].get('fingerprint') != fingerprint:
                        raise TenantLinearError('Sorğunun məlumatları dəyişib.', 409)
                else:
                    cur.execute('''INSERT INTO saas_linear_news_seen(tenant_id,issue_id) VALUES(%s::uuid,%s::uuid)
                      ON CONFLICT DO NOTHING RETURNING issue_id''', (tenant, key))
                    if not cur.fetchone():
                        raise TenantLinearError('Bu xəbər əvvəllər yaradılıb. Təkrar dərc olunmadı.', 409)
                    cur.execute('''INSERT INTO saas_linear_news(tenant_id,id,issue_id,identifier,project_name,title,summary,url,source,
                      status,reviewed_by,published_at) VALUES(%s::uuid,%s::uuid,%s::uuid,'',%s,%s,%s,%s,%s::jsonb,'published',%s,now()) RETURNING *''',
                      (tenant, key, key, content['project_name'], content['title'], content['summary'], content['url'],
                       json.dumps({'manual': True, 'fingerprint': fingerprint}), user))
                    row = cur.fetchone()
            else:
                if not row:
                    raise TenantLinearError('Xəbər tapılmadı.', 404)
                target = 'published' if action == 'publish' else 'rejected'
                if row['status'] == target:
                    if content and any(row[k] != value for k, value in content.items()):
                        raise TenantLinearError('Xəbər artıq dərc edilib.', 409)
                elif row['status'] != 'pending' or str(row['updated_at']) != str(data.get('expected_updated_at') or ''):
                    raise TenantLinearError('Xəbər dəyişib. Siyahını yeniləyin.', 409)
                elif action == 'reject':
                    cur.execute("UPDATE saas_linear_news SET status='rejected',reviewed_by=%s,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid RETURNING *", (user, tenant, key))
                    row = cur.fetchone()
                else:
                    cur.execute('''UPDATE saas_linear_news SET title=%s,summary=%s,project_name=%s,url=%s,status='published',
                      reviewed_by=%s,published_at=now(),updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid RETURNING *''',
                      (content['title'], content['summary'], content['project_name'], content['url'], user, tenant, key))
                    row = cur.fetchone()
        conn.commit()
    return {'tenant_id': tenant, 'user_id': user, 'news': public(row)}


def published(tenant: str, *, after: str | None = None) -> dict:
    tenant = identifier(tenant); point = position(after)
    with _connect() as conn:
        ensure_observer_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM saas_tenants WHERE id=%s::uuid AND status='active'", (tenant,))
            if not cur.fetchone():
                raise TenantLinearError('Səhifə tapılmadı.', 404)
            condition = ' AND (published_at,id)<(%s,%s::uuid)' if point else ''
            cur.execute("SELECT * FROM saas_linear_news WHERE tenant_id=%s::uuid AND status='published'"
                " AND published_at>=now()-interval '90 days'" + condition + ' ORDER BY published_at DESC,id DESC LIMIT 31', (tenant, *(point or ())))
            rows = cur.fetchall()
    # Deliberately do not serialize source, Account, Operator, reviewer or company owner.
    return {'news': [{k: v for k, v in public(r).items() if k not in {'status', 'created_at', 'updated_at'}} for r in rows[:30]],
        'after': cursor(rows[29], 'published_at') if len(rows) > 30 else None}
