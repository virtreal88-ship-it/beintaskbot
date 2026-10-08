"""Durable CRM read-job queue and atomic snapshot/page checkpoints."""
import json
import uuid
import time

from tenant_platform import _connect, TenantPlatformError
from tenant_crm_snapshots import deal_rows, task_rows
from tenant_crm_sync_schema import ensure_sync_schema
from tenant_crm_sync_windows import choose_window


def connection_id(cur, tenant_id: str) -> str:
    cur.execute('''SELECT account_domain, connected_at FROM saas_tenant_integrations
        WHERE tenant_id=%s::uuid AND provider='kommo' AND status='connected' ''', (tenant_id,))
    row = cur.fetchone()
    return str(row['account_domain']) + ':' + str(row['connected_at']) if row and row['account_domain'] else ''


def public_job(row: dict) -> dict:
    return {key: value.isoformat() if hasattr(value, 'isoformat') else value
            for key, value in row.items() if key in {
                'resource', 'status', 'pages_done', 'started_at', 'updated_at', 'finished_at'}}


def enqueue(tenant_id: str, resource: str, *, force_full: bool = False) -> dict:
    if resource not in {'leads', 'tasks'}:
        raise ValueError('Invalid CRM resource')
    with _connect() as conn:
        ensure_sync_schema(conn)
        with conn.cursor() as cur:
            # Serializes concurrent first insert / restart, not provider calls.
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                        ('crm-enqueue:' + tenant_id + ':' + resource,))
            current_connection = connection_id(cur, tenant_id)
            if not current_connection:
                raise TenantPlatformError('Kommo qoşulmayıb. İnteqrasiyanı yoxlayın.')
            cur.execute('SELECT * FROM saas_crm_sync_jobs WHERE tenant_id=%s::uuid AND resource=%s FOR UPDATE',
                        (tenant_id, resource))
            row = cur.fetchone()
            if row and row['connection_id'] == current_connection and row['status'] in {'queued', 'running'}:
                return public_job(row)
            if not force_full and row and row['connection_id'] == current_connection and row['status'] == 'failed':
                cur.execute('''UPDATE saas_crm_sync_jobs SET status='queued', attempts=0,
                    next_attempt_at=now(),updated_at=now(),finished_at=NULL
                    WHERE tenant_id=%s::uuid AND resource=%s RETURNING *''', (tenant_id, resource))
            else:
                prior = row if row and row['connection_id'] == current_connection else None
                window = choose_window(prior, int(time.time()), force_full=force_full)
                cur.execute('''INSERT INTO saas_crm_sync_jobs
                    (tenant_id,resource,run_id,connection_id,status,window_from,window_to,watermark,last_full_sync)
                    VALUES (%s::uuid,%s,%s::uuid,%s,'queued',%s,%s,%s,%s)
                    ON CONFLICT (tenant_id,resource) DO UPDATE SET run_id=EXCLUDED.run_id,
                    connection_id=EXCLUDED.connection_id,status='queued',next_page=1,last_record_id=0,pages_done=0,
                    window_from=EXCLUDED.window_from,window_to=EXCLUDED.window_to,watermark=EXCLUDED.watermark,
                    last_full_sync=EXCLUDED.last_full_sync,
                    records_saved=0,attempts=0,next_attempt_at=now(),started_at=now(),updated_at=now(),finished_at=NULL
                    RETURNING *''', (tenant_id, resource, str(uuid.uuid4()), current_connection, *window))
            result = public_job(cur.fetchone())
        conn.commit()
    return result


