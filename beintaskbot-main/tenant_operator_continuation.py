"""Explicit choice of existing channel state; legacy rows are never moved."""
import hashlib
import json
import uuid
from tenant_operator_legacy import admin
from tenant_operator_assignment import identity
from tenant_operator_access import assert_route
from tenant_ai_agent_connect import scope_matches
from tenant_ai_dialog_scope import lock_dialog
from tenant_ai_agents_store import _audit
from tenant_platform import _connect
from tenant_linear_policy import TenantLinearError


def inspect(cur,profile,data):
    tenant,actor,policy=admin(cur,profile)
    channel,conversation,lead=identity(data)
    route=assert_route(cur,tenant,policy,'connect',conversation,channel,lead)
    if conversation.endswith('@g.us') or route.get('conversation_kind')=='group':
        raise TenantLinearError('Группы требуют отдельной проверки координации.',409)
    cur.execute("SELECT * FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='connect' AND conversation_id=%s ORDER BY channel_id",(tenant,conversation))
    states=[dict(r) for r in cur.fetchall()]
    old=next((r for r in states if r['channel_id']==''),None)
    current=next((r for r in states if r['channel_id']==channel),None)
    cur.execute("SELECT id,name,role,enabled,mode,scopes,version FROM saas_ai_agents WHERE tenant_id=%s::uuid ORDER BY id",(tenant,))
    agents=[dict(a) for a in cur.fetchall() if a['role']=='primary' and a['enabled'] and scope_matches(a['scopes'],channel)]
    cur.execute("SELECT channel_id,state,revision,event_fingerprint FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND provider='connect' AND conversation_id=%s ORDER BY channel_id,agent_id",(tenant,conversation))
    jobs=[dict(r) for r in cur.fetchall()]
    cur.execute("""SELECT d.id,d.state FROM saas_ai_agent_drafts d JOIN saas_tenant_webhook_events e
      ON e.tenant_id=d.tenant_id AND e.fingerprint=d.source_fingerprint
      WHERE d.tenant_id=%s::uuid AND e.provider='connect' AND d.chat_id=%s
        AND d.state IN ('pending','sending','unknown') ORDER BY d.id""",(tenant,conversation))
    drafts=[dict(r) for r in cur.fetchall()]
    cur.execute("""SELECT r.id,r.state,r.result FROM saas_ai_agent_runs r JOIN saas_tenant_webhook_events e
      ON e.tenant_id=r.tenant_id AND e.fingerprint=r.event_fingerprint
      WHERE r.tenant_id=%s::uuid AND e.provider='connect'
        AND (e.payload->>'conversation_id'=%s OR EXISTS (
          SELECT 1 FROM saas_ai_agent_jobs j WHERE j.tenant_id=r.tenant_id AND j.provider='connect'
            AND j.event_fingerprint=r.event_fingerprint AND j.conversation_id=%s))
        AND (r.state IN ('generating','unknown') OR r.result->>'send_state' IN ('reserved','unknown')) ORDER BY r.id""",(tenant,conversation,conversation))
    runs=[dict(r) for r in cur.fetchall()]
    reason=('missing_state' if not old or not current else 'must_be_stopped' if current['state'] not in {'human','paused'} else
      'outstanding_work' if drafts or runs or any(j['state'] in {'queued','processing','unknown','drafted'} for j in jobs) else
      'no_eligible_agent' if not agents else '')
    token=hashlib.sha256(json.dumps([states,jobs,drafts,runs,agents],default=str,sort_keys=True).encode()).hexdigest()
    return {'allowed':not reason,'reason':reason,'expected_token':token,
      'agents':[{'id':str(a['id']),'name':a['name']} for a in agents]},(tenant,actor,channel,conversation,current,old)


def options(profile,data):
    with _connect() as conn:
        with conn.cursor() as cur:
            result,values=inspect(cur,profile,data)
            tenant,_,_,conversation,_,_=values
            cur.execute('''SELECT c.connect_channel_id AS channel_id,c.lead_id,ch.name FROM saas_connect_conversations c
              JOIN saas_connect_channels ch ON ch.tenant_id=c.tenant_id AND ch.connect_channel_id=c.connect_channel_id
              WHERE c.tenant_id=%s::uuid AND c.conversation_id=%s ORDER BY ch.name''',(tenant,conversation))
            result['channels']=[{**dict(r),'channel_id':str(r['channel_id'])} for r in cur.fetchall()]
            return result


def grant(profile,data):
    if data.get('confirmed') is not True:raise TenantLinearError('Требуется подтверждение администратора.',409)
    agent=str(uuid.UUID(str(data.get('agent_id'))))
    with _connect() as conn:
        with conn.cursor() as cur:tenant,_,_=admin(cur,profile)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SET LOCAL lock_timeout='1500ms'")
            cur.execute("SET LOCAL statement_timeout='5000ms'")
            # Match ingestion's dialogue-first order. Never wait for broad table
            # locks while another channel writer may hold a conflicting row lock.
            channel,conversation,_=identity(data)
            lock_dialog(cur,tenant,'connect',channel,conversation)
            cur.execute('LOCK TABLE saas_ai_conversation_state,saas_connect_conversations,saas_ai_agent_jobs,saas_ai_agent_drafts,saas_ai_agent_runs,saas_tenant_webhook_events,saas_ai_agents IN SHARE ROW EXCLUSIVE MODE NOWAIT')
            result,values=inspect(cur,profile,data)
            tenant,actor,channel,conversation,current,old=values
            if not result['allowed'] or result['expected_token']!=data.get('expected_token') or not any(a['id']==agent for a in result['agents']):
                raise TenantLinearError('Данные изменились или агент недоступен. Повторите проверку.',409)
            cur.execute('''INSERT INTO saas_ai_channel_legacy_state(tenant_id,provider,conversation_id,original,reason)
              VALUES(%s::uuid,'connect',%s,%s::jsonb,'channel_continuation_without_migration') ON CONFLICT DO NOTHING''',(tenant,conversation,json.dumps(old,default=str)))
            cur.execute('''UPDATE saas_ai_conversation_state SET agent_id=%s::uuid,agent_assignment_id=%s::uuid,
              revision=revision+1,updated_at=clock_timestamp() WHERE tenant_id=%s::uuid AND provider='connect'
              AND channel_id=%s AND conversation_id=%s RETURNING revision''',(agent,str(uuid.uuid4()),tenant,channel,conversation))
            revision=cur.fetchone()['revision']
            cur.execute('''INSERT INTO saas_ai_legacy_permits(tenant_id,channel_id,conversation_id,agent_id,legacy_revision,channel_revision,actor)
              VALUES(%s::uuid,%s,%s,%s::uuid,%s,%s,%s) ON CONFLICT(tenant_id,channel_id,conversation_id)
              DO UPDATE SET agent_id=EXCLUDED.agent_id,legacy_revision=EXCLUDED.legacy_revision,
              channel_revision=EXCLUDED.channel_revision,granted_at=clock_timestamp(),actor=EXCLUDED.actor''',
              (tenant,channel,conversation,agent,old['revision'],revision,actor))
            _audit(cur,tenant,actor,'ai_channel_continuation_granted','conversation',conversation,
              {'channel_id':channel,'agent_id':agent,'legacy_revision':old['revision'],'channel_revision':revision})
        conn.commit()
    return {'state':current['state'],'resume_required':True,'revision':revision}
