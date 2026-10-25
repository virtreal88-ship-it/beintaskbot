"""Evidence gates and bounded no-answer state for tenant AI agents.

The policy is opt-in for backwards compatibility. Evidence is accepted only
when the model cites retrieved, tenant-scoped source IDs; an empty retrieval
is deterministically insufficient regardless of model output.
"""
import json
from tenant_linear_policy import TenantLinearError

POLICY_ACTIONS = {'stop', 'handoff', 'create_task', 'clarify', 'call_expert', 'consult_developer'}
FALLBACK_ACTIONS = {'stop', 'handoff'}


def validate_policy(value):
    if value is None:
        value = {}
    allowed = {'enabled', 'action', 'fallback_action', 'max_clarifications', 'stop_reply', 'developer_id'}
    if not isinstance(value, dict) or set(value) - allowed:
        raise TenantLinearError('No-answer policy is invalid.')
    enabled = value.get('enabled', False)
    action = value.get('action', 'stop')
    fallback = value.get('fallback_action', 'stop')
    limit = value.get('max_clarifications', 1)
    reply = value.get('stop_reply', '')
    developer = value.get('developer_id')
    if (type(enabled) is not bool or not isinstance(action, str) or action not in POLICY_ACTIONS
            or not isinstance(fallback, str) or fallback not in FALLBACK_ACTIONS):
        raise TenantLinearError('No-answer policy is invalid.')
    if type(limit) is not int or not 1 <= limit <= 3:
        raise TenantLinearError('Clarification limit must be between 1 and 3.')
    if not isinstance(reply, str) or len(reply) > 500 or any(ord(ch) < 32 and ch not in '\n\r\t' for ch in reply):
        raise TenantLinearError('Stop reply is invalid.')
    if action=='consult_developer':
        if type(developer) is not int or developer <= 0: raise TenantLinearError('A designated developer is required.')
    elif developer is not None: raise TenantLinearError('Developer is only valid for developer consultation.')
    return {'enabled': enabled, 'action': action, 'fallback_action': fallback,
            'max_clarifications': limit, 'stop_reply': reply.strip(),'developer_id':developer}


def assess(raw, knowledge):
    """Validate a grounded model assessment against the actual retrieval set."""
    source_ids = {str(row.get('source_id') or '') for row in (knowledge or []) if row.get('source_id')}
    if not source_ids:
        return {'status': 'insufficient', 'reply': '', 'clarification_question': '', 'cited_source_ids': []}
    try:
        value = json.loads(str(raw or ''))
    except (TypeError, ValueError):
        return {'status': 'insufficient', 'reply': '', 'clarification_question': '', 'cited_source_ids': []}
    if not isinstance(value, dict) or set(value) != {'status', 'reply', 'clarification_question', 'citations'}:
        return {'status': 'insufficient', 'reply': '', 'clarification_question': '', 'cited_source_ids': []}
    status = value.get('status')
    reply = value.get('reply')
    question = value.get('clarification_question')
    citations = value.get('citations')
    if (status not in {'supported', 'insufficient', 'conflicting'}
            or not isinstance(reply, str) or len(reply) > 8000
            or not isinstance(question, str) or len(question) > 1000
            or not isinstance(citations, list) or len(citations) > 8):
        return {'status': 'insufficient', 'reply': '', 'clarification_question': '', 'cited_source_ids': []}
    source_text = {str(row.get('source_id')): ' '.join(str(row.get('content') or '').casefold().split())
                   for row in (knowledge or []) if row.get('source_id')}
    cited = []
    cited_quotes = []
    for item in citations:
        if not isinstance(item, dict) or set(item) != {'source_id', 'quote'}:
            continue
        source_id, quote = item.get('source_id'), item.get('quote')
        if not isinstance(source_id, str) or source_id not in source_ids or not isinstance(quote, str) or not 12 <= len(quote.strip()) <= 400:
            continue
        normalized_quote = ' '.join(quote.casefold().split())
        if normalized_quote in source_text.get(source_id, ''):
            cited.append(source_id)
            cited_quotes.append(normalized_quote)
    cited = list(dict.fromkeys(cited))
    if status == 'supported' and (not cited or not reply.strip()):
        status = 'insufficient'
    if status == 'conflicting' and (len(cited) < 2 or len(set(cited_quotes)) < 2):
        status = 'insufficient'
    if status == 'insufficient':
        reply = ''
    return {'status': status, 'reply': reply.strip(),
            'clarification_question': question.strip(), 'cited_source_ids': cited}


def prompt_instructions():
    return ('Return exactly one JSON object with keys status, reply, clarification_question, citations. '
        'citations must be an array of objects with source_id and an exact quote copied from that retrieved source. '
        'status must be supported, insufficient, or conflicting. Mark supported only when the answer is directly '
        'supported by retrieved Knowledge Source text and include a verifiable exact quote. Mark conflicting '
        'only when two or more retrieved sources provide incompatible facts, and quote both. Otherwise mark insufficient. '
        'Never resolve contradictions by guessing. For insufficient/conflicting, leave reply empty and provide one concise '
        'clarification_question only when a missing detail from the customer could make the answer verifiable.')


