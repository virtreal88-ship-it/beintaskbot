"""Non-dispatchable agent projection preview for explicitly authorized staff.

This module is deliberately not wired into a worker, provider or HTTP endpoint.
It does not establish a safe in-flight lease: a deletion, source ACL or agent
assignment can change after the transaction ends. Do not send its output to a
model, retain it in a cache, or use it to skip current-message/media checks.
Only an active employee principal is supported; service principals fail closed.
The deadline detects slow connection/schema setup after it returns; it cannot
interrupt a blocked connection or schema call. It is not an AI dispatch timeout.
"""
import json
import time

import psycopg

from tenant_platform import _connect, _ensure_schema
from tenant_linear_policy import TenantLinearError, identifier
from tenant_operator_case_submission import assignment
from tenant_customer_memory import (
    _assert_claim_allowed, _erasure_cutoff, _guard, _link, _lock_contact,
    _lock_identities, _route, _source, _source_kind_supported, _subject, _text,
    _verify_subject_revision,
)

# UTF-8 byte count conservatively bounds tokenizer inputs; do not truncate a
# fact, because a partial fact can change its meaning. A tighter token budget
# must be applied again by the future integration using its actual tokenizer.
_MAX_FACT_BYTES = 1200
_MAX_FACTS = 20
_MAX_CANDIDATES = 80


def _result(status, **extra):
    return {'status': status, 'dispatchable': False, **extra}


def preview(profile, *, tenant_id, subject_id, channel_id, conversation_id,
            agent_id, agent_version, assignment_revision):
    """Preview current-route facts for a staff principal and exact agent binding.

    Never infers a subject from a phone, route or group; never invokes AI or
    builds memory. Results are not an authorization to dispatch AI context.
    Denials reveal no facts, counts, revisions, or existence of hidden sources.
    """
    deadline=time.monotonic()+.5
    try:
        tenant_id=identifier(tenant_id)
        subject_id=identifier(subject_id)
        channel_id=identifier(channel_id)
        agent_id=identifier(agent_id)
        if (not isinstance(profile,dict) or profile.get('tenant_id') != tenant_id
                or type(agent_version) is not int or agent_version < 1
                or type(assignment_revision) is not int or assignment_revision < 1):
            return _result('denied')
        expected={'agent_id': agent_id, 'agent_version': agent_version,
                  'assignment_revision': assignment_revision}
        with _connect() as conn:
            if time.monotonic()>deadline:return _result('temporary_error')
            _ensure_schema(conn)
            if time.monotonic()>deadline:return _result('temporary_error')
            with conn.cursor() as cur:
                cur.execute("SET LOCAL lock_timeout='150ms'")
                cur.execute("SET LOCAL statement_timeout='500ms'")
                tenant,_,policy=_guard(cur,profile,'view')
                if tenant != tenant_id:return _result('denied')
                subject=_subject(cur,tenant,subject_id)
                _lock_identities(cur,tenant,channel_id,[conversation_id])
                _lock_contact(cur,tenant,subject)
                _route(cur,tenant,policy,channel_id,conversation_id,prelocked=True)
                # Resolves direct/group assignment using the same authority gate
                # as existing conversation AI. Group routes remain unsupported
                # by memory _link and fail closed.
                try:assignment(cur,tenant,channel_id,conversation_id,expected)
                except TenantLinearError:return _result('denied')
                cur.execute('''SELECT 1 FROM saas_ai_agents WHERE tenant_id=%s::uuid
                  AND id=%s::uuid AND enabled=TRUE AND archived_at IS NULL FOR SHARE''',
                  (tenant,agent_id))
                if not cur.fetchone():return _result('denied')
                cur.execute('''SELECT id FROM saas_customer_memory_links WHERE tenant_id=%s::uuid
                  AND contact_id=%s::uuid AND channel_id=%s::uuid AND conversation_id=%s
                  AND state='confirmed' LIMIT 2''',(tenant,subject,channel_id,conversation_id))
                links=cur.fetchall()
                if time.monotonic()>deadline:return _result('temporary_error')
                if not links:return _result('no_memory')
                if len(links)!=1:return _result('ambiguous_identity')
                link=_link(cur,tenant,policy,subject,str(links[0]['id']),prelocked=True)
                cur.execute('''SELECT revision FROM saas_customer_memory_subject_revisions
                  WHERE tenant_id=%s::uuid AND contact_id=%s::uuid''',(tenant,subject))
                revision=cur.fetchone()
                if not revision:return _result('temporary_error')
                cutoff=_erasure_cutoff(cur,tenant,subject)
                cur.execute('''SELECT * FROM saas_customer_memory_facts WHERE tenant_id=%s::uuid
                  AND contact_id=%s::uuid AND link_id=%s::uuid AND reliability='confirmed'
                  AND last_confirmed_at IS NOT NULL AND (valid_from IS NULL OR valid_from<=now())
                  AND (valid_to IS NULL OR valid_to>now()) ORDER BY updated_at DESC,id LIMIT %s''',
                  (tenant,subject,str(link['id']),_MAX_CANDIDATES))
                candidates=cur.fetchall();facts=[]
                for item in candidates:
                    if time.monotonic()>deadline:return _result('temporary_error')
                    if (item['link_version']!=link['version'] or not item['claim_token']
                            or not item['token_key_id'] or not item['evidence_quote_token']
                            or not item['claim_group_id']):continue
                    cur.execute('''SELECT 1 FROM saas_customer_memory_group_fences WHERE tenant_id=%s::uuid
                      AND contact_id=%s::uuid AND claim_group_id=%s::uuid''',
                      (tenant,subject,str(item['claim_group_id'])))
                    if cur.fetchone():continue
                    try:
                        source,source_version=_source(cur,tenant,link,str(item['source_message_id']))
                        if source_version!=item['source_version']:continue
                        if not _source_kind_supported(source,item['source_kind']):continue
                        if cutoff:_assert_claim_allowed(cur,tenant,subject,item['value'],item['token_key_id'],source)
                        value=_text(item['value'])
                    except TenantLinearError:continue
                    candidate={'category':item['category'],'value':value,
                      'source_kind':item['source_kind'],'reliability':item['reliability'],
                      'source_message_id':str(item['source_message_id']),
                      'event_at':item['event_at'],'last_confirmed_at':item['last_confirmed_at'],
                      'valid_to':item['valid_to'],'version':item['version']}
                    encoded=json.dumps(facts+[candidate],ensure_ascii=False,default=str,separators=(',',':')).encode('utf-8')
                    if len(encoded)>_MAX_FACT_BYTES:continue
                    facts.append(candidate)
                    if len(facts)>=_MAX_FACTS:break
                try:_verify_subject_revision(cur,tenant,subject,revision['revision'])
                except TenantLinearError:return _result('temporary_error')
                if time.monotonic()>deadline:return _result('temporary_error')
                if not facts:return _result('no_memory')
                return _result('preview', facts=facts, incomplete=True,
                  content_trust='untrusted_customer_data', binding=expected)
    except TenantLinearError as exc:
        if exc.status in (400,403,404):return _result('denied')
        if exc.status==409:return _result('ambiguous_identity')
        return _result('temporary_error')
    except (psycopg.Error, TimeoutError):
        return _result('temporary_error')
