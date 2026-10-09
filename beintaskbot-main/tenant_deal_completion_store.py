"""Separate deal command journal; task approvals and task results stay untouched."""
import json
from tenant_policy import TenantPolicy
from tenant_platform import _connect, _ensure_schema
from tenant_linear_policy import TenantLinearError


def ensure_schema(conn) -> None:
    _ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_deal_completion_commands (
          tenant_id UUID NOT NULL REFERENCES saas_tenants(id), actor_id BIGINT NOT NULL,
          request_id UUID NOT NULL,lead_id BIGINT NOT NULL,fingerprint TEXT NOT NULL,
          state JSONB NOT NULL DEFAULT '{}',created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),PRIMARY KEY(tenant_id,actor_id,request_id))''')
        cur.execute('''CREATE INDEX IF NOT EXISTS saas_deal_completion_pending
          ON saas_deal_completion_commands(tenant_id,lead_id) WHERE state->>'step' NOT IN ('done','rejected')''')


class DealCommandStore:
    def __init__(self, tenant: str, actor: int, request: str, lead: int, fingerprint: str):
        self.key=(tenant,actor,request)
        self.conn=_connect()
        try:
            ensure_schema(self.conn)
            with self.conn.cursor() as cur:
                # Shared with task routing to prevent concurrent deal mutation.
                cur.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS locked', (f'task-deal:{tenant}:{lead}',))
                if not cur.fetchone()['locked']: raise TenantLinearError('Sövdələşmə üzrə əməliyyat işlənir.',409)
                cur.execute('''INSERT INTO saas_deal_completion_commands(tenant_id,actor_id,request_id,lead_id,fingerprint)
                  VALUES(%s::uuid,%s,%s::uuid,%s,%s) ON CONFLICT DO NOTHING''',(*self.key,lead,fingerprint))
                cur.execute('''SELECT fingerprint,state FROM saas_deal_completion_commands
                  WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid''',self.key)
                row=cur.fetchone()
                if row['fingerprint']!=fingerprint:raise TenantLinearError('Sorğu məlumatı dəyişib.',409)
                self.state=row['state'] or {}
                cur.execute('''SELECT request_id FROM saas_deal_completion_commands WHERE tenant_id=%s::uuid AND lead_id=%s
                  AND state->>'step' NOT IN ('done','rejected') AND NOT(actor_id=%s AND request_id=%s::uuid) LIMIT 1''',
                  (tenant,lead,actor,request))
                if cur.fetchone():raise TenantLinearError('Bu sövdələşmə artıq təsdiq gözləyir və ya işlənir.',409)
            self.conn.commit()
        except BaseException:
            self.conn.close();raise

    def save(self, state: dict) -> None:
        state={**self.state,**state}
        with self.conn.cursor() as cur:
            cur.execute('''UPDATE saas_deal_completion_commands SET state=%s::jsonb,updated_at=now()
              WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid''',(json.dumps(state),*self.key))
        self.conn.commit();self.state=state

    def close(self) -> None:
        self.conn.close()


def pending(profile: dict, limit: int, offset: int) -> dict:
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT c.actor_id,c.request_id,c.lead_id,c.state,m.display_name,d.pipeline_id,d.status_id
              FROM saas_deal_completion_commands c LEFT JOIN saas_tenant_members m
                ON m.tenant_id=c.tenant_id AND m.telegram_id=c.actor_id
              JOIN saas_crm_deals d ON d.tenant_id=c.tenant_id AND d.kommo_lead_id=c.lead_id AND d.deleted_at IS NULL
              WHERE c.tenant_id=%s::uuid AND c.state->>'step' NOT IN ('done','rejected')
              ORDER BY c.created_at,c.actor_id,c.request_id LIMIT %s OFFSET %s''',(profile['tenant_id'],limit+1,offset))
            rows=cur.fetchall()
    return {'approvals':[{'creator_id':r['actor_id'],'creator_name':r['display_name'] or 'Əməkdaş',
        'request_id':str(r['request_id']),'lead_id':r['lead_id'],'step':r['state'].get('step'),
        'name':r['state'].get('name',''),'result_text':r['state'].get('result_text','')} for r in rows[:limit]
        if TenantPolicy(profile).can_access_deal({**r,'tenant_id':profile['tenant_id']})],
        'has_more':len(rows)>limit,'next_offset':offset+min(limit,len(rows))}


def command(tenant: str, actor: int, request: str) -> dict:
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT lead_id,state FROM saas_deal_completion_commands
              WHERE tenant_id=%s::uuid AND actor_id=%s AND request_id=%s::uuid''',(tenant,actor,request))
            row=cur.fetchone()
    if not row:raise TenantLinearError('Təsdiq sorğusu tapılmadı.',404)
    return row
