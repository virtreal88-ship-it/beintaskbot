"""Read-only gate for provider-confirmed split direct-chat identities.

Historical messages and assignments are never merged here. The signed Connect
receipt is the evidence; names, avatars and numeric suffixes are not identities.
"""

def conflicted(cur, tenant, channel, conversation):
    cur.execute("""SELECT 1 FROM saas_crm_messages m
      WHERE m.tenant_id=%s::uuid AND m.channel=%s
        AND m.raw->>'conversationIdentityConflict'='true'
        AND (m.raw->>'conversationId'=%s OR m.raw->'directIdentity'->>'lid'=%s
             OR m.raw->'directIdentity'->>'jid'=%s)
      LIMIT 1""", (tenant, 'connect:' + str(channel), str(conversation), str(conversation), str(conversation)))
    return bool(cur.fetchone())


def inventory(cur, tenant, channel):
    """Full channel inventory; hints are diagnostics, never identity grants.

    Signed event pairs and persisted provider aliases are the only identity
    evidence. Display names/phones may reveal missed records but cannot make
    them eligible. Keep private identifiers inside the server transaction.
    """
    import re
    from collections import defaultdict
    direct=lambda value: bool(re.fullmatch(r'[1-9][0-9]{5,22}@(s\.whatsapp\.net|c\.us|lid)',str(value or '')))
    cur.execute('''SELECT c.*,count(m.id) AS messages,
      count(m.id) FILTER(WHERE m.direction='incoming') AS incoming,
      count(m.id) FILTER(WHERE m.direction='outgoing') AS outgoing
      FROM saas_connect_conversations c LEFT JOIN saas_crm_messages m
        ON m.tenant_id=c.tenant_id AND m.kommo_lead_id=c.lead_id
        AND m.channel='connect:'||c.connect_channel_id::text
      WHERE c.tenant_id=%s::uuid AND c.connect_channel_id=%s::uuid
      GROUP BY c.tenant_id,c.connect_channel_id,c.conversation_id ORDER BY c.conversation_id''',(tenant,channel))
    all_routes=cur.fetchall()
    routes={str(r['conversation_id']):dict(r) for r in all_routes
            if direct(r['conversation_id']) and r.get('conversation_kind')!='group'}
    graph=defaultdict(set); evidence={}; conflicts=set()
    def add(a,b,kind):
        if not direct(a) or not direct(b) or a==b:return
        # Canonical phone JID first, LID/legacy alias second, deterministically.
        a,b=sorted((a,b),key=lambda x:(x.endswith('@lid'),not x.endswith('@s.whatsapp.net'),x))
        graph[a].add(b);graph[b].add(a)
        row=evidence.setdefault((a,b),{'sources':set(),'receipts':0})
        row['sources'].add(kind);row['receipts']+=1
    cur.execute('''SELECT m.raw FROM saas_crm_messages m
      WHERE m.tenant_id=%s::uuid AND m.channel=%s AND m.external_id LIKE 'connect:%%'
        AND m.raw ? 'directIdentity' ORDER BY m.id''',(tenant,'connect:'+str(channel)))
    for row in cur.fetchall():
        raw=row.get('raw') or {};pair=raw.get('directIdentity') if isinstance(raw,dict) else None
        if not isinstance(pair,dict):continue
        jid,lid=pair.get('jid'),pair.get('lid')
        if not (direct(jid) and str(jid).endswith(('@s.whatsapp.net','@c.us')) and direct(lid)
                and str(lid).endswith('@lid') and raw.get('conversationId') in {jid,lid}):continue
        add(jid,lid,'signed_provider_pair')
        if raw.get('conversationIdentityConflict') is True:conflicts.update((jid,lid))
    cur.execute("SELECT to_regclass('public.saas_connect_direct_identities') AS present")
    if cur.fetchone()['present']:
        cur.execute('''SELECT identity_key,canonical_conversation_id FROM saas_connect_direct_identities
          WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid ORDER BY identity_key''',(tenant,channel))
        for row in cur.fetchall():add(row['canonical_conversation_id'],row['identity_key'],'persisted_provider_alias')
    # Legacy phone-JID domains represent the same provider identity, but this
    # remains a review candidate, not an automatic transport reassignment.
    for key in routes:
        if key.endswith('@c.us'):
            other=key.removesuffix('@c.us')+'@s.whatsapp.net'
            if other in routes:add(other,key,'provider_jid_domain_alias')
    hints=defaultdict(set)
    for key,row in routes.items():
        phone=re.sub(r'\D','',str(row.get('phone') or ''))
        name=str(row.get('manual_display_name') or row.get('contact_name') or row.get('display_name') or '').strip().casefold()
        if len(phone)>=6:hints[('stored_phone',phone)].add(key)
        if name and not re.fullmatch(r'[\d\W]+',name):hints[('display_name',name)].add(key)
    for (kind,_),keys in hints.items():
        ordered=sorted(keys)
        for i,a in enumerate(ordered):
            for b in ordered[i+1:]:
                pair=tuple(sorted((a,b),key=lambda x:(x.endswith('@lid'),not x.endswith('@s.whatsapp.net'),x)))
                if pair not in evidence:evidence[pair]={'sources':set(),'receipts':0}
                evidence[pair]['sources'].add(kind+'_hint')
    # Repeated provider message IDs can reveal linked-device echoes or an old
    # lead mapping. They are review hints, not proof that two people are one.
    cur.execute('''SELECT array_agg(DISTINCT kommo_lead_id) AS leads FROM saas_crm_messages
      WHERE tenant_id=%s::uuid AND channel=%s AND external_id LIKE 'connect:%%'
        AND NULLIF(raw->>'externalMessageId','') IS NOT NULL
      GROUP BY raw->>'externalMessageId' HAVING count(DISTINCT kommo_lead_id)>1''',(tenant,'connect:'+str(channel)))
    by_lead={int(row['lead_id']):key for key,row in routes.items()}
    for echo in cur.fetchall():
        keys=sorted({by_lead[int(lead)] for lead in echo['leads'] if int(lead) in by_lead})
        for i,a in enumerate(keys):
            for b in keys[i+1:]:
                pair=tuple(sorted((a,b),key=lambda x:(x.endswith('@lid'),not x.endswith('@s.whatsapp.net'),x)))
                evidence.setdefault(pair,{'sources':set(),'receipts':0})['sources'].add('linked_device_echo_hint')
    items=[]
    for (a,b),proof in sorted(evidence.items()):
        reasons=[];verified=bool(proof['sources'] & {'signed_provider_pair','persisted_provider_alias','provider_jid_domain_alias'})
        if not verified:reasons.append('provider_identity_unproven')
        if a not in routes or b not in routes:reasons.append('route_missing')
        if len(graph[a])>1 or len(graph[b])>1:reasons.append('provider_identity_ambiguous')
        if a in conflicts or b in conflicts:reasons.append('provider_identity_conflict')
        items.append({'canonical_id':a,'split_id':b,'sources':sorted(proof['sources']),
                      'receipts':proof['receipts'],'conflicts':reasons,'verified':verified})
    cur.execute('''SELECT count(*) AS n FROM saas_crm_messages m WHERE m.tenant_id=%s::uuid AND m.channel=%s
      AND NOT EXISTS(SELECT 1 FROM saas_connect_conversations c WHERE c.tenant_id=m.tenant_id
        AND c.connect_channel_id=%s::uuid AND c.lead_id=m.kommo_lead_id)''',(tenant,'connect:'+str(channel),channel))
    return {'routes':routes,'pairs':items,'scanned_routes':len(all_routes),
            'excluded_routes':len(all_routes)-len(routes),'unmapped_messages':int(cur.fetchone()['n']),'complete':True}
