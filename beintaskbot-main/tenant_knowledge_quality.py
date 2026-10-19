"""Knowledge checks reuse private virtual tests, never customer delivery."""
import asyncio
import uuid
import re
from tenant_platform import _connect
from tenant_linear_policy import TenantLinearError
import tenant_knowledge_candidates_store as store


def schema(cur):
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_knowledge_quality_checks(
      tenant_id UUID NOT NULL,campaign_id UUID NOT NULL,id UUID NOT NULL,
      source_id UUID NOT NULL,agent_id UUID NOT NULL,source_updated_at TIMESTAMPTZ NOT NULL,
      state TEXT NOT NULL DEFAULT 'pending',retrieval_state TEXT NOT NULL DEFAULT 'pending',
      response_state TEXT NOT NULL DEFAULT 'pending',created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,campaign_id,source_id,source_updated_at),
      FOREIGN KEY(tenant_id,campaign_id) REFERENCES saas_knowledge_campaigns(tenant_id,id),
      FOREIGN KEY(tenant_id,source_id) REFERENCES saas_ai_knowledge_sources(tenant_id,id))''')


def reserve_budget(cur,tenant,campaign_id,tokens):
    cur.execute("SELECT id FROM saas_knowledge_campaigns WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE",(tenant,campaign_id))
    cur.fetchone()
    cur.execute("SELECT 1 FROM saas_knowledge_campaign_fragments WHERE tenant_id=%s::uuid AND status IN ('running','unknown') LIMIT 1",(tenant,))
    if cur.fetchone():raise TenantLinearError('Дождитесь завершения или проверки текущего анализа.',409)
    cur.execute("SELECT 1 FROM saas_knowledge_quality_checks q JOIN saas_ai_test_turns t ON t.tenant_id=q.tenant_id AND t.id=q.id WHERE q.tenant_id=%s::uuid AND t.state IN ('generating','unknown') LIMIT 1",(tenant,))
    if cur.fetchone():raise TenantLinearError('Дождитесь проверки предыдущего AI-вызова.',409)
    cur.execute('''UPDATE saas_knowledge_campaigns SET reserved_calls=reserved_calls+1,
        reserved_kinds=jsonb_set(reserved_kinds,'{main}',to_jsonb(COALESCE((reserved_kinds->>'main')::int,0)+1)),
      reserved_tokens=reserved_tokens+%s WHERE tenant_id=%s::uuid AND id=%s::uuid
      AND status='running' AND approved_at IS NOT NULL
      AND COALESCE((reserved_kinds->>'main')::int,0)+1<=COALESCE((media_policy->>'max_main_calls')::int,0)
        AND reserved_calls+1<=max_calls AND reserved_tokens+%s<=max_tokens RETURNING id''',(tokens,tenant,campaign_id,tokens))
    if not cur.fetchone():
        error=TenantLinearError('Проверка знаний остановлена или бюджет исчерпан.',429);error.campaign_budget=True;raise error


def check_active(profile,campaign_id):
    with _connect() as conn:
        with conn.cursor() as cur:
            tenant,_,_=store.authorize(cur,profile)
            cur.execute("SELECT 1 FROM saas_knowledge_campaigns c JOIN saas_ai_agent_settings s ON s.tenant_id=c.tenant_id WHERE c.tenant_id=%s::uuid AND c.id=%s::uuid AND c.status='running' AND s.enabled AND NOT s.emergency_stop",(tenant,campaign_id))
            if not cur.fetchone():raise TenantLinearError('Проверка знаний остановлена.',409)


def verdict(turn,source,steps):
    if turn.get('state')!='completed':return 'not_checked','not_checked'
    found=any(str(k.get('source_id'))==str(source) for k in (turn.get('trace') or {}).get('knowledge',[]))
    if not found:return 'missing_source','not_checked'
    normalize=lambda text:re.sub(r'\W+',' ',str(text).casefold()).strip()
    # Conservative exact-match check only. Paraphrases and additional claims go
    # to employee review; this is explicitly not a semantic correctness score.
    answer=normalize(turn.get('response'))
    allowed={normalize(' '.join(step for step in steps if step))}
    return 'found','exact_supported' if answer and answer in allowed else 'requires_review'


def reconcile(profile, campaign_id):
    """Finish persisted receipts after a crash; never restart an uncertain AI call."""
    with _connect() as conn:
        with conn.cursor() as cur:
            tenant, _, _ = store.authorize(cur, profile)
            schema(cur)
            cur.execute("""SELECT q.id,q.source_id,t.state,t.response,t.trace,c.content
              FROM saas_knowledge_quality_checks q
              JOIN saas_ai_test_turns t ON t.tenant_id=q.tenant_id AND t.id=q.id
              JOIN saas_knowledge_candidate_sources p ON p.tenant_id=q.tenant_id AND p.source_id=q.source_id
              JOIN saas_knowledge_candidates c ON c.tenant_id=p.tenant_id AND c.id=p.candidate_id
              WHERE q.tenant_id=%s::uuid AND q.campaign_id=%s::uuid AND q.state IN ('running','unknown')
              AND t.state NOT IN ('generating','pending') LIMIT 4""", (tenant,campaign_id))
            for row in cur.fetchall():
                retrieval,response=verdict(row,row['source_id'],row['content'].get('resolution_steps',[]))
                cur.execute("""UPDATE saas_knowledge_quality_checks SET state=%s,retrieval_state=%s,response_state=%s
                  WHERE tenant_id=%s::uuid AND id=%s::uuid AND state IN ('running','unknown')""",
                  (row['state'],retrieval,response,tenant,row['id']))
        conn.commit()


def tick(profile,campaign_id):
    reconcile(profile,campaign_id)
    from tenant_ai_testing_store import create
    from tenant_ai_testing_service import turn
    with _connect() as conn:
        with conn.cursor() as cur:
            tenant,_,_=store.authorize(cur,profile);schema(cur)
            cur.execute('''SELECT DISTINCT s.id,s.agent_id,s.updated_at,s.title,c.content
              FROM saas_knowledge_candidate_sources p JOIN saas_knowledge_candidates c
              ON c.tenant_id=p.tenant_id AND c.id=p.candidate_id JOIN saas_ai_knowledge_sources s
              ON s.tenant_id=p.tenant_id AND s.id=p.source_id WHERE c.tenant_id=%s::uuid
              AND c.status='approved' AND s.active AND c.extraction_id IN
              (SELECT extraction_id FROM saas_knowledge_campaign_fragments WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid)
              AND NOT EXISTS(SELECT 1 FROM saas_knowledge_quality_checks q WHERE q.state<>'pending' AND q.tenant_id=s.tenant_id
                AND q.campaign_id=%s::uuid AND q.source_id=s.id AND q.source_updated_at=s.updated_at)
              ORDER BY s.updated_at,s.id LIMIT 1''',(tenant,tenant,campaign_id,campaign_id))
            source=cur.fetchone()
            if not source:return False
            key=str(uuid.uuid5(uuid.UUID(campaign_id),str(source['id'])+str(source['updated_at'])))
            cur.execute('''INSERT INTO saas_knowledge_quality_checks(tenant_id,campaign_id,id,source_id,agent_id,source_updated_at,state)
              VALUES(%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,'running') ON CONFLICT(tenant_id,id) DO UPDATE SET state='running' WHERE saas_knowledge_quality_checks.state='pending' RETURNING id''',
              (tenant,campaign_id,key,source['id'],source['agent_id'],source['updated_at']))
            if not cur.fetchone():return False
        conn.commit()
    state='unknown';retrieval=response='not_checked'
    try:
        create(profile,{'id':key,'agent_id':str(source['agent_id'])})
        result=asyncio.run(turn(profile,key,{'request_id':key,'text':source['title']},campaign_id=campaign_id))
        latest=result['turns'][-1] if result.get('turns') else {}
        retrieval,response=verdict(latest,source['id'],source['content'].get('resolution_steps',[]))
        state=latest.get('state') or 'unknown'
    except TenantLinearError as error:
        state='blocked'
        if error.status==429:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE saas_knowledge_campaigns SET status=%s,version=version+1 WHERE tenant_id=%s::uuid AND id=%s::uuid AND status='running'",('budget_exhausted' if getattr(error,'campaign_budget',False) else 'paused',tenant,campaign_id))
                conn.commit()
    finally:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute('''UPDATE saas_knowledge_quality_checks SET state=%s,retrieval_state=%s,response_state=%s
                  WHERE tenant_id=%s::uuid AND id=%s::uuid''',(state,retrieval,response,tenant,key))
            conn.commit()
    return True
