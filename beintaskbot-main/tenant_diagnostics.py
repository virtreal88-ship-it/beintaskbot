"""Bounded owner-only queue health. No payloads, credentials or automatic retries."""
import uuid
from tenant_platform import _connect
from tenant_linear_policy import TenantLinearError

# Static SQL identifiers only. Input never becomes a table/column/expression.
QUEUES = (
    ('crm_sync', 'saas_crm_sync_jobs', 'status', 'updated_at'),
    ('task_commands', 'saas_task_commands', "state->>'step'", 'updated_at'),
    ('deal_commands', 'saas_deal_completion_commands', "state->>'step'", 'updated_at'),
    ('chat_sends', 'saas_chat_send_receipts', 'state', 'updated_at'),
    ('chat_ai', 'saas_chat_ai_runs', 'status', 'updated_at'),
    ('approval_telegram', 'saas_approval_notification_deliveries', 'status', 'updated_at'),
    ('crm_notices', 'saas_crm_notice_deliveries', 'status', 'updated_at'),
    ('deal_notices', 'saas_deal_notice_deliveries', 'status', 'updated_at'),
    ('linear_notices', 'saas_linear_notice_deliveries', 'status', 'updated_at'),
    ('news_publications', 'saas_news_telegram_queue', 'status', 'updated_at'),
    ('hot_order_notices', 'saas_hot_order_deliveries', 'status', 'updated_at'),
    ('task_decisions', 'saas_task_decision_deliveries', 'status', 'updated_at'),
)


def snapshot(session):
    tenant = str(uuid.UUID(str(session['tenant_id'])))
    actor = int(session['telegram_id'])
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '3000ms'")
            cur.execute('''SELECT m.*,t.status AS tenant_status FROM saas_tenant_members m
              JOIN saas_tenants t ON t.id=m.tenant_id
              WHERE m.tenant_id=%s::uuid AND m.telegram_id=%s FOR SHARE OF m,t''', (tenant, actor))
            member = cur.fetchone()
            if (not member or member.get('role') != 'owner' or member.get('active') is not True
                    or member.get('tenant_status') != 'active'):
                raise TenantLinearError('Yalnız şirkət sahibi üçün.', 403)
            # Retain the same membership read lock through the diagnostic snapshot.
            result = []
            for key, table, state, updated in QUEUES:
                cur.execute('SELECT to_regclass(%s) AS table_name', (table,))
                if not cur.fetchone()['table_name']:
                    result.append({'key': key, 'available': False, 'states': [], 'limited': False})
                    continue
                cur.execute(f'''SELECT state,count(*) AS count,min(updated_at) AS oldest,
                  count(*) FILTER(WHERE state IN ('sending','running','preparing')
                    AND updated_at<now()-interval '10 minutes') AS stale
                  FROM (SELECT {state} AS state,{updated} AS updated_at FROM {table}
                    WHERE tenant_id=%s::uuid ORDER BY {updated} DESC LIMIT 1001) recent
                  GROUP BY state ORDER BY state''', (tenant,))
                rows = cur.fetchall()
                result.append({'key': key, 'available': True,
                    'limited': sum(int(row['count']) for row in rows) >= 1001,
                    'states': [{'state': row['state'] or 'reserved', 'count': int(row['count']),
                                'stale': int(row['stale']),
                                'oldest': row['oldest'].isoformat() if row['oldest'] else None} for row in rows]})
            cur.execute('''SELECT provider,status,updated_at FROM saas_tenant_integrations
              WHERE tenant_id=%s::uuid ORDER BY provider LIMIT 20''', (tenant,))
            integrations = [{'provider': row['provider'], 'status': row['status'],
                             'updated_at': row['updated_at'].isoformat() if row['updated_at'] else None}
                            for row in cur.fetchall()]
        conn.commit()
    return {'queues': result, 'integrations': integrations, 'sample_limit': 1001,
            'automatic_retry': False}
