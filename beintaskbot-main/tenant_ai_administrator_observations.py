"""Durable, fail-closed observation queue for the future learning loop."""
import hashlib
import json
import os
import uuid
from tenant_ai_dashboard import _connect


def enabled(): return str(os.environ.get('AI_AUTONOMOUS_IMPROVEMENT_ENABLED') or '').lower() == 'true'


def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_administrator_observations(
          id UUID PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
          source TEXT NOT NULL, dedupe_key TEXT NOT NULL, payload JSONB NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
          attempts INTEGER NOT NULL DEFAULT 0, available_at TIMESTAMPTZ NOT NULL DEFAULT now(), checkpoint TEXT NULL,
          lease_until TIMESTAMPTZ NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE(tenant_id,source,dedupe_key), CHECK(state IN ('queued','processing','completed','blocked')) )''')
        cur.execute('CREATE INDEX IF NOT EXISTS saas_ai_administrator_observations_claim_idx ON saas_ai_administrator_observations(tenant_id,state,available_at)')
        cur.execute('ALTER TABLE saas_ai_administrator_observations ADD COLUMN IF NOT EXISTS lease_until TIMESTAMPTZ NULL')
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_administrator_observation_drafts(
          observation_id UUID PRIMARY KEY REFERENCES saas_ai_administrator_observations(id) ON DELETE CASCADE,
          tenant_id UUID NOT NULL, draft_id UUID NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())''')


def enqueue(cur, tenant_id, source, payload, checkpoint=''):
    """Idempotently record evidence only; the worker must be enabled separately."""
    if not isinstance(payload,dict) or not isinstance(source,str) or not source or len(source)>80: raise ValueError('invalid observation')
    raw=json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False)
    key=hashlib.sha256(raw.encode()).hexdigest()
    cur.execute('''INSERT INTO saas_ai_administrator_observations(id,tenant_id,source,dedupe_key,payload,checkpoint)
      VALUES(%s,%s::uuid,%s,%s,%s::jsonb,%s) ON CONFLICT(tenant_id,source,dedupe_key) DO NOTHING''',(uuid.uuid4(),tenant_id,source,key,raw,str(checkpoint)[:240]))
    return key


def record_ai_testing_diagnostic(cur, tenant_id, session_id, request_id, agent_id, outcome):
    """Record opaque AI Testing lifecycle evidence; never copy test text/output."""
    if outcome not in {'unknown','superseded'}:
        return ''
    return enqueue(cur,tenant_id,'ai_testing_diagnostic',{
        'kind':'testing_scenario',
        'title':'Review virtual AI test outcome',
        'body':{'outcome':outcome,'session_id':str(session_id),'request_id':str(request_id)},
        'target':{'agent_id':str(agent_id)},
    },checkpoint='ai_testing:'+str(request_id))


def claim_next():
    if not enabled(): return None
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("UPDATE saas_ai_administrator_observations SET state='queued',lease_until=NULL WHERE state='processing' AND lease_until<now()")
            cur.execute('''SELECT * FROM saas_ai_administrator_observations WHERE state='queued' AND available_at<=now()
              ORDER BY created_at,id LIMIT 1 FOR UPDATE SKIP LOCKED'''); row=cur.fetchone()
            if not row: conn.commit(); return None
            cur.execute("UPDATE saas_ai_administrator_observations SET state='processing',attempts=attempts+1,lease_until=now()+interval '2 minutes',updated_at=now() WHERE id=%s",(row['id'],))
        conn.commit(); return dict(row)


def process(item):
    """Turn structured, trusted worker observations into drafts only."""
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM saas_ai_administrator_observations WHERE id=%s::uuid AND state=%s FOR UPDATE',(item['id'],'processing')); row=cur.fetchone()
            if not row: conn.commit(); return
            payload=row['payload'] or {}; kind=payload.get('kind'); title=payload.get('title'); body=payload.get('body'); target=payload.get('target') or {}
            if kind not in {'agent_improvement','knowledge','support_case','testing_scenario'} or not isinstance(title,str) or not isinstance(body,dict) or not isinstance(target,dict):
                cur.execute("UPDATE saas_ai_administrator_observations SET state='blocked',lease_until=NULL,updated_at=now() WHERE id=%s",(row['id'],)); conn.commit(); return
            cur.execute("SELECT telegram_id FROM saas_tenant_members WHERE tenant_id=%s::uuid AND role='owner' AND active IS DISTINCT FROM FALSE ORDER BY telegram_id LIMIT 1",(row['tenant_id'],)); owner=cur.fetchone()
            if not owner:
                cur.execute("UPDATE saas_ai_administrator_observations SET state='blocked',lease_until=NULL,updated_at=now() WHERE id=%s",(row['id'],)); conn.commit(); return
            draft_id=uuid.uuid4(); cur.execute('''INSERT INTO saas_ai_administrator_drafts(id,tenant_id,kind,title,body,target,created_by)
              VALUES(%s,%s::uuid,%s,%s,%s::jsonb,%s::jsonb,%s)''',(draft_id,row['tenant_id'],kind,title[:240],json.dumps(body),json.dumps(target),owner['telegram_id']))
            cur.execute('INSERT INTO saas_ai_administrator_observation_drafts(observation_id,tenant_id,draft_id) VALUES(%s,%s::uuid,%s)',(row['id'],row['tenant_id'],draft_id))
            cur.execute("UPDATE saas_ai_administrator_observations SET state='completed',lease_until=NULL,updated_at=now() WHERE id=%s",(row['id'],))
        conn.commit()
