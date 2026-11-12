"""Read-only, source-bound support recommendations and owner SLA policy."""
from tenant_linear_policy import TenantLinearError, identifier
from tenant_platform import _connect
from tenant_ai_agents_schema import ensure_schema
from tenant_support_access import policy_access, require, predicate


_PRIORITIES = ('low', 'normal', 'high', 'urgent')
_SLA_MAX_HOURS = 720


def _safe_excerpt(value, limit):
    from tenant_media_evidence import _safe_text
    return _safe_text(value)[:limit]


def _validated_hours(value):
    if not isinstance(value, dict) or set(value) != set(_PRIORITIES):
        raise TenantLinearError('Укажите срок ответа для каждого приоритета.', 400)
    result = {}
    for key in _PRIORITIES:
        hours = value[key]
        if type(hours) is not int or not 1 <= hours <= _SLA_MAX_HOURS:
            raise TenantLinearError('Срок ответа должен быть от 1 до 720 часов.', 400)
        result[key] = hours
    return result


def _owner(policy):
    if policy.profile.get('role') != 'owner':
        raise TenantLinearError('Настраивать сроки ответа может только владелец компании.', 403)


def read_sla_policy(profile):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, _, policy = policy_access(cur, profile, 'view')
            _owner(policy)
            cur.execute('SELECT response_hours,updated_at FROM saas_support_sla_policies WHERE tenant_id=%s::uuid', (tenant,))
            row = cur.fetchone() or {}
            return {'response_hours': row.get('response_hours') or {},
                    'updated_at': row.get('updated_at').isoformat() if row.get('updated_at') else None}


def save_sla_policy(profile, data):
    if not isinstance(data, dict) or set(data) != {'response_hours'}:
        raise TenantLinearError('Некорректные настройки сроков ответа.', 400)
    hours = _validated_hours(data['response_hours'])
    import json
    from tenant_ai_agents_store import _audit
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, actor, policy = policy_access(cur, profile, 'update')
            _owner(policy)
            cur.execute('''INSERT INTO saas_support_sla_policies(tenant_id,response_hours,updated_by,updated_at)
              VALUES(%s::uuid,%s::jsonb,%s,now()) ON CONFLICT(tenant_id) DO UPDATE
              SET response_hours=EXCLUDED.response_hours,updated_by=EXCLUDED.updated_by,updated_at=now()
              RETURNING response_hours,updated_at''', (tenant, json.dumps(hours), actor))
            row = cur.fetchone()
            _audit(cur, tenant, actor, 'support_sla_policy_updated', 'support_sla_policy', tenant,
                   {'response_hours': hours})
        conn.commit()
    return {'response_hours': row['response_hours'], 'updated_at': row['updated_at'].isoformat()}