def sync_status(tenant_id: str, resources: list[str]) -> list[dict]:
    with _connect() as conn:
        ensure_sync_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT resource,status,pages_done,started_at,updated_at,finished_at
                FROM saas_crm_sync_jobs WHERE tenant_id=%s::uuid AND resource=ANY(%s) ORDER BY resource''',
                (tenant_id, resources))
            return [public_job(row) for row in cur.fetchall()]


class SyncPageStore:
    """One page under a session lock; snapshot and cursor commit together.

    No lease stealing: a live slow process retains its lock. Process death
    closes the connection and the same page can be safely reread.
    """
    def __init__(self, conn, row: dict):
        self.conn, self.row = conn, row

    @classmethod
    def claim(cls):
        conn = _connect()
        try:
            ensure_sync_schema(conn)
            with conn.cursor() as cur:
                cur.execute('''SELECT * FROM saas_crm_sync_jobs
                    WHERE status IN ('queued','running') AND next_attempt_at<=now()
                    ORDER BY updated_at,tenant_id,resource LIMIT 20''')
                candidates = cur.fetchall()
                for candidate in candidates:
                    key = (str(candidate['tenant_id']), candidate['resource'])
                    lock = 'crm-sync:' + ':'.join(key)
                    cur.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS locked', (lock,))
                    if not cur.fetchone()['locked']:
                        continue
                    cur.execute('''SELECT * FROM saas_crm_sync_jobs WHERE tenant_id=%s::uuid AND resource=%s
                        AND status IN ('queued','running') AND next_attempt_at<=now() FOR UPDATE''', key)
                    row = cur.fetchone()
                    if not row:
                        cur.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))', (lock,))
                        continue
                    cur.execute('''UPDATE saas_crm_sync_jobs SET status='running',updated_at=now()
                        WHERE tenant_id=%s::uuid AND resource=%s''', key)
                    conn.commit()
                    return cls(conn, row)
            conn.commit()
            conn.close()
            return None
        except BaseException:
            conn.close()
            raise

    def connection_is_current(self) -> bool:
        with self.conn.cursor() as cur:
            matches = connection_id(cur, str(self.row['tenant_id'])) == self.row['connection_id']
        self.conn.commit()
        return matches

    def save_page(self, snapshots: list[dict], *, done: bool) -> None:
        tenant, resource = str(self.row['tenant_id']), self.row['resource']
        rows = (deal_rows if resource == 'leads' else task_rows)(tenant, snapshots, json.dumps)
        with self.conn.cursor() as cur:
            # Also fences an enqueue after reauthorization while this GET ran.
            cur.execute('''SELECT run_id FROM saas_crm_sync_jobs WHERE tenant_id=%s::uuid AND resource=%s
                FOR UPDATE''', (tenant, resource))
            current = cur.fetchone()
            if not current or str(current['run_id']) != str(self.row['run_id']):
                self.conn.rollback()
                return
            cur.execute('''SELECT account_domain,connected_at FROM saas_tenant_integrations
                WHERE tenant_id=%s::uuid AND provider='kommo' AND status='connected' FOR SHARE''', (tenant,))
            integration = cur.fetchone()
            matches = integration and str(integration['account_domain']) + ':' + str(integration['connected_at']) == self.row['connection_id']
            if not matches:
                self.conn.rollback()
                self.fail(permanent=True)
                return
            if rows and resource == 'leads':
                # CRM refresh must not erase chat previews or their timestamps.
                cur.executemany('''INSERT INTO saas_crm_deals
                    (tenant_id,kommo_lead_id,pipeline_id,status_id,stage_name,name,contact_name,phone,
                     channel,last_message,last_message_at,source_updated_at,raw)
                    VALUES (%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::timestamptz,%s::timestamptz,%s::jsonb)
                    ON CONFLICT (tenant_id,kommo_lead_id) DO UPDATE SET pipeline_id=EXCLUDED.pipeline_id,
                    status_id=EXCLUDED.status_id,stage_name=EXCLUDED.stage_name,name=EXCLUDED.name,
                    contact_name=CASE WHEN EXCLUDED.contact_name='' THEN saas_crm_deals.contact_name ELSE EXCLUDED.contact_name END,
                    phone=CASE WHEN EXCLUDED.phone='' THEN saas_crm_deals.phone ELSE EXCLUDED.phone END,
                    source_updated_at=EXCLUDED.source_updated_at,raw=EXCLUDED.raw,synced_at=now()
                    WHERE saas_crm_deals.source_updated_at IS NULL OR
                        EXCLUDED.source_updated_at>=saas_crm_deals.source_updated_at''', rows)
            elif rows:
                cur.executemany('''INSERT INTO saas_crm_tasks
                    (tenant_id,kommo_task_id,kommo_lead_id,text,due_at,responsible_id,completed,raw)
                    VALUES (%s::uuid,%s,%s,%s,%s::timestamptz,%s,%s,%s::jsonb)
                    ON CONFLICT (tenant_id,kommo_task_id) DO UPDATE SET kommo_lead_id=EXCLUDED.kommo_lead_id,
                    text=EXCLUDED.text,due_at=EXCLUDED.due_at,responsible_id=EXCLUDED.responsible_id,
                    completed=EXCLUDED.completed,raw=EXCLUDED.raw,synced_at=now()
                    WHERE COALESCE(CASE WHEN EXCLUDED.raw->>'updated_at' ~ '^[0-9]+$'
                        THEN (EXCLUDED.raw->>'updated_at')::numeric END,0) >=
                        COALESCE(CASE WHEN saas_crm_tasks.raw->>'updated_at' ~ '^[0-9]+$'
                        THEN (saas_crm_tasks.raw->>'updated_at')::numeric END,0)''', rows)
            cur.execute('''UPDATE saas_crm_sync_jobs SET status=%s,next_page=next_page+1,pages_done=pages_done+1,last_record_id=%s,
                records_saved=records_saved+%s,attempts=0,next_attempt_at=now(),updated_at=now(),
                finished_at=CASE WHEN %s THEN now() ELSE NULL END
                ,watermark=CASE WHEN %s AND window_to>0 THEN window_to ELSE watermark END
                ,last_full_sync=CASE WHEN %s AND window_from=0 AND window_to>0 THEN window_to ELSE last_full_sync END
                WHERE tenant_id=%s::uuid AND resource=%s AND run_id=%s::uuid''',
                ('done' if done else 'queued',
                 max((int(item[1]) for item in rows),
                     default=int(self.row.get('last_record_id') or 0)),
                 len(rows), done, done, done, tenant, resource, str(self.row['run_id'])))
            if done:
                cur.execute('''INSERT INTO saas_tenant_audit_events
                    (id,tenant_id,action,entity_type,entity_id,payload)
                    VALUES (%s,%s::uuid,'crm_sync','kommo',%s,%s::jsonb)''',
                    (uuid.uuid4(), tenant, resource, json.dumps({'resource':resource,
                        'pages':int(self.row.get('pages_done') or 0)+1,
                        'records':int(self.row.get('records_saved') or 0)+len(rows)})))
        self.conn.commit()

    def fail(self, *, permanent: bool = False) -> None:
        self.conn.rollback()
        with self.conn.cursor() as cur:
            cur.execute('''UPDATE saas_crm_sync_jobs SET attempts=attempts+1,
                status=CASE WHEN %s OR attempts>=2 THEN 'failed' ELSE 'queued' END,
                next_attempt_at=now()+interval '60 seconds',updated_at=now()
                WHERE tenant_id=%s::uuid AND resource=%s AND run_id=%s::uuid''',
                (permanent, str(self.row['tenant_id']), self.row['resource'], str(self.row['run_id'])))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
