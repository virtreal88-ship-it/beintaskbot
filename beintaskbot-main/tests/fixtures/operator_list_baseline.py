"""Frozen list algorithm from 9bf0948, used only as a SQL/result oracle."""
from tenant_operator_dialogs import *
from tenant_operator_dialogs import _connect, _unread_counts

def listing(profile,search='',channel='',agent='',state='',offset=0):
    if state not in {'','automatic','manual','needs_assignment','attention','no_agent'}: raise TenantLinearError('Некорректный статус.')
    offset=max(0,min(int(offset),1000000))
    with _connect() as conn:
        ensure_schema(conn)
        from tenant_connect_transport import _schema as ensure_connect_schema
        ensure_connect_schema(conn)
        with conn.cursor() as cur:
            tenant,_,policy=access(cur,profile,'dialogs')
            sql,scopes=projection(policy);params=(tenant,tenant,*scopes,runtime_flag('AI_AGENTS_ENABLED'))
            cur.execute(sql+"SELECT count(DISTINCT CASE WHEN provider='connect' AND conversation_id ~ '^[0-9]{5,22}(-[0-9]{5,22})?@g[.]us$' THEN provider||':'||conversation_id ELSE provider||':'||channel_id||':'||lead_id END) FILTER(WHERE display_state IN ('human','paused','blocked','error')) AS attention_count FROM visible",params)
            count=int(cur.fetchone()['attention_count'])
            cur.execute(sql+"SELECT DISTINCT provider||':'||channel_id AS id,channel_name AS name FROM visible ORDER BY name",params)
            channels=[dict(r) for r in cur.fetchall()]
            cur.execute(sql+'SELECT DISTINCT agent_id::text AS id,agent_name AS name FROM visible WHERE agent_id IS NOT NULL ORDER BY name',params)
            agents=[dict(r) for r in cur.fetchall()]
            cur.execute(sql+"SELECT * FROM (SELECT visible.*,row_number() OVER(PARTITION BY CASE WHEN provider='connect' AND conversation_id ~ '^[0-9]{5,22}(-[0-9]{5,22})?@g[.]us$' THEN provider||':'||conversation_id ELSE provider||':'||channel_id||':'||lead_id END ORDER BY last_message_at DESC NULLS LAST,lead_id) AS logical_rank FROM visible) folded WHERE logical_rank=1 ORDER BY last_message_at DESC NULLS LAST,provider,lead_id",params)
            rows=cur.fetchall()
            from tenant_ai_group_coordination import group_jid,view as group_view
            rows=[decorate(r) for r in rows]
            for r in rows:
                if r['provider']=='connect' and group_jid(r['conversation_id']):
                    apply_group_view(cur,tenant,r)
                    from tenant_operator_assignment import group_options_in_transaction
                    r['group_assignment_preview']=group_options_in_transaction(cur,tenant,r['channel_id'],r['conversation_id'])
                    if not r.get('agent_id') and not r.get('group_coordination',{}).get('assignments'):
                        candidates=r['group_assignment_preview'].get('agents') or []
                        r['agent_status']='needs_assignment' if candidates else 'no_agent'
                        r['agent_status_reason']='assignment_required' if candidates else r['group_assignment_preview'].get('reason') or ''
                elif r['provider']=='connect' and not r.get('agent_id') and r.get('unambiguous') and r.get('controls_allowed'):
                    # Surface the same read-only candidate choices used by the
                    # card for every page of existing dialogues. Never bind or
                    # resume an agent while listing conversations.
                    from tenant_operator_assignment import options_in_transaction
                    r['agent_assignment']=options_in_transaction(cur,profile,{
                        'provider':'connect','lead_id':r['lead_id'],'channel_id':r['channel_id'],
                        'conversation_id':r['conversation_id']})
                    r['agent_status']='needs_assignment' if r['agent_assignment'].get('agents') else 'no_agent'
                    r['agent_status_reason']=('assignment_required' if len(r['agent_assignment'].get('agents') or [])==1 else
                      'agent_selection_required' if len(r['agent_assignment'].get('agents') or [])>1 else r['agent_assignment'].get('reason') or '')
            unread_total=_unread_counts(cur,tenant,int(profile['telegram_id']),rows)
            if search:
                query_text='%'+str(search)[:200]+'%'
                rows=[r for r in rows if any(query_text[1:-1].casefold() in str(r.get(key) or '').casefold() for key in ('customer_name','phone','conversation_id'))]
            if channel:rows=[r for r in rows if str(r.get('provider') or '')+':'+str(r.get('channel_id') or '')==str(channel)[:250]]
            for r in rows:
                if r.get('agent_id') and r.get('agent_name') and not any(str(a.get('id'))==str(r['agent_id']) for a in agents):
                    agents.append({'id':str(r['agent_id']),'name':r['agent_name']})
            if agent=='unassigned':rows=[r for r in rows if not r.get('agent_id')]
            elif agent:
                selected_agent=identifier(agent)
                rows=[r for r in rows if str(r.get('agent_id') or '')==selected_agent]
            rows=[r for r in rows if not state or r.get('agent_status')==state]
            agents.sort(key=lambda item:str(item.get('name') or '').casefold())
            total_count=len(rows)
            rows=rows[offset:offset+51]
    return {'dialogs':rows[:50],'channels':channels,'agents':agents,
            'attention_count':count,'unread_count':unread_total,'total_count':total_count,
            'next_offset':offset+50 if len(rows)>50 else None}
