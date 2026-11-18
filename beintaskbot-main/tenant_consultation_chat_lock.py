"""Common admission fence, acquired before consultation/source row locks."""
import json
from tenant_linear_policy import identifier


def lock_route(cur,tenant,case,channel,conversation):
    if not case:return
    key=json.dumps([identifier(tenant),identifier(case),identifier(channel),str(conversation)],separators=(',',':'))
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('case-chat:'+key,))


def lock_consultation(cur,profile,key):
    tenant=identifier(profile.get('tenant_id'));key=identifier(key)
    # Route proof only, not authorization. All current ACL checks follow while
    # this lock is held; the persisted case and destination are immutable.
    cur.execute('''SELECT c.case_id::text,d.channel_id::text,d.conversation_id
      FROM saas_internal_consultations c JOIN saas_consultation_group_deliveries d
        ON d.tenant_id=c.tenant_id AND d.consultation_id=c.id
      WHERE c.tenant_id=%s::uuid AND c.id=%s::uuid AND d.is_chat_followup=FALSE
      ORDER BY d.created_at,d.request_id LIMIT 1''',(tenant,key))
    row=cur.fetchone()
    if row:lock_route(cur,tenant,row['case_id'],row['channel_id'],row['conversation_id'])