def recommendations(profile, ticket_id):
    """Search published same-tenant guidance and resolved cases; never invokes AI."""
    ticket_id = identifier(ticket_id)
    from tenant_support_case_context import load_scope, summary_messages
    from tenant_media_evidence import annotate_rows
    from tenant_ai_agents_store import _audit
    from tenant_knowledge_search import patterns, tsquery

    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, actor, policy = require(cur, profile, ticket_id, 'view')
            cur.execute('''SELECT description,summary,next_steps FROM saas_support_tickets
              WHERE tenant_id=%s::uuid AND id=%s::uuid''', (tenant, ticket_id))
            ticket = cur.fetchone()
            if not ticket:
                raise TenantLinearError('Обращение не найдено.', 404)
            scope = load_scope(cur, tenant, ticket_id)
            rows = summary_messages(cur, scope)
            query_parts = [ticket.get('description'), ticket.get('summary')]
            query_parts.extend(row.get('body') for row in rows[-20:] if row.get('direction') == 'incoming')
            query = ' '.join(str(value or '') for value in query_parts)
            search_patterns = patterns(query)
            search_query = tsquery(query)

            knowledge = []
            if search_patterns and search_query:
                cur.execute('''SELECT s.id::text AS source_id,s.title,s.updated_at AS source_updated_at,
                      c.chunk_no,c.content,ts_rank(c.search_vector,to_tsquery('simple',%s)) AS rank
                  FROM saas_ai_knowledge_chunks c JOIN saas_ai_knowledge_sources s
                    ON s.tenant_id=c.tenant_id AND s.id=c.source_id
                  WHERE c.tenant_id=%s::uuid AND s.active=TRUE
                    AND (c.search_vector @@ to_tsquery('simple',%s) OR c.content ILIKE ANY(%s))
                  ORDER BY rank DESC,s.updated_at DESC,c.chunk_no LIMIT 6''',
                  (search_query, tenant, search_query, search_patterns))
                knowledge = [{
                    'title': _safe_excerpt(row.get('title'), 180),
                    'excerpt': _safe_excerpt(row.get('content'), 900),
                    'source_ref': {'kind': 'published_knowledge_chunk', 'source_id': str(row['source_id']),
                                   'chunk_no': int(row['chunk_no']),
                                   'source_updated_at': row['source_updated_at'].isoformat()
                                       if row.get('source_updated_at') else None},
                } for row in cur.fetchall()]

            scope_sql, scope_args = predicate(policy, 'view', 't')
            similar = []
            if search_patterns:
                cur.execute('''SELECT t.id::text AS case_id,t.description,t.summary,t.next_steps,
                      t.priority,t.created_at,t.updated_at
                  FROM saas_support_tickets t
                  WHERE t.tenant_id=%s::uuid AND t.id<>%s::uuid AND t.status='resolved'
                    AND ''' + scope_sql + '''
                    AND (t.description ILIKE ANY(%s) OR t.summary ILIKE ANY(%s) OR t.next_steps ILIKE ANY(%s))
                  ORDER BY t.updated_at DESC,t.id LIMIT 4''',
                  (tenant, ticket_id, *scope_args, search_patterns, search_patterns, search_patterns))
                similar = [{
                    'case_id': str(row['case_id']),
                    'description': _safe_excerpt(row.get('description'), 300),
                    'summary': _safe_excerpt(row.get('summary'), 650),
                    'resolution_notes': _safe_excerpt(row.get('next_steps'), 650),
                    'priority': row.get('priority') if row.get('priority') in _PRIORITIES else 'normal',
                    'updated_at': row.get('updated_at').isoformat() if row.get('updated_at') else None,
                    'source_ref': {'kind': 'resolved_support_case', 'case_id': str(row['case_id'])},
                } for row in cur.fetchall()]

            # Report only counts/statuses. Evidence text and attachment bytes stay
            # behind the existing owner+dialog authorization path.
            media_rows = [row for row in rows if row.get('message_type') not in (None, '', 'text')]
            annotate_rows(cur, tenant, media_rows)
            complete = partial = unavailable = 0
            for row in media_rows:
                evidence = row.get('media_evidence') or {}
                coverage = evidence.get('coverage') or {}
                if evidence.get('status') == 'processed' and coverage.get('complete') is True:
                    complete += 1
                elif evidence.get('status') in {'partial', 'processed'}:
                    partial += 1
                elif evidence.get('status') in {'unavailable', 'unsupported', 'error'}:
                    unavailable += 1
            total = len(media_rows)
            _audit(cur, tenant, actor, 'support_recommendations_read', 'support_ticket', ticket_id,
                   {'knowledge_count': len(knowledge), 'similar_case_count': len(similar),
                    'media_count': total, 'media_complete': complete, 'media_partial': partial,
                    'media_unavailable': unavailable})
        conn.commit()
    return {
        'read_only': True,
        'knowledge': knowledge,
        'similar_cases': similar,
        'evidence_coverage': {'messages_checked': len(rows), 'attachments_in_scope': len(media_rows),
                              'complete': complete, 'partial': partial,
                              'unavailable': unavailable, 'unprocessed': max(0, total-complete-partial-unavailable)},
        'notice': 'Источники базы знаний и прошлые обращения — подсказки, не гарантированная причина. Проверьте источник и текущие факты.',
    }
