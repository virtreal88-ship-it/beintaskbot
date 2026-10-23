"""Deterministic projections of existing Simulation journals; never calls AI.

Groups are derived from immutable run evidence. Only human review state is
persisted, in the existing dashboard review table. Reading cannot enqueue work.
"""
import hashlib
import json
import re

from tenant_linear_policy import TenantLinearError
from tenant_ai_agents_store import _authorize, _audit
from tenant_ai_dashboard import _json_value, _iso


def findings(run):
    """Exact evidence codes, not keyword judgments about customer answers."""
    found = {}
    def add(category, reason, action, component, priority='medium', media='', operation=''):
        # Raw exception text can contain secrets/PII. Never return it as a label.
        code = str(reason or 'unclassified_stop')
        safe = code if re.fullmatch(r'[a-z][a-z0-9_]{0,99}', code) else 'unclassified_stop'
        identity = json.dumps([category, code, media, operation], separators=(',', ':'))
        key = 'learning:' + hashlib.sha256(identity.encode()).hexdigest()[:48]
        found[key] = dict(issue_key=key, category=category, reason=safe,
                          action=action, component=component, priority=priority, media=media,
                          operation=operation if operation in {'create_task','update_phone','move_stage','handoff_after_reply'} else '')
    events = _json_value(run.get('events'), [])
    for event in events:
        if not isinstance(event, dict):
            continue
        kind, reason = event.get('type'), event.get('reason')
        if kind == 'active_mode_stop_observed':
            continue  # PR #141: expected observation, never a Simulation defect.
        if kind == 'expert_unavailable':
            add('behavior', reason or 'expert_unavailable', 'agent', 'expert')
        elif kind == 'provider_result_unknown':
            add('infrastructure', 'provider_result_unknown', 'technical', 'provider', 'high')
        elif kind == 'simulation_unsupported':
            media = event.get('message_type') if event.get('message_type') in {'audio','image','video','document','sticker'} else ''
            if reason == 'media_context_unavailable':
                add('context', 'simulation_media_context_unavailable', 'technical', 'media', media=media)
            else:
                actions = event.get('actions') or ['unsupported_operation']
                for action in actions:
                    add('context', reason or 'simulation_unsupported', 'technical', 'executor', operation=str(action))
        elif kind == 'knowledge_retrieved' and event.get('source_ids') == []:
            add('knowledge', 'retrieval_empty', 'knowledge', 'retrieval', 'low')
        elif kind == 'virtual_no_answer_action':
            if reason == 'missing_knowledge':
                add('knowledge', 'retrieval_empty', 'knowledge', 'retrieval')
            elif reason in {'unsupported_evidence', 'conflicting_evidence'}:
                add('quality', reason, 'knowledge', 'answer_validator')
            elif reason == 'expert_failed':
                add('behavior', reason, 'agent', 'expert')
            elif reason == 'clarification_limit_reached':
                add('behavior', reason, 'agent', 'simulation')
    stop = str(run.get('virtual_stop_reason') or '')
    if run.get('outcome') in {'error','unknown','blocked','unsupported','virtual_stop','skipped'}:
        # A terminal state is evidence, not proof of a bug. Configuration and
        # intentional safety stops are review prompts and must not suggest bypass.
        if stop == 'simulation_media_context_unavailable':
            if not any(x['reason'] == stop for x in found.values()):
                add('context', stop, 'technical', 'media')
        elif stop == 'provider_result_unknown':
            add('infrastructure', stop, 'technical', 'provider', 'high')
        elif stop.startswith('ai_spend_') or stop in {'simulation_budget_exhausted','simulation_processing_failed'}:
            add('infrastructure', stop, 'technical', 'budget' if 'budget' in stop or 'spend' in stop else 'executor')
        elif stop in {'legacy_channel_ambiguous','route_or_tenant_unavailable','simulation_source_message_unavailable','simulation_source_not_inbound'}:
            add('context', stop, 'technical', 'routing')
        elif stop.startswith('no_answer_') and found:
            pass  # More specific recorded evidence already explains the stop.
        elif stop:
            add('behavior', stop, 'agent', 'simulation')
        elif run.get('outcome') in {'error','unknown'}:
            add('infrastructure', 'simulation_processing_failed', 'technical', 'executor', 'high')
    return list(found.values())


