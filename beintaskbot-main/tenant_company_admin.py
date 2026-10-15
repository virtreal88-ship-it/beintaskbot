"""Owner-only company management with tenant-scoped authorization."""
from __future__ import annotations

import uuid

from tenant_platform import TenantPlatformError, _connect, _ensure_schema, _json


def _warnings(cur, tenant_id: str) -> list[str]:
    warnings = []
    cur.execute('SELECT count(*) AS n FROM saas_tenant_members WHERE tenant_id=%s::uuid', (tenant_id,))
    members = int(cur.fetchone()['n'])
    if members > 1:
        warnings.append(f'{members - 1} əməkdaş')
    cur.execute("SELECT metadata FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='connect'", (tenant_id,))
    connect = cur.fetchone()
    if connect:
        metadata = connect.get('metadata') if isinstance(connect.get('metadata'), dict) else {}
        channel_ids = metadata.get('channel_ids') if isinstance(metadata.get('channel_ids'), list) else []
        if channel_ids:
            warnings.append(f'{len(channel_ids)} WhatsApp kanalı')
    cur.execute("SELECT count(*) AS n FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider IN ('chat_ai','ai_agents') AND status='connected'", (tenant_id,))
    if int(cur.fetchone()['n']):
        warnings.append('AI bağlantısı/agent ayarları')
    for table, label in (('saas_crm_deals','sövdələşmə'), ('saas_crm_tasks','tapşırıq'), ('saas_crm_messages','çat mesajı')):
        cur.execute(f'SELECT count(*) AS n FROM {table} WHERE tenant_id=%s::uuid', (tenant_id,))
        count = int(cur.fetchone()['n'])
        if count:
            warnings.append(f'{count} {label}')
    # Optional modules create these ledgers lazily; include them in the
    # warning without making company management depend on those modules.
    for table, label in (
        ('saas_finance_entries', 'maliyyə qeydi'),
        ('saas_chat_send_receipts', 'mesaj göndəriş qeydi'),
        ('saas_deal_completion_commands', 'əməliyyat tarixçəsi'),
        ('saas_crm_notice_events', 'bildiriş tarixçəsi'),
    ):
        cur.execute('SELECT to_regclass(%s) AS table_name', (f'public.{table}',))
        if cur.fetchone()['table_name']:
            cur.execute(f'SELECT count(*) AS n FROM {table} WHERE tenant_id=%s::uuid', (tenant_id,))
            count = int(cur.fetchone()['n'])
            if count:
                warnings.append(f'{count} {label}')
    return warnings


def list_owned(profile: dict) -> list[dict]:
    actor, current = int(profile['telegram_id']), str(uuid.UUID(str(profile['tenant_id'])))
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT t.id,t.name,t.created_at,m.role
              FROM saas_tenants t JOIN saas_tenant_members m ON m.tenant_id=t.id
              WHERE m.telegram_id=%s AND m.active=TRUE AND (m.role='owner' OR t.owner_telegram_id=%s)
              ORDER BY t.created_at,t.id''', (actor, actor))
            rows = list(cur.fetchall())
            primary_id = str(rows[0]['id']) if rows else ''
            result = []
            for row in rows:
                tenant_id = str(row['id'])
                warnings = _warnings(cur, tenant_id)
                result.append({'id': tenant_id, 'name': str(row['name']),
                    'created_at': row['created_at'].isoformat() if row.get('created_at') else None,
                    'is_current': tenant_id == current, 'is_primary': tenant_id == primary_id,
                    'warnings': warnings, 'can_delete': tenant_id not in {current, primary_id}})
    return result


def manage(profile: dict, payload: dict) -> dict:
    actor, current = int(profile['telegram_id']), str(uuid.UUID(str(profile['tenant_id'])))
    try:
        target = str(uuid.UUID(str(payload.get('tenant_id') or '')))
    except (TypeError, ValueError):
        raise TenantPlatformError('Şirkət identifikatoru düzgün deyil.') from None
    action = str(payload.get('action') or '')
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT t.id,t.name,t.created_at FROM saas_tenants t
              JOIN saas_tenant_members m ON m.tenant_id=t.id
              WHERE t.id=%s::uuid AND m.telegram_id=%s AND m.active=TRUE
                AND (m.role='owner' OR t.owner_telegram_id=%s) FOR UPDATE OF t''', (target, actor, actor))
            row = cur.fetchone()
            if not row:
                raise TenantPlatformError('Bu şirkəti idarə etmək üçün sahib icazəsi yoxdur.')
            if action == 'rename':
                name = str(payload.get('name') or '').strip()[:120]
                if len(name) < 2:
                    raise TenantPlatformError('Şirkət adı ən azı 2 simvol olmalıdır.')
                cur.execute('''UPDATE saas_tenants SET name=%s,updated_at=now(),
                  onboarding=jsonb_set(COALESCE(onboarding,'{}'::jsonb),'{company_name}',to_jsonb(%s::text),TRUE)
                  WHERE id=%s::uuid''', (name,name,target))
                cur.execute('''INSERT INTO saas_tenant_audit_events(id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
                  VALUES(%s,%s::uuid,%s,'company_renamed','company',%s,%s::jsonb)''',
                  (uuid.uuid4(),target,actor,target,_json({'name':name})))
            elif action == 'delete':
                if target == current:
                    raise TenantPlatformError('Cari şirkəti silmək olmaz. Əvvəl başqa şirkətə keçin.')
                name = str(row['name'])
                if str(payload.get('confirm_name') or '') != name or str(payload.get('confirm_tenant_id') or '') != target:
                    raise TenantPlatformError('Təsdiq üçün şirkət adını və tenant_id dəyərini dəqiq daxil edin.')
                cur.execute('''SELECT t.id FROM saas_tenants t JOIN saas_tenant_members m ON m.tenant_id=t.id
                  WHERE m.telegram_id=%s AND m.active=TRUE AND (m.role='owner' OR t.owner_telegram_id=%s)
                  ORDER BY t.created_at,t.id LIMIT 1''', (actor, actor))
                primary = cur.fetchone()
                if primary and str(primary['id']) == target:
                    raise TenantPlatformError('Əsas şirkəti silmək olmaz. Bu məhdudiyyət əsas hesabı və SSO girişini qoruyur.')
                warnings = _warnings(cur, target)
                # Tenant tables intentionally retain audit/financial/message
                # history. Archive access instead of cascading away records;
                # the central identity account and Connect data are untouched.
                cur.execute("UPDATE saas_tenants SET status='archived',updated_at=now() WHERE id=%s::uuid", (target,))
                cur.execute('UPDATE saas_tenant_members SET active=FALSE,updated_at=now() WHERE tenant_id=%s::uuid', (target,))
                conn.commit()
                return {'deleted': True, 'archived': True, 'tenant_id': target, 'warnings': warnings}
            else:
                raise TenantPlatformError('Şirkət əməliyyatı tanınmadı.')
        conn.commit()
    return {'updated': True, 'tenant_id': target}
