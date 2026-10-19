"""Deterministic review hints, never automatic fact reconciliation."""
from tenant_platform import _json
from tenant_ai_group_coordination import group_jid


def check(cur,tenant,route,value,refs):
    cur.execute('''SELECT c.id,c.content,e.source_refs FROM saas_knowledge_candidates c
      JOIN saas_knowledge_extractions e ON e.tenant_id=c.tenant_id AND e.id=c.extraction_id
      WHERE c.tenant_id=%s::uuid AND c.status<>'rejected'
      AND e.route->>'conversation_id'=%s AND (%s OR e.route->>'channel_id'=%s)
      AND c.content->'source_message_ids'=%s::jsonb''',
      (tenant,route['conversation_id'],bool(group_jid(route['conversation_id'])),route['channel_id'],_json(value['source_message_ids'])))
    evidence={r['id']:r.get('body_hash') for r in refs if r['id'] in value['source_message_ids']}
    for old in cur.fetchall():
        old_evidence={r['id']:r.get('body_hash') for r in old['source_refs'] if r['id'] in value['source_message_ids']}
        if old['content']==value and old_evidence==evidence:return True,[]
    cur.execute('''SELECT id,content FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid
      AND status='approved' AND content->>'product'=%s AND content->'symptoms'=%s::jsonb
      AND content->'resolution_steps'<>%s::jsonb LIMIT 5''',
      (tenant,value.get('product',''),_json(value.get('symptoms',[])),_json(value.get('resolution_steps',[]))))
    conflicts=cur.fetchall()
    return False,[{'code':'possible_solution_conflict','candidate_id':str(row['id'])} for row in conflicts]