def _projection(cur, tenant):
    # Use the Dashboard's explicit 30-day window; group before pagination.
    # No message bodies, provider errors, credentials or customer names selected.
    cur.execute('''SELECT r.id,r.primary_agent_id,r.channel_id,r.outcome,r.virtual_stop_reason,
        r.events,r.knowledge_refs,r.expert_results,r.created_at,q.recheck_of_run_id,
        a.name AS agent_name,ch.name AS channel_name
      FROM saas_ai_simulation_runs r JOIN saas_ai_simulation_queue q ON q.tenant_id=r.tenant_id AND q.id=r.queue_id
      LEFT JOIN saas_ai_agents a ON a.tenant_id=r.tenant_id AND a.id=r.primary_agent_id
      LEFT JOIN saas_connect_channels ch ON ch.tenant_id=r.tenant_id AND ch.connect_channel_id=r.channel_id
      WHERE r.tenant_id=%s::uuid AND r.created_at>=now()-interval '30 days'
      ORDER BY r.created_at,r.id''', (tenant,))
    rows = cur.fetchall()
    groups, runs = {}, {}
    metrics = dict(runs=len(rows), replies=0, stops=0, errors=0, knowledge=0, expert=0,
                   confirmed=0, fixed=0, rechecks_without_original_issue=0)
    for row in rows:
        run_id = str(row['id'])
        evidence = findings(row)
        runs[run_id] = {x['issue_key'] for x in evidence}
        metrics['replies'] += row['outcome'] == 'virtual_reply'
        metrics['stops'] += row['outcome'] == 'virtual_stop'
        metrics['errors'] += row['outcome'] in {'error','unknown'}
        for item in evidence:
            key = item['issue_key']
            group = groups.setdefault(key, {**item, 'id':key, 'count':0, 'run_id':run_id,
                'first_seen':_iso(row['created_at']), 'last_seen':_iso(row['created_at']),
                'agents':{}, 'channels':{}, 'examples':[], 'comparisons':[], 'status':'new', 'history':[]})
            group['count'] += 1
            group['last_seen'] = _iso(row['created_at'])
            if row.get('primary_agent_id'):
                group['agents'][str(row['primary_agent_id'])] = row.get('agent_name') or ''
            group['channels'][str(row['channel_id'])] = row.get('channel_name') or ''
            refs = _json_value(row.get('knowledge_refs'), [])
            experts = _json_value(row.get('expert_results'), [])
            group['examples'] = (group['examples'] + [dict(run_id=run_id, at=_iso(row['created_at']),
                outcome=row['outcome'], knowledge_ids=[str(x['source_id']) for x in refs if isinstance(x,dict) and x.get('source_id')],
                expert_ids=[str(x['agent_id']) for x in experts if isinstance(x,dict) and x.get('agent_id')])])[-5:]
    for row in rows:
        parent = str(row.get('recheck_of_run_id') or '')
        if parent not in runs:
            continue
        for key in runs[parent]:
            absent = key not in runs[str(row['id'])]
            comparable = row['outcome'] in {'virtual_reply','virtual_stop'}
            groups[key]['comparisons'].append(dict(before_run_id=parent, after_run_id=str(row['id']),
                outcome=row['outcome'], result='not_observed' if absent and comparable else 'persists' if not absent else 'inconclusive'))
            metrics['rechecks_without_original_issue'] += bool(absent and comparable)
    cur.execute('''SELECT issue_key,status,history,updated_at FROM saas_ai_dashboard_issue_reviews
        WHERE tenant_id=%s::uuid AND left(issue_key,9)='learning:' ''', (tenant,))
    for row in cur.fetchall():
        group = groups.get(row['issue_key'])
        if group:
            group.update(status=row['status'], history=_json_value(row.get('history'), []), reviewed_at=_iso(row['updated_at']))
            # A fresh recurrence must not silently inherit an old "fixed" verdict.
            if row['status'] == 'fixed' and group['last_seen'] > _iso(row['updated_at']):
                group['status'] = 'new'
                group['recurred'] = True
    for group in groups.values():
        group['comparisons'] = group['comparisons'][-10:]
        metrics['knowledge'] += group['category'] == 'knowledge'
        metrics['expert'] += group['component'] == 'expert'
        metrics['confirmed'] += group['status'] in {'confirmed','fixing','recheck_pending','fixed'}
        metrics['fixed'] += group['status'] == 'fixed'
    return groups, metrics


