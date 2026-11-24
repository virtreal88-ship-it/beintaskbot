"""Owner-only duplicate discovery. Search matches are never identity evidence."""
from tenant_linear_policy import TenantLinearError
from tenant_operator_access import access
from tenant_platform import _connect

PAGE_SIZE = 25


def discover(profile, params):
    from tenant_ai_agent_runtime_flags import enabled
    from tenant_operator_dialogs import projection
    mode = params.get('mode', 'phone')
    if mode not in ('phone','name','both','missing'):
        raise TenantLinearError('Некорректный поиск диалогов.',400)
    offset=max(0,min(int(params.get('offset') or 0),5000))
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout='5s'")
        cur.execute("SET LOCAL lock_timeout='2s'")
        tenant,actor,policy=access(cur,profile,'dialogs')
        if policy.profile.get('role')!='owner':
            raise TenantLinearError('Поиск дублей доступен владельцу.',403)
        sql,scopes=projection(cur,policy,allow_closed_archive=True)
        args=[tenant,tenant,*scopes,enabled('AI_AGENTS_ENABLED')]
        from tenant_schema_preflight import ready
        aliases=ready(cur,{'saas_connect_direct_identities':('tenant_id','connect_channel_id','canonical_conversation_id','identity_key')},[])
        conflicts=ready(cur,{'saas_connect_identity_conflicts':('tenant_id','connect_channel_id','conversation_id')},[])
        manual=ready(cur,{'saas_connect_dialog_display_phones':('tenant_id','channel_id','conversation_id','lead_id','phone')},[])
        manual_value="COALESCE(c.phone,'')" if manual else "''"
        manual_join="""LEFT JOIN saas_connect_dialog_display_phones c ON c.tenant_id=%s::uuid
              AND c.channel_id=v.channel_id::uuid AND c.conversation_id=v.conversation_id
              AND c.lead_id=v.lead_id""" if manual else ''
        alias_sql="""UNION SELECT split_part(i.identity_key,'@',1) FROM saas_connect_direct_identities i
             WHERE i.tenant_id=%s::uuid AND i.connect_channel_id=v.channel_id::uuid
               AND i.canonical_conversation_id=v.conversation_id
               AND i.identity_key ~ '^[1-9][0-9]{6,14}@(s[.]whatsapp[.]net|c[.]us)$'""" if aliases else ''
        if aliases:args.append(tenant)
        conflict_sql="""v.conversation_id LIKE '%%@lid' AND EXISTS(SELECT 1 FROM saas_connect_identity_conflicts i
            WHERE i.tenant_id=%s::uuid AND i.connect_channel_id=v.channel_id::uuid
              AND i.conversation_id=v.conversation_id)""" if conflicts else 'FALSE'
        if conflicts:args.append(tenant)
        if manual:args.append(tenant)
        from tenant_dialog_rollout import guard as rollout_guard
        archive=rollout_guard(cur,tenant).enforced
        archive_join="""LEFT JOIN saas_dialog_workflows w ON w.tenant_id=%s::uuid AND w.channel_id=v.channel_id::uuid
              AND w.conversation_id=v.conversation_id""" if archive else ''
        archive_value="COALESCE(w.state IN ('closed','dismissed') AND w.operator_id IS DISTINCT FROM actor.id,FALSE)" if archive else 'FALSE'
        phone_writable="COALESCE(w.state IN ('waiting','accepted'),TRUE)" if archive else 'TRUE'
        if archive:args.extend((tenant,actor))
        sql+=""" , direct AS (
           SELECT v.provider,v.channel_id,v.conversation_id,v.lead_id,v.channel_name,v.last_message_at,
             CASE WHEN v.customer_name=v.conversation_id OR v.customer_name=split_part(v.conversation_id,'@',1)
                OR v.customer_name ~* '@(lid|g[.]us|s[.]whatsapp[.]net|c[.]us)$' THEN '' ELSE v.customer_name END AS customer_name,
             """+manual_value+""" AS manual_display_phone,
             """+archive_value+""" AS archive_read_only,
             (NOT identity_conflict AND NOT p.ambiguous AND """+phone_writable+""") AS display_phone_editable,
             CASE WHEN identity_conflict OR (p.ambiguous AND v.conversation_id LIKE '%%@lid') THEN '' ELSE COALESCE(
                CASE WHEN v.phone ~ '^\\+?[0-9(). -]+$' AND length(regexp_replace(v.phone,'[^0-9]','','g')) BETWEEN 7 AND 15
                   AND NOT (v.conversation_id LIKE '%%@lid' AND split_part(v.conversation_id,'@',1)=regexp_replace(v.phone,'[^0-9]','','g'))
                  THEN v.phone END, p.phone,'') END AS phone
           FROM visible v
           CROSS JOIN LATERAL (
             SELECT CASE WHEN count(*)=1 THEN '+'||min(pn) END AS phone,count(*)>1 AS ambiguous
                FROM (SELECT split_part(v.conversation_id,'@',1) AS pn
                 WHERE v.conversation_id ~ '^[1-9][0-9]{6,14}@(s[.]whatsapp[.]net|c[.]us)$'
                 """+alias_sql+""") proofs
           ) p
           CROSS JOIN LATERAL (SELECT ("""+conflict_sql+""") AS identity_conflict) guard
           """+manual_join+" "+archive_join+(" CROSS JOIN (SELECT %s::bigint AS id) actor" if archive else "")+"""
           WHERE v.provider='connect' AND v.conversation_kind IN ('contact','') AND
             v.conversation_id ~ '^[1-9][0-9]{5,22}@(s[.]whatsapp[.]net|c[.]us|lid)$'
        ), hints AS (
          SELECT d.*,
            CASE WHEN length(regexp_replace(COALESCE(NULLIF(phone,''),manual_display_phone),'[^0-9]','','g'))>=7
                 THEN right(regexp_replace(COALESCE(NULLIF(phone,''),manual_display_phone),'[^0-9]','','g'),7)
                 ELSE '' END AS suffix,
            CASE WHEN customer_name ~ '^[+0-9().[:space:]-]+$' OR lower(btrim(customer_name)) IN
                 ('whatsapp contact','контакт whatsapp','whatsapp kontaktı') THEN ''
                 ELSE lower(regexp_replace(btrim(customer_name),'[[:space:]]+',' ','g')) END AS normalized_name
          FROM direct d
        ) """
        columns="provider,channel_id,conversation_id,lead_id,customer_name,phone,manual_display_phone,archive_read_only,display_phone_editable,channel_name"
        if mode=='missing':
            sql+="SELECT "+columns+" FROM hints WHERE phone='' AND manual_display_phone='' ORDER BY last_message_at DESC NULLS LAST,channel_id,lead_id"
        else:
            # Partition by exact source because merge may never cross a source.
            # A windowed group emits one row per route, never all N² pairs.
            keys={'phone':'suffix','name':'normalized_name','both':'suffix,normalized_name'}[mode]
            present={'phone':"suffix<>''",'name':"normalized_name<>''",'both':"suffix<>'' AND normalized_name<>''"}[mode]
            sql+=""" , candidate_rows AS (
              SELECT h.*,count(*) OVER (PARTITION BY channel_id,"""+keys+""") AS group_size,
                min(lead_id) OVER (PARTITION BY channel_id,"""+keys+""") AS candidate_group
              FROM hints h WHERE """+present+"""
            ) SELECT """+columns+""",candidate_group,group_size FROM candidate_rows
                  WHERE group_size>1 ORDER BY channel_id,candidate_group,last_message_at DESC NULLS LAST,lead_id"""
        cur.execute(sql+" LIMIT %s OFFSET %s",(*args,PAGE_SIZE+1,offset))
        rows=[dict(row) for row in cur.fetchall()]
    return {'dialogs':rows[:PAGE_SIZE],'next_offset':offset+PAGE_SIZE if len(rows)>PAGE_SIZE and offset<5000 else None}
