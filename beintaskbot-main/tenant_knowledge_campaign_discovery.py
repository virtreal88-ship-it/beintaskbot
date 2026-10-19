"""Bounded SQL discovery. Content remains in existing scoped CRM history."""
from datetime import timedelta
from tenant_platform import _json
from tenant_linear_policy import TenantLinearError
from tenant_knowledge_candidates_policy import moment
from tenant_ai_group_coordination import group_jid
import tenant_knowledge_campaigns as queue
import tenant_knowledge_candidates_store as store


def inventory(cur, profile, channels):
    tenant,_,_=store.authorize(cur,profile)
    # Bounded keyset pages; initial metadata snapshot commits atomically with
    # durable per-source cursors. A crash here repeats reads, never AI calls.
    rows=[];after_channel='00000000-0000-0000-0000-000000000000';after_conversation=''
    while True:
        cur.execute("""SELECT c.connect_channel_id AS channel_id,c.conversation_id,
          min(COALESCE(m.happened_at,m.created_at)) AS earliest,
          max(COALESCE(m.happened_at,m.created_at)) AS latest,
          count(*) AS messages,count(*) FILTER(WHERE m.message_type<>'text') AS attachments
          FROM saas_connect_conversations c JOIN saas_crm_messages m ON m.tenant_id=c.tenant_id
          AND m.kommo_lead_id=c.lead_id AND m.channel='connect:'||c.connect_channel_id::text
          WHERE c.tenant_id=%s::uuid AND c.connect_channel_id=ANY(%s::uuid[])
          AND (c.connect_channel_id,c.conversation_id)>(%s::uuid,%s)
          GROUP BY c.connect_channel_id,c.conversation_id
          ORDER BY c.connect_channel_id,c.conversation_id LIMIT 100""",
          (tenant,channels,after_channel,after_conversation))
        page=cur.fetchall();rows.extend(page)
        if len(page)<100:return rows
        after_channel=str(page[-1]['channel_id']);after_conversation=page[-1]['conversation_id']