def improvements(profile, *, limit=30, cursor='', status=''):
    from tenant_ai_dashboard import _connect, ensure_schema
    safe = max(1, min(100, int(limit)))
    if cursor and not re.fullmatch(r'learning:[a-f0-9]{48}', cursor):
        raise TenantLinearError('Invalid cursor.')
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, _ = _authorize(cur, profile)
            groups, metrics = _projection(cur, tenant)
            ordered = sorted((x for x in groups.values() if not status or x['status']==status), key=lambda x:x['id'])
            remaining = [x for x in ordered if x['id'] > cursor]
            page = remaining[:safe]
            from tenant_ai_spend_budget import limit_microusd
            cur.execute('''SELECT COALESCE(sum(actual_microusd) FILTER(WHERE state='settled'),0) AS actual,
                COALESCE(sum(reserved_microusd) FILTER(WHERE state IN ('reserved','unknown')),0) AS held,
                count(*) FILTER(WHERE state='unknown') AS unknown_calls
                FROM saas_ai_spend_reservations WHERE tenant_id=%s::uuid''', (tenant,))
            spend=cur.fetchone()
            cap=limit_microusd(tenant)
            budget={'limit_microusd':cap or None, 'actual_microusd':int(spend['actual']),
                    'held_microusd':int(spend['held']), 'unknown_calls':int(spend['unknown_calls']),
                    'period':'ledger_lifetime'}
            return dict(items=page, total=len(ordered), next_cursor=page[-1]['id'] if len(remaining)>safe else None,
                        metrics=metrics, budget=budget, window_days=30, paid_calls=0)


def review_improvement(cur, tenant, actor, run_id, issue_key, status):
    groups, _ = _projection(cur, tenant)
    group = groups.get(issue_key)
    if not group or group['run_id'] != run_id:
        raise TenantLinearError('Issue not found.', 404)
    # Serialize reviews of a derived group, including its first insert.
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (tenant+':'+issue_key,))
    cur.execute('''INSERT INTO saas_ai_dashboard_issue_reviews(tenant_id,run_id,issue_key,status,updated_by,history)
      VALUES(%s::uuid,%s::uuid,%s,%s,%s,jsonb_build_array(jsonb_build_object('status',%s::text,'at',now())))
      ON CONFLICT(tenant_id,issue_key) WHERE left(issue_key,9)='learning:'
      DO UPDATE SET status=EXCLUDED.status,updated_by=EXCLUDED.updated_by,updated_at=now(),
        history=saas_ai_dashboard_issue_reviews.history || EXCLUDED.history
      RETURNING status,updated_at''', (tenant,run_id,issue_key,status,actor,status))
    row = cur.fetchone()
    _audit(cur,tenant,actor,'ai_improvement_reviewed','ai_dashboard_issue',issue_key,{'status':status,'run_id':run_id})
    return {'status':row['status'],'updated_at':_iso(row['updated_at'])}
