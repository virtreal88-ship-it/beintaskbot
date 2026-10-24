"""Durable, fail-closed observation queue for the future learning loop."""
import hashlib
import json
import os
import uuid


def enabled(): return str(os.environ.get('AI_AUTONOMOUS_IMPROVEMENT_ENABLED') or '').lower() == 'true'


def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_administrator_observations(
          id UUID PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
          source TEXT NOT NULL, dedupe_key TEXT NOT NULL, payload JSONB NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
          attempts INTEGER NOT NULL DEFAULT 0, available_at TIMESTAMPTZ NOT NULL DEFAULT now(), checkpoint TEXT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE(tenant_id,source,dedupe_key), CHECK(state IN ('queued','processing','completed','blocked')) )''')
        cur.execute('CREATE INDEX IF NOT EXISTS saas_ai_administrator_observations_claim_idx ON saas_ai_administrator_observations(tenant_id,state,available_at)')


def enqueue(cur, tenant_id, source, payload, checkpoint=''):
    """Idempotently record evidence only; the worker must be enabled separately."""
    if not isinstance(payload,dict) or not isinstance(source,str) or not source or len(source)>80: raise ValueError('invalid observation')
    raw=json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False)
    key=hashlib.sha256(raw.encode()).hexdigest()
    cur.execute('''INSERT INTO saas_ai_administrator_observations(id,tenant_id,source,dedupe_key,payload,checkpoint)
      VALUES(%s,%s::uuid,%s,%s,%s::jsonb,%s) ON CONFLICT(tenant_id,source,dedupe_key) DO NOTHING''',(uuid.uuid4(),tenant_id,source,key,raw,str(checkpoint)[:240]))
    return key