def seed(cur, profile, campaign):
    """One initial inventory, no age cutoff; raw copy counts are not unique counts."""
    rows=inventory(cur,profile,campaign['sources']);groups={}
    for row in rows:
        identity=row['conversation_id'] if group_jid(row['conversation_id']) else str(row['channel_id'])+':'+row['conversation_id']
        if identity in groups:
            old=groups[identity];old['earliest']=min(old['earliest'],row['earliest']);old['latest']=max(old['latest'],row['latest'])
        else:groups[identity]=dict(row)
    for identity,row in groups.items():
        route={'provider':'connect','channel_id':str(row['channel_id']),'conversation_id':row['conversation_id'],
          'history_mode':'unified_group' if group_jid(row['conversation_id']) else 'channel'}
        store.route(cur,profile,route)
        start=moment(str(row['earliest']));end=moment(str(row['latest']))+timedelta(seconds=1)
        cur.execute('''INSERT INTO saas_knowledge_campaign_sources
          (tenant_id,campaign_id,source_key,channel_id,conversation_id,history_mode,period_start,period_end,next_start)
          VALUES(%s::uuid,%s::uuid,%s,%s::uuid,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
          (campaign['tenant_id'],campaign['id'],store.digest(identity),row['channel_id'],row['conversation_id'],route['history_mode'],start,end,start))
    progress={'phase':'history','source_count':len(groups),'stored_message_copies':sum(r['messages'] for r in rows),
      'stored_attachment_copies':sum(r['attachments'] for r in rows)}
    cur.execute('UPDATE saas_knowledge_campaigns SET progress=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid',(_json(progress),campaign['tenant_id'],campaign['id']))


def discover(cur, profile, campaign):
    """At most one <=30-day window per tick. Existing 80-message limits unchanged."""
    if not campaign['progress']:
        seed(cur,profile,campaign);return
    cur.execute("SELECT count(*) AS n FROM saas_knowledge_campaign_fragments WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid AND status IN ('pending','running')",(campaign['tenant_id'],campaign['id']))
    if cur.fetchone()['n']>=4:return
    cur.execute("SELECT * FROM saas_knowledge_campaign_sources WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid AND status='pending' ORDER BY source_key LIMIT 1 FOR UPDATE",(campaign['tenant_id'],campaign['id']))
    source=cur.fetchone()
    if not source:return
    start=moment(str(source['next_start']));end=min(start+timedelta(days=30),moment(str(source['period_end'])))
    route={'provider':'connect','channel_id':str(source['channel_id']),'conversation_id':source['conversation_id'],
      'history_mode':source['history_mode'],'period_start':start.isoformat(),'period_end':end.isoformat(),
      'analysis_agent_id':str(campaign['analysis_agent_id']),'allow_external_media':True,'allow_paid_asr':True}
    queue.enqueue(cur,profile,campaign,route)
    cur.execute('''UPDATE saas_knowledge_campaign_sources SET next_start=%s,status=%s
      WHERE tenant_id=%s::uuid AND campaign_id=%s::uuid AND source_key=%s''',
      (end,'complete' if end>=moment(str(source['period_end'])) else 'pending',campaign['tenant_id'],campaign['id'],source['source_key']))


def discover_resolved(cur, profile, campaign):
    """Only explicit resolved tickets, stable for 30 minutes. Silence is not resolution."""
    cur.execute('''SELECT t.* FROM saas_support_tickets t WHERE t.tenant_id=%s::uuid
      AND t.channel_id=ANY(%s::uuid[]) AND t.status='resolved' AND t.resolved_at IS NOT NULL
      AND t.updated_at<now()-interval '30 minutes'
      AND NOT EXISTS(SELECT 1 FROM saas_connect_conversations c JOIN saas_crm_messages m
        ON m.tenant_id=c.tenant_id AND m.kommo_lead_id=c.lead_id AND m.channel='connect:'||c.connect_channel_id::text
        WHERE c.tenant_id=t.tenant_id AND c.conversation_id=t.conversation_id
        AND c.connect_channel_id=ANY(%s::uuid[])
        AND (c.connect_channel_id=t.channel_id OR t.conversation_id ~ '^[0-9]{5,22}(-[0-9]{5,22})?@g[.]us$')
        AND m.created_at>t.resolved_at)
      AND NOT EXISTS(SELECT 1 FROM saas_knowledge_campaign_sources s WHERE s.tenant_id=t.tenant_id
        AND s.campaign_id=%s::uuid AND s.source_key='ticket:'||t.id::text||':'||t.resolved_at::text)
      ORDER BY t.resolved_at,t.id LIMIT 1''',(campaign['tenant_id'],campaign['sources'],campaign['sources'],campaign['id']))
    ticket=cur.fetchone()
    if not ticket:return
    start=moment(str(ticket['created_at']));end=moment(str(ticket['resolved_at']))+timedelta(microseconds=1)
    if end<=start:return
    cur.execute('''INSERT INTO saas_knowledge_campaign_sources
      (tenant_id,campaign_id,source_key,channel_id,conversation_id,history_mode,period_start,period_end,next_start)
      VALUES(%s::uuid,%s::uuid,'ticket:'||%s::text||':'||%s::timestamptz::text,%s::uuid,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
      (campaign['tenant_id'],campaign['id'],ticket['id'],ticket['resolved_at'],ticket['channel_id'],ticket['conversation_id'],
       'unified_group' if group_jid(ticket['conversation_id']) else 'channel',start,end,start))

def bound_context(item):
    """Advance by actually admitted evidence; never strand media behind a page cursor."""
    def over():
        kinds=[r.get('media_kind') for r in item['refs']]
        return (sum(bool(k) for k in kinds)>4 or
          sum({'image':1,'video':2,'document':2}.get(k,0) for k in kinds)>2 or
          sum(k in {'audio','voice','video'} for k in kinds)>1)
    trimmed=False
    while len(item['refs'])>1 and over():
        item['refs'].pop(0);item['messages'].pop(0);trimmed=True
    if trimmed:item['next_before']=item['refs'][0]['id']
