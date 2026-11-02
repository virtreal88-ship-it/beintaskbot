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
