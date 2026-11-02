"""Summary versions use the existing private, tenant-owned audit ledger."""
import hashlib
import json

FIELDS = ('description', 'summary', 'suggestions', 'next_steps')


def content(row):
    return {key: str(row.get(key) or '') for key in FIELDS}


def fingerprint(row):
    return hashlib.sha256(json.dumps(content(row), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def versions(cur, tenant, ticket_id):
    cur.execute('''SELECT id,created_at,payload FROM saas_tenant_audit_events
      WHERE tenant_id=%s::uuid AND entity_type='support_ticket' AND entity_id=%s
        AND action='support_summary_generated' ORDER BY created_at DESC,id DESC LIMIT 10''',
                (tenant, str(ticket_id)))
    return [{'id': str(row['id']), 'created_at': str(row['created_at']), **(row.get('payload') or {})}
            for row in cur.fetchall()]


def applied_version(cur, tenant, ticket_id, content_hash):
    """Current text proof is independent of the bounded history shown in UI."""
    cur.execute('''SELECT payload FROM saas_tenant_audit_events WHERE tenant_id=%s::uuid
      AND entity_type='support_ticket' AND entity_id=%s AND action='support_summary_generated'
      AND payload->>'state'='applied' AND payload->>'applied_hash'=%s
      ORDER BY created_at DESC,id DESC LIMIT 1''',(tenant,str(ticket_id),content_hash))
    row=cur.fetchone()
    return row['payload'] if row else None


def request_status(cur, tenant, ticket_id, request_id):
    cur.execute('''SELECT payload FROM saas_tenant_audit_events WHERE tenant_id=%s::uuid
      AND entity_type='support_ticket' AND entity_id=%s AND action='support_summary_generated'
      AND payload->>'request_id'=%s ORDER BY created_at DESC,id DESC LIMIT 1''',
      (tenant,str(ticket_id),request_id))
    generated=cur.fetchone()
    if generated:
        payload=generated['payload']
        return {'request_id':request_id,'state':payload.get('request_state') or
          (payload['state'] if payload.get('state') in {'applied','conflict'} else 'unknown')}
    cur.execute('''SELECT r.state FROM saas_tenant_audit_events a JOIN saas_ai_agent_runs r
      ON r.tenant_id=a.tenant_id AND r.id::text=a.payload->>'run_id'
      WHERE a.tenant_id=%s::uuid AND a.entity_type='support_ticket' AND a.entity_id=%s
        AND a.action='support_summary_requested' AND a.payload->>'request_id'=%s LIMIT 1''',
      (tenant,str(ticket_id),request_id))
    run=cur.fetchone()
    return {'request_id':request_id,'state':'pending' if run and run['state']=='generating' else 'unknown'}
