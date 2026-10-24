"""One-time, version-bound approvals for future administrator applications.

No MCP tool can issue owner approval. A trusted owner-facing route may call
``approve`` or grant one exact reviewed publication; backend applicators consume
the corresponding receipt in the same transaction as the business mutation.
"""
import uuid
from tenant_ai_agents_store import _audit, _authorize
from tenant_ai_administrator_drafts import DRAFT_SCOPE
from tenant_linear_policy import TenantLinearError


def _owner(cur, profile):
    """Approval is a company-owner decision, never an MCP or admin decision."""
    tenant, actor = _authorize(cur, profile)
    cur.execute('''SELECT role,active FROM saas_tenant_members
      WHERE tenant_id=%s::uuid AND telegram_id=%s FOR SHARE''', (tenant, actor))
    member = cur.fetchone()
    if not member or member['role'] != 'owner' or member.get('active') is False:
        raise TenantLinearError('Only the company owner can approve administrator changes.', 403)
    return tenant, actor


def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_administrator_approvals(
          id UUID PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
          draft_id UUID NOT NULL, draft_version INTEGER NOT NULL, action TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT 'pending', approved_by BIGINT NULL, approved_at TIMESTAMPTZ NULL,
          expires_at TIMESTAMPTZ NOT NULL, consumed_at TIMESTAMPTZ NULL, consumed_by TEXT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CHECK(action='apply_draft'), CHECK(state IN ('pending','approved','rejected','consumed')),
          UNIQUE(tenant_id,draft_id,draft_version,action))''')
        _publication_schema(cur)


def definitions():
    return [
      {'name':'administrator_approval_request','description':'Request a one-time owner review for one exact non-public draft version. This never approves or applies a change.',
       'inputSchema':{'type':'object','properties':{'draft_id':{'type':'string'},'version':{'type':'integer','minimum':1}},'required':['draft_id','version'],'additionalProperties':False},
       'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False},'securitySchemes':[{'type':'oauth2','scopes':[DRAFT_SCOPE]}]},
      {'name':'administrator_approval_list','description':'List owner-review receipts for this company. It never applies a change.',
       'inputSchema':{'type':'object','properties':{'limit':{'type':'integer','minimum':1,'maximum':100}},'additionalProperties':False},
       'annotations':{'readOnlyHint':True,'destructiveHint':False,'openWorldHint':False},'securitySchemes':[{'type':'oauth2','scopes':[DRAFT_SCOPE]}]},
    ]


def request(cur, profile, draft_id, version, *, ttl_minutes=30):
    if type(version) is not int or not 1 <= ttl_minutes <= 1440: raise TenantLinearError('Invalid approval request.',400)
    tenant,actor=_owner(cur,profile)
    try: draft_id=str(uuid.UUID(str(draft_id)))
    except (ValueError,TypeError): raise TenantLinearError('Invalid approval request.',400) from None
    cur.execute('''SELECT version,state FROM saas_ai_administrator_drafts WHERE tenant_id=%s::uuid AND id=%s::uuid FOR SHARE''',(tenant,draft_id)); draft=cur.fetchone()
    if not draft or draft['state']!='draft' or draft['version']!=version: raise TenantLinearError('Draft version is not available for approval.',409)
    cur.execute('''INSERT INTO saas_ai_administrator_approvals(id,tenant_id,draft_id,draft_version,action,expires_at)
      VALUES(%s,%s::uuid,%s::uuid,%s,'apply_draft',now()+(%s||' minutes')::interval)
      ON CONFLICT(tenant_id,draft_id,draft_version,action) DO UPDATE SET expires_at=EXCLUDED.expires_at,state=CASE WHEN saas_ai_administrator_approvals.state IN ('rejected','consumed') THEN 'pending' ELSE saas_ai_administrator_approvals.state END
      RETURNING id,state,expires_at''',(uuid.uuid4(),tenant,draft_id,version,ttl_minutes)); row=dict(cur.fetchone())
    _audit(cur,tenant,actor,'ai_administrator_approval_requested','ai_administrator_draft',draft_id,{'version':version,'approval_id':str(row['id'])})
    return {'id':str(row['id']),'state':row['state'],'expires_at':row['expires_at'].isoformat()}


def approve(cur, profile, approval_id, decision):
    if decision not in {'approve','reject'}: raise TenantLinearError('Invalid approval decision.',400)
    tenant,actor=_owner(cur,profile)
    try: approval_id=str(uuid.UUID(str(approval_id)))
    except (ValueError,TypeError): raise TenantLinearError('Invalid approval.',400) from None
    cur.execute('SELECT * FROM saas_ai_administrator_approvals WHERE tenant_id=%s::uuid AND id=%s::uuid AND expires_at>now() FOR UPDATE',(tenant,approval_id)); row=cur.fetchone()
    if not row or row['state']!='pending': raise TenantLinearError('Approval is unavailable or expired.',409)
    state='approved' if decision=='approve' else 'rejected'
    cur.execute('UPDATE saas_ai_administrator_approvals SET state=%s,approved_by=%s,approved_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid',(state,actor,tenant,approval_id))
    _audit(cur,tenant,actor,'ai_administrator_approval_'+state,'ai_administrator_approval',approval_id,{})
    return {'id':approval_id,'state':state}


def approve_many(cur, profile, approval_ids):
    """Atomically approve a bounded review batch owned by one tenant."""
    if not isinstance(approval_ids,list) or not 1 <= len(approval_ids) <= 25:
        raise TenantLinearError('Invalid approval batch.',400)
    try:
        ids=sorted({str(uuid.UUID(str(value))) for value in approval_ids})
    except (ValueError,TypeError):
        raise TenantLinearError('Invalid approval batch.',400) from None
    if len(ids)!=len(approval_ids): raise TenantLinearError('Invalid approval batch.',400)
    tenant,actor=_owner(cur,profile)
    cur.execute('''SELECT id,state FROM saas_ai_administrator_approvals
      WHERE tenant_id=%s::uuid AND id=ANY(%s::uuid[]) AND expires_at>now()
      ORDER BY id FOR UPDATE''',(tenant,ids))
    rows=cur.fetchall()
    if len(rows)!=len(ids) or any(row['state']!='pending' for row in rows):
        raise TenantLinearError('Every approval in the batch must still be pending.',409)
    cur.execute('''UPDATE saas_ai_administrator_approvals SET state='approved',approved_by=%s,approved_at=now()
      WHERE tenant_id=%s::uuid AND id=ANY(%s::uuid[])''',(actor,tenant,ids))
    _audit(cur,tenant,actor,'ai_administrator_approval_batch_approved','ai_administrator_approval_batch',','.join(ids),{'count':len(ids)})
    return {'ids':ids,'state':'approved','count':len(ids)}


def listing(cur, profile, *, limit=50):
    """Owner-only approval queue; draft bodies stay tenant-local."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise TenantLinearError('Invalid approval list.',400)
    tenant,_ = _owner(cur,profile)
    cur.execute('''SELECT a.id,a.draft_id,a.draft_version,a.state,a.expires_at,a.created_at,
                         d.kind,d.title,d.body,d.target,d.version,d.state AS draft_state
      FROM saas_ai_administrator_approvals a
      JOIN saas_ai_administrator_drafts d ON d.id=a.draft_id AND d.tenant_id=a.tenant_id
      WHERE a.tenant_id=%s::uuid ORDER BY a.created_at DESC,a.id DESC LIMIT %s''',(tenant,limit))
    rows=[]
    for row in cur.fetchall():
        value=dict(row)
        for key in ('id','draft_id'):
            value[key]=str(value[key])
        for key in ('expires_at','created_at'):
            value[key]=value[key].isoformat()
        rows.append(value)
    return {'items':rows}


def consume(cur, tenant_id, approval_id, draft_id, version, consumer):
    """Atomically single-use an approved receipt; caller applies the draft next."""
    cur.execute('''UPDATE saas_ai_administrator_approvals SET state='consumed',consumed_at=now(),consumed_by=%s
      WHERE tenant_id=%s::uuid AND id=%s::uuid AND draft_id=%s::uuid AND draft_version=%s
        AND action='apply_draft' AND state='approved' AND expires_at>now() AND consumed_at IS NULL RETURNING id''',(str(consumer)[:120],tenant_id,approval_id,draft_id,version))
    if not cur.fetchone(): raise TenantLinearError('Approval is not valid for this exact draft version.',409)

# Publication is a separate, explicit owner capability, not permission to
# approve arbitrary future drafts. OAuth consent alone never creates a grant.
PUBLISH_SCOPE = 'ai_learning.publish'


def _publication_schema(cur):
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_administrator_publication_grants(
      id UUID PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES saas_tenants(id),
      owner_id BIGINT NOT NULL, draft_id UUID NOT NULL, draft_version INTEGER NOT NULL,
      kind TEXT NOT NULL CHECK(kind IN ('knowledge','instruction','support_case')),
      object_id UUID NOT NULL, agent_id UUID NOT NULL, channel_id UUID NOT NULL,
      before_snapshot JSONB NOT NULL, applied_snapshot JSONB NULL,
      result JSONB NULL, state TEXT NOT NULL DEFAULT 'granted'
        CHECK(state IN ('granted','consumed','revoked','rolled_back')),
      expires_at TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS saas_ai_publication_one_live_material ON saas_ai_administrator_publication_grants(tenant_id,kind,object_id) WHERE state IN ('granted','consumed')")


def _publication_object(cur,tenant,kind,key,*,lock=True):
    tables={'instruction':'saas_instruction_lessons','knowledge':'saas_ai_knowledge_sources',
            'support_case':'saas_knowledge_candidates'}
    # Identifiers only come from this server allowlist.
    cur.execute('SELECT * FROM '+tables[kind]+' WHERE tenant_id=%s::uuid AND id=%s::uuid'+(' FOR UPDATE' if lock else ''),(tenant,key))
    row=cur.fetchone()
    if not row: raise TenantLinearError('Publication material unavailable.',404)
    return dict(row)


def _snapshot(row):
    import json
    # Stable exact snapshot includes versions, provenance and timestamps. No secrets.
    return json.loads(json.dumps(row,default=str,sort_keys=True))


def publication_grant(cur,profile,data):
    """Owner session only. Never expose this function as an MCP tool."""
    from tenant_ai_agents_store import _json
    tenant,actor=_owner(cur,profile)
    required={'draft_id','version','kind','object_id','agent_id','channel_id','ttl_minutes','review_confirmed','material_hash'}
    if set(data)!=required or data.get('review_confirmed') is not True or data.get('kind') not in {'instruction','knowledge','support_case'} or type(data.get('version')) is not int or type(data.get('ttl_minutes')) is not int or not 1<=data['ttl_minutes']<=1440:
        raise TenantLinearError('Explicit owner review of one exact material is required.',400)
    try: ids={k:str(uuid.UUID(str(data[k]))) for k in ('draft_id','object_id','agent_id','channel_id')}
    except (ValueError,TypeError): raise TenantLinearError('Invalid publication identifiers.',400) from None
    # Lock the draft before its material, consistently with execution.
    cur.execute('SELECT * FROM saas_ai_administrator_drafts WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,ids['draft_id']));draft=cur.fetchone()
    if not draft or draft['kind']!=data['kind'] or draft['version']!=data['version'] or draft['state']=='rolled_back':
        raise TenantLinearError('Draft changed; review again.',409)
    cur.execute('SELECT scopes FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL FOR SHARE',(tenant,ids['agent_id']));agent=cur.fetchone()
    cur.execute('SELECT connect_channel_id FROM saas_connect_channels WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid FOR SHARE',(tenant,ids['channel_id']))
    if not agent or not cur.fetchone(): raise TenantLinearError('Publication scope unavailable.',403)
    if not _publication_agent_scope(agent['scopes'],ids['channel_id']): raise TenantLinearError('Publication requires an agent confined to the delegated channel.',403)
    kind=data['kind'];row=_publication_object(cur,tenant,kind,ids['object_id'])
    if data.get('material_hash')!=_publication_hash(row): raise TenantLinearError('Material changed since owner review.',409)
    if kind in {'knowledge','instruction'}:
        cur.execute('''SELECT id FROM saas_ai_administrator_material_rollbacks WHERE tenant_id=%s::uuid
          AND draft_id=%s::uuid AND object_id=%s::uuid AND kind=%s AND rolled_back_at IS NULL''',(tenant,ids['draft_id'],ids['object_id'],kind))
        if not cur.fetchone(): raise TenantLinearError('Material is not the applied draft.',409)
        if kind=='instruction' and row['state'] not in {'draft','pending_review','approved'}: raise TenantLinearError('Instruction is not awaiting publication.',409)
        if kind=='knowledge' and (row['active'] or row['agent_id'] is not None or row['provenance'].get('review_only') is not True): raise TenantLinearError('Knowledge is not an inactive reviewed proposal.',409)
    else:
        evidence=set(str(v) for v in draft['body'].get('evidence_message_ids',[]))
        if row['status'] not in {'pending_review','needs_clarification'} or not evidence or not evidence<=set(row['content'].get('source_message_ids',[])):
            raise TenantLinearError('Case evidence does not match the draft.',409)
        # Historical outcomes still have to pass the existing business evidence guard.
        import tenant_knowledge_candidates_store as cases
        run=cases.extraction_row(cur,tenant,row['extraction_id'])
        if str(run['route'].get('channel_id'))!=ids['channel_id']: raise TenantLinearError('Case outside delegated channel.',403)
        items=cases.evidence(cur,profile,run)
        value=cases.enforce_confirmation(cases.card(row['content'],[i['id'] for i in items],allow_boundary_outside_sources=True),items)
        reason=cases.approval_reason(value)
        if reason: raise TenantLinearError(reason,409)
    cur.execute("SELECT * FROM saas_ai_administrator_publication_grants WHERE tenant_id=%s::uuid AND kind=%s AND object_id=%s::uuid AND state IN ('granted','consumed')",(tenant,kind,ids['object_id']))
    old=cur.fetchone()
    if old:
        if old['owner_id']==actor and str(old['draft_id'])==ids['draft_id'] and old['draft_version']==data['version'] and str(old['agent_id'])==ids['agent_id'] and str(old['channel_id'])==ids['channel_id'] and old['before_snapshot']==_snapshot(row):
            return {'grant_id':str(old['id']),'state':old['state'],'kind':kind,'object_id':ids['object_id']}
        raise TenantLinearError('Material already has a publication permission; revoke or roll back first.',409)
    key=str(uuid.uuid4())
    cur.execute('''INSERT INTO saas_ai_administrator_publication_grants
      (id,tenant_id,owner_id,draft_id,draft_version,kind,object_id,agent_id,channel_id,before_snapshot,expires_at)
      VALUES(%s::uuid,%s::uuid,%s,%s::uuid,%s,%s,%s::uuid,%s::uuid,%s::uuid,%s::jsonb,now()+(%s||' minutes')::interval)''',
      (key,tenant,actor,ids['draft_id'],data['version'],kind,ids['object_id'],ids['agent_id'],ids['channel_id'],_json(_snapshot(row)),data['ttl_minutes']))
    _audit(cur,tenant,actor,'ai_administrator_publication_delegated','ai_administrator_publication',key,{'kind':kind,**ids})
    return {'grant_id':key,'state':'granted','kind':kind,'object_id':ids['object_id']}


def publication_list(cur,profile):
    tenant,_=_owner(cur,profile)
    cur.execute('''SELECT id,kind,object_id,draft_id,draft_version,agent_id,channel_id,state,expires_at
      FROM saas_ai_administrator_publication_grants WHERE tenant_id=%s::uuid ORDER BY created_at DESC LIMIT 100''',(tenant,))
    return {'items':[_snapshot(dict(row)) for row in cur.fetchall()]}


def publication_revoke(cur,profile,grant_id):
    tenant,actor=_owner(cur,profile)
    try: grant_id=str(uuid.UUID(str(grant_id)))
    except (ValueError,TypeError): raise TenantLinearError('Invalid publication grant.',400) from None
    cur.execute("UPDATE saas_ai_administrator_publication_grants SET state='revoked' WHERE tenant_id=%s::uuid AND id=%s::uuid AND state='granted' RETURNING id",(tenant,grant_id))
    if not cur.fetchone(): raise TenantLinearError('Grant cannot be revoked; use rollback for a publication.',409)
    _audit(cur,tenant,actor,'ai_administrator_publication_revoked','ai_administrator_publication',grant_id,{})
    return {'grant_id':str(grant_id),'state':'revoked'}


def publication_execute(cur,cfg,grant_id,*,rollback=False):
    import os
    from tenant_ai_agents_store import _json,save_knowledge
    if PUBLISH_SCOPE not in set(cfg.get('granted_scopes') or []): raise TenantLinearError('Additional OAuth scope required.',403)
    if str(os.environ.get('AI_AUTONOMOUS_APPLY_ENABLED','')).lower()!='true': raise TenantLinearError('Autonomous application is disabled.',403)
    profile={'tenant_id':cfg['AI_LEARNING_MCP_TENANT_ID'],'telegram_id':cfg['actor_id']}
    tenant,actor=_owner(cur,profile)
    try: grant_id=str(uuid.UUID(str(grant_id)))
    except (ValueError,TypeError): raise TenantLinearError('Invalid publication grant.',400) from None
    cur.execute('SELECT *,(expires_at>now()) AS live FROM saas_ai_administrator_publication_grants WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,grant_id));g=cur.fetchone()
    if not g or g['owner_id']!=actor or str(g['agent_id'])!=cfg['AI_LEARNING_MCP_AGENT_ID'] or str(g['channel_id'])!=cfg['AI_LEARNING_MCP_CHANNEL_ID']:
        raise TenantLinearError('Publication outside delegated OAuth scope.',403)
    if rollback and g['state']=='rolled_back': return {'grant_id':grant_id,'rolled_back':True}
    if not rollback and g['state']=='consumed': return g['result']
    if g['state']!=('consumed' if rollback else 'granted') or (not rollback and not g['live']): raise TenantLinearError('Publication grant is unavailable or expired.',409)
    cur.execute('SELECT * FROM saas_ai_administrator_drafts WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,g['draft_id']));draft=cur.fetchone()
    if not draft or draft['version']!=g['draft_version'] or draft['state']=='rolled_back': raise TenantLinearError('Draft changed; review again.',409)
    row=_publication_object(cur,tenant,g['kind'],str(g['object_id']))
    expected=g['applied_snapshot'] if rollback else g['before_snapshot']
    if _snapshot(row)!=(expected['object'] if rollback else expected):
        raise TenantLinearError('Material changed; review again.',409)
    # Validate route again; a changed agent cannot expand this grant.
    cur.execute('SELECT scopes FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL FOR SHARE',(tenant,g['agent_id']));agent=cur.fetchone()
    if not rollback and (not agent or not _publication_agent_scope(agent['scopes'],str(g['channel_id']))): raise TenantLinearError('Agent scope changed.',403)
    if rollback:
        sources=expected['sources']
        provenance_key={'instruction':'instruction_id','support_case':'candidate_id','knowledge':'publication_grant_id'}[g['kind']]
        provenance_value=grant_id if g['kind']=='knowledge' else str(g['object_id'])
        cur.execute("SELECT id FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid AND provenance->>%s=%s ORDER BY id FOR UPDATE",(tenant,provenance_key,provenance_value))
        if [str(v['id']) for v in cur.fetchall()]!=[v['id'] for v in sources]: raise TenantLinearError('Publication dependencies changed; review rollback.',409)
        for old in sources:
            current=_publication_object(cur,tenant,'knowledge',old['id'])
            if _snapshot(current)!=old: raise TenantLinearError('Published knowledge changed; review rollback.',409)
        if g['kind']=='instruction':
            import tenant_instruction_library as lib
            lib.operation(profile,'unpublish',{'expected_version':row['version']},str(g['object_id']),connection=cur.connection)
        elif g['kind']=='support_case':
            import tenant_knowledge_candidates_store as cases
            cases.mutate(profile,str(g['object_id']),{'expected_version':row['version']},'archive',connection=cur.connection)
        else:
            for old in sources:
                cur.execute('UPDATE saas_ai_knowledge_sources SET active=FALSE,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,old['id']))
        cur.execute("UPDATE saas_ai_administrator_publication_grants SET state='rolled_back' WHERE id=%s::uuid",(grant_id,))
        _audit(cur,tenant,actor,'ai_administrator_publication_rolled_back','ai_administrator_publication',grant_id,{})
        return {'grant_id':grant_id,'rolled_back':True}
    kind=g['kind'];key=str(g['object_id']);agent_id=str(g['agent_id'])
    if kind=='instruction':
        import tenant_instruction_library as lib
        if row['state']=='draft': row=lib.operation(profile,'submit',{'expected_version':row['version']},key,connection=cur.connection)
        if row['state']=='pending_review': row=lib.operation(profile,'approve',{'expected_version':row['version'],'review_confirmed':True},key,connection=cur.connection)
        lib.operation(profile,'publish',{'expected_version':row['version'],'agent_ids':[agent_id]},key,connection=cur.connection)
        predicate="provenance->>'instruction_id'=%s"
    elif kind=='support_case':
        import tenant_knowledge_candidates_store as cases
        cases.mutate(profile,key,{'expected_version':row['version'],'agent_ids':[agent_id]},'approve',connection=cur.connection)
        predicate="provenance->>'candidate_id'=%s"
    else:
        source=save_knowledge(profile,{'title':row['title'],'kind':row['kind'],'content':row['content'],'active':True,'agent_id':agent_id},connection=cur.connection)
        provenance={**row['provenance'],'review_only':False,'review_status':'owner_verified_knowledge','publication_grant_id':grant_id,'approved_by':actor}
        # This attests to reviewed knowledge, never a confirmed historical outcome.
        cur.execute('UPDATE saas_ai_knowledge_sources SET provenance=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid',(_json(provenance),tenant,source['id']))
        predicate="provenance->>'publication_grant_id'=%s";key=grant_id
    cur.execute('SELECT * FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid AND '+predicate+' ORDER BY id FOR UPDATE',(tenant,key))
    sources=[_snapshot(dict(v)) for v in cur.fetchall()]
    result={'grant_id':grant_id,'kind':kind,'object_id':str(g['object_id']),'source_ids':[v['id'] for v in sources],'published':True,'public_link_created':False}
    after={'object':_snapshot(_publication_object(cur,tenant,kind,str(g['object_id']))),'sources':sources}
    cur.execute("UPDATE saas_ai_administrator_publication_grants SET state='consumed',result=%s::jsonb,applied_snapshot=%s::jsonb WHERE id=%s::uuid",(_json(result),_json(after),grant_id))
    _audit(cur,tenant,actor,'ai_administrator_material_published','ai_administrator_publication',grant_id,{'kind':kind,'source_ids':result['source_ids']})
    return result


def publication_definitions():
    return [{'name':'administrator_publication_grants','description':'List exact owner-issued publication permissions for the OAuth agent and channel. Cannot issue permissions.',
      'inputSchema':{'type':'object','properties':{},'additionalProperties':False},'annotations':{'readOnlyHint':True},
      'securitySchemes':[{'type':'oauth2','scopes':[PUBLISH_SCOPE]}]}] + [{'name':name,'description':description,
      'inputSchema':{'type':'object','properties':{'grant_id':{'type':'string','format':'uuid'}},'required':['grant_id'],'additionalProperties':False},
      'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False},
      'securitySchemes':[{'type':'oauth2','scopes':[PUBLISH_SCOPE]}]}
      for name,description in (
        ('administrator_publish_reviewed','Publish one exact owner-reviewed material under an unexpired, revocable, one-use owner grant. Cannot grant or self-approve. No public share link.'),
        ('administrator_rollback_publication','Withdraw exactly one delegated publication if its material and all published sources are unchanged. Preserves history.'))]


def _publication_hash(row):
    import hashlib,json
    return hashlib.sha256(json.dumps(_snapshot(row),sort_keys=True,separators=(',',':')).encode()).hexdigest()


def publication_options(cur,profile,draft_id):
    """Existing owner panel: display the exact material before granting permission."""
    tenant,_=_owner(cur,profile)
    try: draft_id=str(uuid.UUID(str(draft_id)))
    except (ValueError,TypeError): raise TenantLinearError('Invalid draft.',400) from None
    cur.execute('SELECT * FROM saas_ai_administrator_drafts WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,draft_id));draft=cur.fetchone()
    if not draft or draft['kind'] not in {'instruction','knowledge','support_case'}: raise TenantLinearError('Material draft unavailable.',404)
    if draft['kind']=='support_case':
        cur.execute("SELECT id FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND status IN ('pending_review','needs_clarification') ORDER BY updated_at DESC LIMIT 100",(tenant,))
        ids=[str(v['id']) for v in cur.fetchall()]
        evidence=set(draft['body'].get('evidence_message_ids',[]))
        objects=[_publication_object(cur,tenant,draft['kind'],key,lock=False) for key in ids]
        objects=[v for v in objects if evidence and evidence<=set(v['content'].get('source_message_ids',[]))]
    else:
        cur.execute('SELECT object_id FROM saas_ai_administrator_material_rollbacks WHERE tenant_id=%s::uuid AND draft_id=%s::uuid AND kind=%s AND rolled_back_at IS NULL',(tenant,draft_id,draft['kind']))
        ids=[str(v['object_id']) for v in cur.fetchall()]
        objects=[_publication_object(cur,tenant,draft['kind'],key,lock=False) for key in ids]
    cur.execute('SELECT id,name FROM saas_ai_agents WHERE tenant_id=%s::uuid AND archived_at IS NULL ORDER BY name LIMIT 100',(tenant,));agents=[_snapshot(dict(v)) for v in cur.fetchall()]
    cur.execute('SELECT connect_channel_id AS id,name FROM saas_connect_channels WHERE tenant_id=%s::uuid ORDER BY name LIMIT 100',(tenant,));channels=[_snapshot(dict(v)) for v in cur.fetchall()]
    return {'draft_id':draft_id,'version':draft['version'],'kind':draft['kind'],
      'materials':[{'id':str(v['id']),'material_hash':_publication_hash(v),'preview':_snapshot(v)} for v in objects],
      'agents':agents,'channels':channels}


def _publication_agent_scope(scopes,channel):
    # Existing Knowledge Sources bind to an agent, not a channel. Do not let
    # this new permission publish into an agent covering unrelated channels.
    from tenant_ai_agent_connect import scope_matches
    return (isinstance(scopes,list) and bool(scopes)
      and all(v.get('provider')=='connect' and str(v.get('channel_id'))==channel for v in scopes)
      and scope_matches(scopes,channel,is_group=False))


def publication_assert_detached(cur,tenant,draft_id):
    # Prevent the older material-draft rollback from orphaning an active
    # publication or invalidating a still outstanding owner permission.
    cur.execute('SELECT id FROM saas_ai_administrator_drafts WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,draft_id))
    cur.execute("SELECT id FROM saas_ai_administrator_publication_grants WHERE tenant_id=%s::uuid AND draft_id=%s::uuid AND state IN ('granted','consumed') LIMIT 1",(tenant,draft_id))
    if cur.fetchone(): raise TenantLinearError('Revoke or withdraw the delegated publication before rolling back its draft.',409)
