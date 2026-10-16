"""Agent trigger matching and tenant-safe handoff state transitions."""
import re
import uuid
from tenant_ai_agents_schema import ensure_schema
from tenant_platform import _connect, _json

_HANDOFF = re.compile(r"(?:\b(?:human|manager|supervisor|operator|agent)\b|менеджер|оператор|человек|руководител|menecer|operator|insan|canlı\s+dəstək|canli\s+destek)",re.I)


def should_handoff(text):
    return bool(_HANDOFF.search(str(text or '')[:6000]))


def enabled_triggers(tenant_id,agent_id):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT id,name,trigger_type,condition,actions FROM saas_ai_agent_triggers
              WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND enabled=TRUE ORDER BY created_at,id''',(tenant_id,agent_id))
            return [dict(row) for row in cur.fetchall()]


def matching_triggers(triggers,text,*,first_inquiry=False,phone=''):
    body=str(text or '').casefold()
    matches=[]
    for trigger in triggers:
        kind=trigger.get('trigger_type');condition=trigger.get('condition') or {}
        if kind=='new_inquiry' and first_inquiry:matches.append(trigger)
        elif kind=='phone_received' and phone:matches.append(trigger)
        elif kind=='phrase' and any(phrase.strip().casefold() in body for phrase in
            str(condition.get('phrase') or '').split('|') if phrase.strip()):matches.append(trigger)
    return matches


def handoff(job,reason):
    from tenant_support_inbox_store import handoff_ticket, summarize_created
    ticket = None
    tenant=str(job['tenant_id']);chat=str(job['chat_id'])
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute('''UPDATE saas_ai_conversation_state SET state='human',reason=%s,revision=revision+1,updated_at=now()
              WHERE tenant_id=%s::uuid AND provider=%s AND conversation_id=%s AND state<>'human' RETURNING agent_id''',
              (str(reason)[:240],tenant,job['provider'],chat))
            changed=cur.fetchone()
            ticket = handoff_ticket(cur, job, reason)
            if changed:
                cur.execute("UPDATE saas_ai_agent_jobs SET state='skipped',revision=revision+1,lease_token=NULL,lease_until=NULL,error_code='human_handoff',updated_at=now() WHERE tenant_id=%s::uuid AND provider=%s AND conversation_id=%s AND state IN ('queued','processing')",(tenant,job['provider'],chat))
                cur.execute("UPDATE saas_ai_agent_runs SET state='superseded',updated_at=now() WHERE tenant_id=%s::uuid AND event_fingerprint=%s AND state='generating'",(tenant,job['event_fingerprint']))
                cur.execute("UPDATE saas_tenant_webhook_events SET state='completed',completed_at=now(),error_code='human_handoff' WHERE tenant_id=%s::uuid AND fingerprint=%s",(tenant,job['event_fingerprint']))
                cur.execute("SELECT telegram_id FROM saas_tenant_members WHERE tenant_id=%s::uuid AND active=TRUE AND role IN ('owner','admin')",(tenant,))
                for member in cur.fetchall():
                    cur.execute('''INSERT INTO saas_tenant_notifications(id,tenant_id,telegram_id,event_key,title,body,target_url)
                      VALUES(%s,%s::uuid,%s,%s,'AI: менеджер должен ответить',%s,%s)''',
                      (uuid.uuid4(),tenant,member['telegram_id'],'ai_handoff:'+chat+':'+job['event_fingerprint'],str(reason)[:240],'/app?view=deals&deal_id='+str(job['lead_id'])))
        conn.commit()
    summarize_created(tenant, ticket)
    return bool(changed)