def selected_action(policy, signal, *, clarification_count=0, expert_result=None):
    """Resolve a policy action without weakening the configured fallback."""
    config = validate_policy(policy)
    if not config['enabled']:
        return {'action': 'continue', 'reason': 'policy_disabled'}
    if signal not in {'missing_knowledge', 'unsupported_evidence', 'conflicting_evidence', 'expert_failed', 'customer_unsolved'}:
        return {'action': 'continue', 'reason': 'not_a_no_answer_signal'}
    action = config['action']
    if action == 'clarify' and int(clarification_count) >= config['max_clarifications']:
        return {'action': config['fallback_action'], 'reason': 'clarification_limit_reached'}
    if action == 'call_expert':
        result = expert_result or {}
        value = result.get('result') if isinstance(result.get('result'),dict) else {}
        grounded = list(result.get('grounded_sources') or [])
        if not grounded:
            grounded = [source for specialist in result.get('specialists') or []
                        if isinstance(specialist, dict) and specialist.get('status') == 'completed'
                        for source in specialist.get('grounded_sources') or []]
        if (result.get('status') != 'completed' or value.get('requires_human') is True or not grounded):
            return {'action': config['fallback_action'], 'reason': 'expert_unavailable_or_unverified'}
        return {'action': 'continue', 'reason': 'expert_evidence_available'}
    return {'action': action, 'reason': signal}


def clarification_decision(job, message_id, limit):
    """Increment one per-dialog clarification only once per inbound message."""
    import uuid
    from tenant_ai_agents_schema import ensure_schema
    from tenant_ai_dialog_scope import job_scope, lock_dialog
    from tenant_platform import _connect
    tenant, agent = str(job['tenant_id']), str(job['agent_id'])
    provider, channel, conversation = str(job['provider']), job_scope(job), str(job['conversation_id'])
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            lock_dialog(cur, tenant, provider, job.get('channel_id'), conversation)
            cur.execute('''SELECT clarification_count,last_inbound_id FROM saas_ai_no_answer_state
              WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND provider=%s AND channel_id=%s AND conversation_id=%s FOR UPDATE''',
              (tenant,agent,provider,channel,conversation))
            row=cur.fetchone()
            if row and str(row.get('last_inbound_id') or '')==str(message_id):
                conn.commit()
                return {'state':'duplicate','count':int(row['clarification_count'])}
            count=int((row or {}).get('clarification_count') or 0)
            if count >= int(limit):
                next_count=count
                state='exhausted'
            else:
                next_count=count+1
                state='clarify'
            cur.execute('''INSERT INTO saas_ai_no_answer_state(tenant_id,agent_id,provider,channel_id,conversation_id,clarification_count,last_inbound_id)
              VALUES(%s::uuid,%s::uuid,%s,%s,%s,%s,%s)
              ON CONFLICT(tenant_id,agent_id,provider,channel_id,conversation_id) DO UPDATE SET
                clarification_count=EXCLUDED.clarification_count,last_inbound_id=EXCLUDED.last_inbound_id,updated_at=now()''',
              (tenant,agent,provider,channel,conversation,next_count,str(message_id)[:500]))
        conn.commit()
    return {'state':state,'count':next_count}


def clear_clarifications(job):
    from tenant_ai_agents_schema import ensure_schema
    from tenant_ai_dialog_scope import job_scope
    from tenant_platform import _connect
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''UPDATE saas_ai_no_answer_state SET clarification_count=0,last_inbound_id='',updated_at=now()
              WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND provider=%s AND channel_id=%s AND conversation_id=%s''',
              (job['tenant_id'],job['agent_id'],job['provider'],job_scope(job),job['conversation_id']))
        conn.commit()


def stop_without_answer(job, reservation, reason='ai_no_answer'):
    """Durably pause this exact tenant/channel conversation and terminalize its run."""
    from tenant_ai_agents_schema import ensure_schema
    from tenant_ai_dialog_scope import job_scope, lock_dialog, assert_resolved
    from tenant_platform import _connect, _json
    tenant, channel, conversation = str(job['tenant_id']), job_scope(job), str(job['conversation_id'])
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            lock_dialog(cur, tenant, job['provider'], job.get('channel_id'), conversation)
            if job['provider']=='connect':
                assert_resolved(cur, tenant, channel, conversation, job.get('event_fingerprint') or '')
                from tenant_ai_group_coordination import group_jid, stop
                if group_jid(conversation):
                    stop(cur,tenant,conversation,state='paused',reason=reason)
            cur.execute('''INSERT INTO saas_ai_conversation_state(tenant_id,provider,conversation_id,channel_id,state,reason,agent_id)
              VALUES(%s::uuid,%s,%s,%s,'paused',%s,%s::uuid)
              ON CONFLICT(tenant_id,provider,channel_id,conversation_id) DO UPDATE SET
                state='paused',reason=EXCLUDED.reason,agent_id=COALESCE(saas_ai_conversation_state.agent_id,EXCLUDED.agent_id),
                revision=saas_ai_conversation_state.revision+1,updated_at=now()
              WHERE saas_ai_conversation_state.state<>'human' ''',
              (tenant,job['provider'],conversation,channel,str(reason)[:160],job['agent_id']))
            cur.execute("UPDATE saas_ai_agent_runs SET state='skipped',error_code=%s,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid AND state='generating'",
              (str(reason)[:80],tenant,(reservation or {}).get('run_id')))
            cur.execute("UPDATE saas_ai_agent_jobs SET state='skipped',lease_token=NULL,lease_until=NULL,error_code=%s,updated_at=now() WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND provider=%s AND conversation_id=%s AND scope_channel_id=%s AND lease_token=%s AND revision=%s",
              (str(reason)[:80],tenant,job['agent_id'],job['provider'],conversation,channel,job['lease_token'],job['revision']))
            cur.execute("UPDATE saas_tenant_webhook_events SET state='completed',completed_at=now(),error_code=%s WHERE tenant_id=%s::uuid AND fingerprint=%s",
              (str(reason)[:80],tenant,job['event_fingerprint']))
        conn.commit()
    return True
