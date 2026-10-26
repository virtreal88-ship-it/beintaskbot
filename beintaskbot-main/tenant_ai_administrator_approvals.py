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
        cur.execute("ALTER TABLE saas_ai_administrator_approvals ADD COLUMN IF NOT EXISTS delegation_policy_id UUID NULL")
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
    # The MCP-app capability table is optional during a rolling deployment.
    # Once present, every web decision invalidates all per-connection widgets
    # for the exact draft version before the receipt can be used elsewhere.
    cur.execute("SELECT to_regclass('saas_ai_owner_review_capabilities') AS relation")
    if cur.fetchone()['relation']:
        cur.execute('''UPDATE saas_ai_owner_review_capabilities SET consumed_at=now()
          WHERE tenant_id=%s::uuid AND draft_id=%s::uuid AND draft_version=%s AND consumed_at IS NULL''',
          (tenant,row['draft_id'],row['draft_version']))
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
    cur.execute('''SELECT id,state,draft_id,draft_version FROM saas_ai_administrator_approvals
      WHERE tenant_id=%s::uuid AND id=ANY(%s::uuid[]) AND expires_at>now()
      ORDER BY id FOR UPDATE''',(tenant,ids))
    rows=cur.fetchall()
    if len(rows)!=len(ids) or any(row['state']!='pending' for row in rows):
        raise TenantLinearError('Every approval in the batch must still be pending.',409)
    cur.execute('''UPDATE saas_ai_administrator_approvals SET state='approved',approved_by=%s,approved_at=now()
      WHERE tenant_id=%s::uuid AND id=ANY(%s::uuid[])''',(actor,tenant,ids))
    cur.execute("SELECT to_regclass('saas_ai_owner_review_capabilities') AS relation")
    if cur.fetchone()['relation']:
        for row in rows:
            cur.execute('''UPDATE saas_ai_owner_review_capabilities SET consumed_at=now()
              WHERE tenant_id=%s::uuid AND draft_id=%s::uuid AND draft_version=%s AND consumed_at IS NULL''',
              (tenant,row['draft_id'],row['draft_version']))
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
    _training_policy_schema(cur)
    cur.execute("ALTER TABLE saas_ai_administrator_publication_grants ADD COLUMN IF NOT EXISTS policy_id UUID NULL")
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


def publication_grant(cur,profile,data,*,policy=None):
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
        delegated_owner_knowledge=(kind=='knowledge' and policy is not None and policy.get('scope_kind')=='company'
          and (row.get('provenance') or {}).get('review_status') in {'owner_delegated_owner_provided','owner_delegated_evidence_draft'})
        if delegated_owner_knowledge:
            _,basis=_owner_draft_basis(cur,profile,ids['draft_id'],data['version'],(row.get('provenance') or {}).get('delegated_draft_hash'),policy)
            if basis!=row['provenance'].get('delegated_draft_basis'):raise TenantLinearError('Draft source evidence changed.',409)
            if row.get('provenance',{}).get('delegated_draft_id')!=ids['draft_id']:
                raise TenantLinearError('Delegated draft source provenance mismatch.',409)
        else:
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
        if policy is not None and str(old.get('policy_id'))!=str(policy['id']): raise TenantLinearError('Material already has a different publication authorization.',409)
        if old['owner_id']==actor and str(old['draft_id'])==ids['draft_id'] and old['draft_version']==data['version'] and str(old['agent_id'])==ids['agent_id'] and str(old['channel_id'])==ids['channel_id'] and old['before_snapshot']==_snapshot(row):
            return {'grant_id':str(old['id']),'state':old['state'],'kind':kind,'object_id':ids['object_id']}
        raise TenantLinearError('Material already has a publication permission; revoke or roll back first.',409)
    if policy is not None and (str(policy['tenant_id'])!=tenant or policy['owner_id']!=actor or str(policy['agent_id'])!=ids['agent_id'] or str(policy['channel_id'])!=ids['channel_id'] or kind not in policy['kinds']): raise TenantLinearError('Delegation policy mismatch.',403)
    key=str(uuid.uuid4())
    cur.execute('''INSERT INTO saas_ai_administrator_publication_grants
      (id,tenant_id,owner_id,draft_id,draft_version,kind,object_id,agent_id,channel_id,before_snapshot,expires_at)
      VALUES(%s::uuid,%s::uuid,%s,%s::uuid,%s,%s,%s::uuid,%s::uuid,%s::uuid,%s::jsonb,now()+(%s||' minutes')::interval)''',
      (key,tenant,actor,ids['draft_id'],data['version'],kind,ids['object_id'],ids['agent_id'],ids['channel_id'],_json(_snapshot(row)),data['ttl_minutes']))
    if policy is not None:
        cur.execute('UPDATE saas_ai_administrator_publication_grants SET policy_id=%s::uuid,expires_at=LEAST(expires_at,%s::timestamptz) WHERE id=%s::uuid',(policy['id'],policy['expires_at'],key))
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
    profile={'tenant_id':cfg['AI_LEARNING_MCP_TENANT_ID'],'telegram_id':cfg['actor_id']}
    tenant,actor=_owner(cur,profile)
    try: grant_id=str(uuid.UUID(str(grant_id)))
    except (ValueError,TypeError): raise TenantLinearError('Invalid publication grant.',400) from None
    cur.execute('''SELECT g.policy_id,g.agent_id,g.channel_id,p.scope_kind FROM saas_ai_administrator_publication_grants g
      LEFT JOIN saas_ai_training_policies p ON p.tenant_id=g.tenant_id AND p.id=g.policy_id
      WHERE g.tenant_id=%s::uuid AND g.id=%s::uuid''',(tenant,grant_id));policy_ref=cur.fetchone()
    if policy_ref and policy_ref.get('policy_id') is not None:
        training_policy_guard(cur,cfg,str(policy_ref['policy_id']),'rollback' if rollback else 'publish',
          str(policy_ref['agent_id']) if policy_ref.get('scope_kind')=='company' else None,
          str(policy_ref['channel_id']) if policy_ref.get('scope_kind')=='company' else None,grant_id if rollback else None)
    # A bounded training policy is a separate, tenant-scoped owner consent.
    # Keep the global switch as a kill switch for the older general apply flow,
    # but never require an operator to enable that broad flow to execute an
    # exact policy-linked publication.
    elif str(os.environ.get('AI_AUTONOMOUS_APPLY_ENABLED','')).lower()!='true':
        raise TenantLinearError('Autonomous application is disabled.',403)
    cur.execute('SELECT *,(expires_at>now()) AS live FROM saas_ai_administrator_publication_grants WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,grant_id));g=cur.fetchone()
    company_policy=bool(policy_ref and policy_ref.get('scope_kind')=='company')
    if not g or g['owner_id']!=actor or (not company_policy and (str(g['agent_id'])!=cfg['AI_LEARNING_MCP_AGENT_ID'] or str(g['channel_id'])!=cfg['AI_LEARNING_MCP_CHANNEL_ID'])):
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
    delegated_prepared=(_training_prepared(cur,{'id':g['policy_id']},str(g['draft_id']),str(g['object_id']))
      if company_policy else None)
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
        if row['state']=='pending_review':
            if company_policy:
                basis=delegated_prepared['basis']
                row=lib.operation(profile,'delegate_approve',{'expected_version':row['version'],'delegation_policy_id':str(g['policy_id']),
                  'source_id':basis['id'],'source_hash':basis['hash'],'agent_id':agent_id,'channel_id':str(g['channel_id'])},key,connection=cur.connection)
            else:row=lib.operation(profile,'approve',{'expected_version':row['version'],'review_confirmed':True},key,connection=cur.connection)
        publish_data={'expected_version':row['version'],'agent_ids':[agent_id]}
        if company_policy:
            basis=delegated_prepared['basis'];publish_data.update({'delegation_policy_id':str(g['policy_id']),'source_id':basis['id'],'source_hash':basis['hash'],'agent_id':agent_id,'channel_id':str(g['channel_id'])})
        lib.operation(profile,'publish',publish_data,key,connection=cur.connection)
        predicate="provenance->>'instruction_id'=%s"
    elif kind=='support_case':
        import tenant_knowledge_candidates_store as cases
        case_action='delegated_approve' if company_policy else 'approve'
        case_data={'expected_version':row['version'],'agent_ids':[agent_id]}
        if company_policy:case_data.update({'delegation_policy_id':str(g['policy_id']),'channel_id':str(g['channel_id'])})
        cases.mutate(profile,key,case_data,case_action,connection=cur.connection)
        predicate="provenance->>'candidate_id'=%s"
    else:
        source=save_knowledge(profile,{'title':row['title'],'kind':row['kind'],'content':row['content'],'active':True,'agent_id':agent_id},connection=cur.connection)
        owner_draft_source=(row.get('provenance') or {}).get('review_status') in {'owner_delegated_owner_provided','owner_delegated_evidence_draft'}
        provenance={**row['provenance'],'review_only':False,'review_status':(row['provenance']['review_status'] if owner_draft_source else 'policy_verified_knowledge' if g.get('policy_id') is not None else 'owner_verified_knowledge'),'publication_grant_id':grant_id}
        if company_policy:provenance.update({'authorization_mode':'owner_delegated_policy','delegation_policy_id':str(g['policy_id'])})
        elif not owner_draft_source:provenance['approved_by']=actor
        # This attests to reviewed knowledge, never a confirmed historical outcome.
        cur.execute('UPDATE saas_ai_knowledge_sources SET provenance=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid',(_json(provenance),tenant,source['id']))
        predicate="provenance->>'publication_grant_id'=%s";key=grant_id
    cur.execute('SELECT * FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid AND '+predicate+' ORDER BY id FOR UPDATE',(tenant,key))
    sources=[_snapshot(dict(v)) for v in cur.fetchall()]
    if g.get('policy_id') is not None:
        prepared=delegated_prepared or _training_prepared(cur,{'id':g['policy_id']},str(g['draft_id']),str(g['object_id']))
        for source in sources:
            cur.execute("UPDATE saas_ai_knowledge_sources SET provenance=provenance||%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid",(_json({'training_basis':prepared['basis'],'delegation_policy_id':str(g['policy_id'])}),tenant,source['id']))
        cur.execute('SELECT * FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid AND '+predicate+' ORDER BY id FOR UPDATE',(tenant,key));sources=[_snapshot(dict(v)) for v in cur.fetchall()]
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

# Persistent delegation is an owner permission, not a parallel material store.
# Each successful operation stores an immutable idempotency/audit receipt.
def _training_policy_schema(cur):
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_training_policies(
      id UUID PRIMARY KEY,tenant_id UUID NOT NULL REFERENCES saas_tenants(id),owner_id BIGINT NOT NULL,
      agent_id UUID NOT NULL,channel_id UUID NOT NULL,kinds JSONB NOT NULL,rights JSONB NOT NULL,
      max_operations INTEGER NOT NULL CHECK(max_operations BETWEEN 1 AND 1000),
      max_per_hour INTEGER NOT NULL CHECK(max_per_hour BETWEEN 1 AND 100),
      expires_at TIMESTAMPTZ NOT NULL,revoked_at TIMESTAMPTZ NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_ai_training_operations(
      sequence BIGSERIAL UNIQUE,policy_id UUID NOT NULL REFERENCES saas_ai_training_policies(id),request_id UUID NOT NULL,
      action TEXT NOT NULL,request_hash TEXT NOT NULL,result JSONB NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
      PRIMARY KEY(policy_id,request_id))''')
    # Null scope/limits are reserved for a separately owner-confirmed company
    # delegation. Existing rows remain agent/channel-bound and bounded.
    cur.execute("ALTER TABLE saas_ai_training_policies ADD COLUMN IF NOT EXISTS scope_kind TEXT NOT NULL DEFAULT 'agent_channel'")
    for column in ('agent_id','channel_id','max_operations','max_per_hour','expires_at'):
        cur.execute('ALTER TABLE saas_ai_training_policies ALTER COLUMN '+column+' DROP NOT NULL')
    cur.execute("ALTER TABLE saas_ai_training_policies DROP CONSTRAINT IF EXISTS saas_ai_training_policies_scope_kind_check")
    cur.execute("ALTER TABLE saas_ai_training_policies ADD CONSTRAINT saas_ai_training_policies_scope_kind_check CHECK (scope_kind IN ('agent_channel','company'))")
    cur.execute("ALTER TABLE saas_ai_training_policies DROP CONSTRAINT IF EXISTS saas_ai_training_policies_company_bounds_check")
    cur.execute("ALTER TABLE saas_ai_training_policies ADD CONSTRAINT saas_ai_training_policies_company_bounds_check CHECK ((scope_kind='agent_channel' AND agent_id IS NOT NULL AND channel_id IS NOT NULL AND max_operations BETWEEN 1 AND 1000 AND max_per_hour BETWEEN 1 AND 100 AND expires_at IS NOT NULL) OR (scope_kind='company' AND agent_id IS NULL AND channel_id IS NULL AND max_operations IS NULL AND max_per_hour IS NULL AND expires_at IS NULL))")


def training_policy_create(cur,profile,data):
    from tenant_ai_agents_store import _json
    tenant,actor=_owner(cur,profile)
    required={'agent_id','channel_id','kinds','rights','ttl_hours','max_operations','max_per_hour','consent_confirmed'}
    if (set(data)!=required or data.get('consent_confirmed') is not True
        or not isinstance(data.get('kinds'),list) or not data['kinds'] or any(type(v) is not str for v in data['kinds']) or len(data['kinds'])!=len(set(data['kinds'])) or set(data['kinds'])-{'knowledge','instruction','support_case'}
        or not isinstance(data.get('rights'),list) or not data['rights'] or any(type(v) is not str for v in data['rights']) or len(data['rights'])!=len(set(data['rights'])) or set(data['rights'])-{'create','update','publish','rollback'}
        or any(type(data.get(k)) is not int for k in ('ttl_hours','max_operations','max_per_hour'))
        or not 1<=data['ttl_hours']<=720 or not 1<=data['max_operations']<=1000 or not 1<=data['max_per_hour']<=100):
        raise TenantLinearError('Explicit bounded owner delegation is required.',400)
    agent_id=_training_uuid(data['agent_id']);channel_id=_training_uuid(data['channel_id'])
    cur.execute('SELECT scopes FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL FOR SHARE',(tenant,agent_id));agent=cur.fetchone()
    cur.execute('SELECT connect_channel_id FROM saas_connect_channels WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid FOR SHARE',(tenant,channel_id))
    if not agent or not cur.fetchone() or not _publication_agent_scope(agent['scopes'],channel_id):raise TenantLinearError('Delegation scope unavailable.',403)
    key=str(uuid.uuid4())
    cur.execute('''INSERT INTO saas_ai_training_policies(id,tenant_id,owner_id,agent_id,channel_id,kinds,rights,max_operations,max_per_hour,expires_at)
      VALUES(%s::uuid,%s::uuid,%s,%s::uuid,%s::uuid,%s::jsonb,%s::jsonb,%s,%s,clock_timestamp()+(%s||' hours')::interval)''',
      (key,tenant,actor,agent_id,channel_id,_json(data['kinds']),_json(data['rights']),data['max_operations'],data['max_per_hour'],data['ttl_hours']))
    _audit(cur,tenant,actor,'ai_training_policy_granted','ai_training_policy',key,{k:v for k,v in data.items() if k!='consent_confirmed'})
    return {'policy_id':key,'state':'active'}


def training_company_policy_create(cur,profile,data):
    """Explicit owner consent for unbounded company-wide training publication."""
    from tenant_ai_agents_store import _json
    tenant,actor=_owner(cur,profile)
    if not isinstance(data,dict) or set(data)!={'consent_confirmed'} or data.get('consent_confirmed') is not True:
        raise TenantLinearError('Explicit company-wide publication consent is required.',400)
    cur.execute("SELECT id FROM saas_ai_training_policies WHERE tenant_id=%s::uuid AND scope_kind='company' AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at>clock_timestamp()) FOR UPDATE",(tenant,))
    if cur.fetchone():raise TenantLinearError('An active company-wide policy already exists; revoke it before replacing it.',409)
    key=str(uuid.uuid4())
    kinds=['knowledge','instruction','support_case']
    rights=['create','update','publish','rollback']
    cur.execute('''INSERT INTO saas_ai_training_policies
      (id,tenant_id,owner_id,agent_id,channel_id,kinds,rights,max_operations,max_per_hour,expires_at,scope_kind)
      VALUES(%s::uuid,%s::uuid,%s,NULL,NULL,%s::jsonb,%s::jsonb,NULL,NULL,NULL,'company')''',
      (key,tenant,actor,_json(kinds),_json(rights)))
    _audit(cur,tenant,actor,'ai_training_company_policy_granted','ai_training_policy',key,
      {'scope':'company_all_agents','kinds':kinds,'rights':rights,'operation_limits':'unlimited','expires_at':None})
    return {'policy_id':key,'state':'active','scope':'company_all_agents','expires_at':None,
            'max_operations':None,'max_per_hour':None,'kinds':kinds,'rights':rights}


def _training_uuid(value):
    try:return str(uuid.UUID(str(value)))
    except (ValueError,TypeError):raise TenantLinearError('Invalid delegation identifier.',400) from None


def training_policy_revoke(cur,profile,policy_id):
    tenant,actor=_owner(cur,profile);policy_id=_training_uuid(policy_id)
    cur.execute('UPDATE saas_ai_training_policies SET revoked_at=COALESCE(revoked_at,clock_timestamp()) WHERE tenant_id=%s::uuid AND id=%s::uuid RETURNING id',(tenant,policy_id))
    if not cur.fetchone():raise TenantLinearError('Policy unavailable.',404)
    _audit(cur,tenant,actor,'ai_training_policy_revoked','ai_training_policy',policy_id,{})
    return {'policy_id':policy_id,'revoked':True}


def training_policy_list(cur,profile,cfg=None):
    tenant,_=_owner(cur,profile)
    cur.execute('SELECT *,(COALESCE(expires_at>now(),TRUE) AND revoked_at IS NULL) AS active FROM saas_ai_training_policies WHERE tenant_id=%s::uuid ORDER BY created_at DESC LIMIT 100',(tenant,))
    policies=[dict(v) for v in cur.fetchall()]
    if cfg:policies=[v for v in policies if v.get('scope_kind')=='company' or (str(v['agent_id'])==cfg['AI_LEARNING_MCP_AGENT_ID'] and str(v['channel_id'])==cfg['AI_LEARNING_MCP_CHANNEL_ID'])]
    for p in policies:
        cur.execute('SELECT action,result,created_at FROM saas_ai_training_operations WHERE policy_id=%s::uuid ORDER BY created_at DESC LIMIT 100',(p['id'],));p['recent_operations']=[_snapshot(dict(v)) for v in cur.fetchall()]
    # `AI_AUTONOMOUS_APPLY_ENABLED` governs the legacy general application
    # workflow. It is intentionally not a prerequisite for an exact, owner
    # scoped training policy. Keeping both values makes the distinction clear
    # to the owner and avoids accidentally presenting the global kill switch
    # as a tenant-level readiness error.
    from tenant_ai_administrator_apply import enabled
    active=any(v['active'] for v in policies)
    result={'items':[_snapshot(v) for v in policies],
      'training_ready':active,
      'training_status':'ready' if active else 'owner_consent_required',
      'legacy_application_enabled':enabled()}
    if cfg is None:
        cur.execute('SELECT id,name FROM saas_ai_agents WHERE tenant_id=%s::uuid AND archived_at IS NULL ORDER BY name LIMIT 100',(tenant,));result['agents']=[_snapshot(dict(v)) for v in cur.fetchall()]
        cur.execute('SELECT connect_channel_id AS id,name FROM saas_connect_channels WHERE tenant_id=%s::uuid ORDER BY name LIMIT 100',(tenant,));result['channels']=[_snapshot(dict(v)) for v in cur.fetchall()]
    return result


def training_policy_guard(cur,cfg,policy_id,action,target_agent_id=None,target_channel_id=None,grant_id=None):
    if PUBLISH_SCOPE not in set(cfg.get('granted_scopes') or []):raise TenantLinearError('Additional OAuth scope required.',403)
    profile={'tenant_id':cfg['AI_LEARNING_MCP_TENANT_ID'],'telegram_id':cfg['actor_id']};tenant,actor=_owner(cur,profile)
    # All policy mutations serialize here, including revoke and quota checks.
    cur.execute('SELECT *,(COALESCE(expires_at>clock_timestamp(),TRUE)) AS live FROM saas_ai_training_policies WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,_training_uuid(policy_id)));p=cur.fetchone()
    if not p or p['owner_id']!=actor or action not in p['rights']:raise TenantLinearError('Operation outside owner delegation.',403)
    if p.get('scope_kind')=='company':
        # Hold the existing OAuth token row through mutation/commit, serializing
        # with revocation/rotation after the earlier HTTP authentication read.
        cur.execute('''SELECT scope FROM saas_ai_learning_mcp_tokens
          WHERE id=%s::uuid AND oauth_connection_id=%s::uuid AND token_hash=%s
            AND tenant_id=%s::uuid AND created_by=%s AND agent_id=%s::uuid AND channel_id=%s::uuid
            AND revoked_at IS NULL AND expires_at>clock_timestamp() FOR SHARE''',
          (cfg.get('oauth_token_id'),cfg.get('oauth_connection_id'),cfg.get('oauth_token_hash'),tenant,actor,
           cfg['AI_LEARNING_MCP_AGENT_ID'],cfg['AI_LEARNING_MCP_CHANNEL_ID']))
        token=cur.fetchone()
        required={PUBLISH_SCOPE}|({DRAFT_SCOPE} if action in {'create','update'} else set())
        if not token or not required<=set(str(token['scope']).split()):
            raise TenantLinearError('Current OAuth connection is unavailable for delegated publication.',403)
        if action=='rollback' and grant_id:
            cur.execute('SELECT agent_id,channel_id FROM saas_ai_administrator_publication_grants WHERE tenant_id=%s::uuid AND id=%s::uuid AND policy_id=%s::uuid',(tenant,_training_uuid(grant_id),p['id']))
            target=cur.fetchone()
            if not target:raise TenantLinearError('Publication outside policy.',403)
            target_agent_id,target_channel_id=target['agent_id'],target['channel_id']
        try:target_agent_id=str(uuid.UUID(str(target_agent_id)));target_channel_id=str(uuid.UUID(str(target_channel_id)))
        except (TypeError,ValueError):raise TenantLinearError('Explicit target agent and channel are required.',400) from None
        cur.execute('SELECT scopes FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL FOR SHARE',(tenant,target_agent_id));agent=cur.fetchone()
        cur.execute('SELECT 1 FROM saas_connect_channels WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid FOR SHARE',(tenant,target_channel_id));channel=cur.fetchone()
        if action!='rollback' and (not agent or not channel or not _publication_agent_scope(agent['scopes'],target_channel_id)):
            raise TenantLinearError('Target is outside the active company agent/channel scope.',403)
        p=dict(p);p['agent_id']=target_agent_id;p['channel_id']=target_channel_id
    elif (str(p['agent_id'])!=cfg['AI_LEARNING_MCP_AGENT_ID'] or str(p['channel_id'])!=cfg['AI_LEARNING_MCP_CHANNEL_ID']
          or target_agent_id is not None or target_channel_id is not None):raise TenantLinearError('Operation outside owner delegation.',403)
    # Safe withdrawal remains possible after expiry/revocation, never new writes.
    if action!='rollback' and (not p['live'] or p['revoked_at'] is not None):raise TenantLinearError('Delegation expired or revoked.',403)
    if p.get('scope_kind')!='company':
        cur.execute('SELECT scopes FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL FOR SHARE',(tenant,p['agent_id']));agent=cur.fetchone()
        if action!='rollback' and (not agent or not _publication_agent_scope(agent['scopes'],str(p['channel_id']))):raise TenantLinearError('Agent scope changed.',403)
    return dict(p),profile


def _company_target(cur,p,tenant,agent_id,channel_id):
    """Resolve one explicit target under a company policy; never auto-fanout."""
    if p.get('scope_kind')!='company':
        if agent_id is not None or channel_id is not None:
            raise TenantLinearError('Target override is unavailable for this policy.',403)
        return p
    try: agent_id=str(uuid.UUID(str(agent_id)));channel_id=str(uuid.UUID(str(channel_id)))
    except (TypeError,ValueError):raise TenantLinearError('Explicit target agent and channel are required.',400) from None
    cur.execute('SELECT scopes FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid AND archived_at IS NULL FOR SHARE',(tenant,agent_id));agent=cur.fetchone()
    cur.execute('SELECT 1 FROM saas_connect_channels WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid FOR SHARE',(tenant,channel_id));channel=cur.fetchone()
    if not agent or not channel or not _publication_agent_scope(agent['scopes'],channel_id):raise TenantLinearError('Target is outside the active agent/channel scope.',403)
    result=dict(p);result['agent_id']=agent_id;result['channel_id']=channel_id
    return result


def _owner_draft_basis(cur,profile,draft_id,version,digest,policy,*,preview=False):
    """Exact delegated knowledge draft; evidence references do not attest to every claim."""
    from tenant_ai_owner_review_component import _draft_hash
    tenant=str(profile['tenant_id']);actor=profile['telegram_id']
    try:draft_id=str(uuid.UUID(str(draft_id)))
    except (TypeError,ValueError):raise TenantLinearError('Invalid owner draft.',400) from None
    cur.execute('''SELECT * FROM saas_ai_administrator_drafts
      WHERE tenant_id=%s::uuid AND id=%s::uuid FOR SHARE''',(tenant,draft_id));draft=cur.fetchone()
    if (not draft or draft['kind']!='knowledge' or draft['state']!='draft' or type(version) is not int or draft['version']!=version
        or draft['created_by']!=actor
        or policy['owner_id']!=actor or policy.get('scope_kind')!='company'):
        raise TenantLinearError('Current delegated knowledge draft is unavailable for this delegation.',403)
    calculated=_draft_hash(draft)
    body=draft['body'];owner=body.get('owner_provided')
    basis={'kind':'owner_provided_draft' if owner else 'evidence_backed_draft','id':draft_id,'version':version,'hash':calculated}
    if owner:
        if not isinstance(owner,dict) or owner.get('verified_by')!=actor or draft['target'].get('source_eligibility')!='owner_provided_unverified':
            raise TenantLinearError('Owner-provided provenance is invalid.',403)
    else:
        import hashlib,json
        from tenant_ai_administrator_drafts import _message_evidence
        evidence=_message_evidence(cur,tenant,body.get('evidence_ids'))
        cur.execute('''SELECT id,body,channel,kommo_lead_id,direction,message_type,external_id,raw FROM saas_crm_messages
          WHERE tenant_id=%s::uuid AND id=ANY(%s::uuid[]) ORDER BY id FOR SHARE''',(tenant,evidence))
        rows=[dict(v) for v in cur.fetchall()]
        if any(v['channel']!='connect:'+str(policy['channel_id']) for v in rows):
            raise TenantLinearError('Draft evidence outside the selected channel.',403)
        from tenant_media_evidence import publication_fingerprint
        for row in rows:
            row['media_evidence_fingerprint']=publication_fingerprint(cur,tenant,row)
            # Only the existing identity fence and exact receipt digest enter
            # the source hash; raw attachments/URLs never enter tool output.
            row.pop('raw',None);row.pop('external_id',None)
        basis['source_evidence']={'ids':sorted(evidence),'hash':hashlib.sha256(json.dumps(_snapshot(rows),sort_keys=True,separators=(',',':')).encode()).hexdigest()}
        calculated=hashlib.sha256(json.dumps({'draft':calculated,'evidence':basis['source_evidence']},sort_keys=True,separators=(',',':')).encode()).hexdigest()
        basis['hash']=calculated
    if not preview and (not isinstance(digest,str) or digest!=calculated):raise TenantLinearError('Draft or its evidence changed; review its exact hash again.',409)
    return dict(draft),basis


def _training_case(cur,profile,key,channel):
    import tenant_knowledge_candidates_store as cases
    tenant=str(profile['tenant_id']);row=_publication_object(cur,tenant,'support_case',key)
    run=cases.extraction_row(cur,tenant,row['extraction_id'])
    if str(run['route'].get('channel_id'))!=channel:raise TenantLinearError('Case outside delegated channel.',403)
    items=cases.evidence(cur,profile,run)
    value=cases.enforce_confirmation(cases.card(row['content'],[v['id'] for v in items],allow_boundary_outside_sources=True),items)
    reason=cases.approval_reason(value)
    # Unattended publication requires literal evidence for operational facts.
    texts=[v['text'].casefold() for v in items if v['role'] in {'operator','customer'}]
    claims=value.get('resolution_steps',[])+value.get('operator_actions',[])
    if any(not any(claim.casefold() in text for text in texts) for claim in claims):reason=reason or 'resolution_evidence_required'
    if value.get('cause',{}).get('status')=='confirmed' and not any(value['cause']['text'].casefold() in text for text in texts):reason=reason or 'cause_evidence_required'
    return row,reason


def _training_basis(cur,profile,kind,key,policy,seen=None):
    tenant=str(policy['tenant_id']);channel=str(policy['channel_id'])
    seen=set(seen or ())
    if (kind,key) in seen or len(seen)>=16:raise TenantLinearError('Evidence provenance cycle or excessive depth.',409)
    seen.add((kind,key))
    if kind=='support_case':
        row,reason=_training_case(cur,profile,key,channel)
        if reason:raise TenantLinearError('Clarification required: '+reason,409)
    else:
        row=_publication_object(cur,tenant,kind,key)
        if kind=='instruction':
            if row['state'] not in {'approved','published'} or row['doc'].get('review_status')!='approved':raise TenantLinearError('Verified instruction required.',409)
            from tenant_instruction_library import _assets,_video
            _assets(cur,tenant,row['doc']);_video(cur,tenant,row['doc'])
        elif kind=='knowledge':
            provenance=row.get('provenance') or {}
            if not row['active'] or str(row.get('agent_id'))!=str(policy['agent_id']) or not (provenance.get('approved_by') or (provenance.get('authorization_mode')=='owner_delegated_policy' and provenance.get('review_status')=='policy_verified_knowledge' and provenance.get('training_basis') and provenance.get('delegation_policy_id'))) or provenance.get('review_only') is True:
                raise TenantLinearError('Verified agent-bound knowledge required.',409)
            if provenance.get('candidate_id'):_training_basis(cur,profile,'support_case',provenance['candidate_id'],policy,seen)
            elif provenance.get('instruction_id'):_training_basis(cur,profile,'instruction',provenance['instruction_id'],policy,seen)
            elif provenance.get('review_status')!='owner_verified_knowledge' and not (provenance.get('review_status')=='policy_verified_knowledge' and provenance.get('training_basis') and provenance.get('delegation_policy_id')):raise TenantLinearError('Verified provenance required.',409)
    lineage=(row.get('provenance') or {}).get('training_basis')
    if kind=='instruction':
        cur.execute("""SELECT o.result FROM saas_ai_training_operations o JOIN saas_ai_training_policies p ON p.id=o.policy_id
          WHERE p.tenant_id=%s::uuid AND o.action IN ('create','update') AND o.result->>'kind'='instruction' AND o.result->>'object_id'=%s ORDER BY o.sequence DESC LIMIT 1""",(tenant,key));origin=cur.fetchone()
        if origin:lineage=origin['result'].get('basis')
    if lineage:
        _,checked=_training_basis(cur,profile,lineage['kind'],lineage['id'],policy,seen)
        if checked!=lineage:raise TenantLinearError('Upstream verified evidence changed or was withdrawn.',409)
    return row,{'kind':kind,'id':key,'hash':_publication_hash(row)}


def _training_prepared(cur,policy,draft_id,object_id):
    cur.execute('''SELECT result FROM saas_ai_training_operations WHERE policy_id=%s::uuid
      AND action IN ('create','update') AND result->>'draft_id'=%s AND result->>'object_id'=%s ORDER BY sequence DESC LIMIT 1''',(policy['id'],draft_id,object_id))
    row=cur.fetchone()
    if not row:raise TenantLinearError('Material was not prepared under this policy.',403)
    return row['result']


def _training_source(cur,profile,kind,key,policy):
    """Give a safe, actionable error for a same-tenant draft/source mix-up."""
    try:
        return _training_basis(cur,profile,kind,key,policy)
    except TenantLinearError as exc:
        if exc.status != 404:
            raise
        cur.execute('''SELECT kind FROM saas_ai_administrator_drafts
          WHERE tenant_id=%s::uuid AND id=%s::uuid FOR SHARE''',(policy['tenant_id'],key))
        if cur.fetchone():
            raise TenantLinearError('Training source must be a reviewed material, not an administrator draft.',409) from None
        raise


def training_sources(cur,cfg,data):
    """List only reviewed source records eligible for one scoped policy.

    Administrator drafts are proposals, so they intentionally never appear
    here.  The returned identifiers can be passed to ``training_create``;
    that operation revalidates the evidence under the same policy lock.
    """
    if (not isinstance(data,dict) or set(data)-{'policy_id','kind','limit','target_agent_id','target_channel_id'}
            or not {'policy_id','kind'} <= set(data)):
        raise TenantLinearError('Invalid training source request.',400)
    if data['kind'] not in {'knowledge','instruction','support_case','owner_knowledge_draft'}:
        raise TenantLinearError('Invalid training source kind.',400)
    limit=data.get('limit',25)
    if type(limit) is not int or not 1 <= limit <= 50:
        raise TenantLinearError('Invalid training source limit.',400)
    policy,profile=training_policy_guard(cur,cfg,data['policy_id'],'create',data.get('target_agent_id'),data.get('target_channel_id'))
    has_target='target_agent_id' in data and 'target_channel_id' in data
    if (policy.get('scope_kind')=='company')!=has_target:raise TenantLinearError('Target must match the policy scope.',403)
    if data['kind']=='owner_knowledge_draft' and policy.get('scope_kind')!='company':raise TenantLinearError('Owner drafts require a company delegation.',403)
    kind='knowledge' if data['kind']=='owner_knowledge_draft' else data['kind']
    if kind not in policy['kinds']:
        raise TenantLinearError('Material type outside delegation.',403)
    tenant=str(policy['tenant_id'])
    if data['kind']=='owner_knowledge_draft':
        if DRAFT_SCOPE not in set(cfg.get('granted_scopes') or []):raise TenantLinearError('Additional OAuth scope required.',403)
        from tenant_ai_owner_review_component import _draft_hash
        cur.execute('''SELECT * FROM saas_ai_administrator_drafts WHERE tenant_id=%s::uuid
          AND kind='knowledge' AND state='draft' AND created_by=%s
          ORDER BY updated_at DESC,id DESC LIMIT %s''',(tenant,policy['owner_id'],limit))
        items=[]
        for row in cur.fetchall():
            value=dict(row);body=value.get('body') or {}
            if not isinstance(body.get('content'),str) or not body['content'].strip():continue
            try: _,basis=_owner_draft_basis(cur,profile,str(value['id']),value['version'],None,policy,preview=True)
            except TenantLinearError:continue
            items.append({'source_id':str(value['id']),'kind':data['kind'],'title':value['title'],'version':value['version'],'source_hash':basis['hash'],'basis':basis,'preview':{'title':value['title'],'body':body,'content':body['content']}})
        return {'policy_id':str(policy['id']),'kind':data['kind'],'items':items}
    # Scan a bounded set because provenance can make a superficially matching
    # row ineligible. Nothing from drafts is queried or exposed here.
    if kind=='knowledge':
        cur.execute('''SELECT id FROM saas_ai_knowledge_sources
          WHERE tenant_id=%s::uuid AND active=TRUE AND agent_id=%s::uuid
          ORDER BY id LIMIT %s''',(tenant,policy['agent_id'],min(limit*4,200)))
    elif kind=='instruction':
        cur.execute("""SELECT id FROM saas_instruction_lessons
          WHERE tenant_id=%s::uuid AND state IN ('approved','published')
          ORDER BY id LIMIT %s""",(tenant,min(limit*4,200)))
    else:
        cur.execute("""SELECT id FROM saas_knowledge_candidates
          WHERE tenant_id=%s::uuid AND status IN ('pending_review','needs_clarification')
          ORDER BY id LIMIT %s""",(tenant,min(limit*4,200)))
    items=[]
    for value in cur.fetchall():
        if len(items)>=limit:
            break
        key=str(value['id'])
        try:
            row,basis=_training_basis(cur,profile,kind,key,policy)
        except TenantLinearError:
            continue
        if kind=='support_case' and row['status'] not in {'pending_review','needs_clarification'}:
            continue
        title=(row.get('title') if kind=='knowledge' else row.get('doc',{}).get('title') if kind=='instruction' else row.get('content',{}).get('title'))
        items.append({'source_id':key,'kind':kind,'title':title,'material_hash':_publication_hash(row),'basis':basis})
    return {'policy_id':str(policy['id']),'kind':kind,'items':items}


def training_operation(cur,cfg,action,data):
    import hashlib,json
    from tenant_ai_agents_store import _json,save_knowledge
    import tenant_ai_administrator_drafts as drafts
    import tenant_ai_administrator_apply as application
    if not isinstance(data,dict) or action not in {'create','update','publish','rollback'}:raise TenantLinearError('Invalid training operation.',400)
    base={'policy_id','request_id'}
    fields={'create':{'kind','source_id'},'update':{'draft_id','object_id','version','material_hash','source_id'},'publish':{'draft_id','object_id','version','material_hash'},'rollback':{'grant_id'}}[action]
    optional={'target_agent_id','target_channel_id'}|({'source_version','source_hash'} if action=='create' else set())
    if set(data)-optional!=base|fields or len(set(data)&{'target_agent_id','target_channel_id'}) not in (0,2) or len(set(data)&{'source_version','source_hash'}) not in (0,2):raise TenantLinearError('Invalid training contract.',400)
    p,profile=training_policy_guard(cur,cfg,data['policy_id'],action,data.get('target_agent_id'),data.get('target_channel_id'),data.get('grant_id') if action=='rollback' else None);tenant=str(p['tenant_id']);actor=p['owner_id']
    if action in {'create','update'} and DRAFT_SCOPE not in set(cfg.get('granted_scopes') or []):raise TenantLinearError('Additional OAuth scope required.',403)
    has_target=('target_agent_id' in data and 'target_channel_id' in data)
    if p.get('scope_kind')=='company' and action!='rollback' and not has_target:raise TenantLinearError('Company policy operations require one explicit target.',403)
    if p.get('scope_kind')!='company' and has_target:raise TenantLinearError('Target must match the policy scope.',403)
    if 'source_version' in data and not (action=='create' and data.get('kind')=='knowledge' and p.get('scope_kind')=='company'):raise TenantLinearError('Only company policy owner knowledge accepts an exact owner draft.',403)
    request_id=_training_uuid(data['request_id']);digest=hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    cur.execute('SELECT action,request_hash,result FROM saas_ai_training_operations WHERE policy_id=%s::uuid AND request_id=%s::uuid',(p['id'],request_id));old=cur.fetchone()
    if old:
        if old['action']!=action or old['request_hash']!=digest:raise TenantLinearError('Idempotency key reused with different parameters.',409)
        return old['result']
    cur.execute("SELECT count(*) AS total,count(*) FILTER(WHERE created_at>clock_timestamp()-interval '1 hour') AS hourly FROM saas_ai_training_operations WHERE policy_id=%s::uuid",(p['id'],));counts=cur.fetchone()
    if action!='rollback' and ((p.get('max_operations') is not None and counts['total']>=p['max_operations']) or (p.get('max_per_hour') is not None and counts['hourly']>=p['max_per_hour'])):raise TenantLinearError('Delegation operation limit reached.',429)
    if action=='rollback':
        cur.execute('SELECT policy_id,state FROM saas_ai_administrator_publication_grants WHERE tenant_id=%s::uuid AND id=%s::uuid',(tenant,_training_uuid(data['grant_id'])));grant=cur.fetchone()
        if not grant or str(grant['policy_id'])!=str(p['id']):raise TenantLinearError('Publication outside policy.',403)
        if grant['state']=='rolled_back':return {'grant_id':str(data['grant_id']),'rolled_back':True}
        result=publication_execute(cur,cfg,data['grant_id'],rollback=True)
    elif action=='create':
        kind=data['kind'];key=_training_uuid(data['source_id'])
        if kind not in p['kinds']:raise TenantLinearError('Material type outside delegation.',403)
        if kind=='support_case':
            try:
                row,reason=_training_case(cur,profile,key,str(p['channel_id']))
            except TenantLinearError as exc:
                if exc.status != 404:
                    raise
                cur.execute('''SELECT kind FROM saas_ai_administrator_drafts
                  WHERE tenant_id=%s::uuid AND id=%s::uuid FOR SHARE''',(tenant,key))
                if cur.fetchone():
                    raise TenantLinearError('Training source must be a reviewed material, not an administrator draft.',409) from None
                raise
            if row['status'] not in {'pending_review','needs_clarification'}:raise TenantLinearError('Case is already reviewed.',409)
            d=drafts.typed_create(cur,cfg,'support_case',{'title':row['content']['title'],'problem':row['content']['title'],'evidence_message_ids':row['content']['source_message_ids']})
            if reason:
                import tenant_knowledge_candidates_store as cases
                cases.mutate(profile,key,{'expected_version':row['version'],'clarification_note':'Delegated review: '+reason},'clarify',connection=cur.connection)
            _,basis=_training_basis(cur,profile,kind,key,p) if not reason else (row,{'kind':kind,'id':key,'hash':_publication_hash(_publication_object(cur,tenant,kind,key))})
            object_id=key
        else:
            if 'source_version' in data:
                owner_draft,basis=_owner_draft_basis(cur,profile,key,data['source_version'],data['source_hash'],p)
                content=owner_draft['body'].get('content')
                if kind!='knowledge' or not isinstance(content,str) or not content.strip():raise TenantLinearError('Owner knowledge draft has no content.',409)
                source=save_knowledge(profile,{'title':owner_draft['title'],'kind':'text','content':content.strip(),'active':False},connection=cur.connection)
                object_id=str(source['id']);reason=None;d=owner_draft
                cur.execute('UPDATE saas_ai_knowledge_sources SET provenance=%s::jsonb WHERE tenant_id=%s::uuid AND id=%s::uuid',(_json({'delegated_draft_id':key,'delegated_draft_version':owner_draft['version'],'delegated_draft_hash':basis['hash'],**({'owner_provided_draft_hash':basis['hash']} if basis['kind']=='owner_provided_draft' else {}),'delegated_draft_basis':basis,'review_status':('owner_delegated_owner_provided' if basis['kind']=='owner_provided_draft' else 'owner_delegated_evidence_draft'),'review_only':True}),tenant,object_id))
                _audit(cur,tenant,actor,'ai_training_owner_draft_prepared','knowledge_source',object_id,{'policy_id':str(p['id']),'draft_id':key,'draft_version':owner_draft['version'],'draft_hash':basis['hash'],'agent_id':p['agent_id'],'channel_id':p['channel_id']})
            else:
                row,basis=_training_source(cur,profile,kind,key,p);reason=None
                proposal={'title':row['title'],'content':row['content'],'evidence_ids':[],'agent_ids':[]} if kind=='knowledge' else {'title':row['doc']['title'],'doc':row['doc']}
                d=(drafts.create(cur,cfg,{'kind':'knowledge','title':row['title'],'body':{'content':row['content'],'evidence_ids':[]},'target':{'agent_ids':[]}}) if kind=='knowledge' else drafts.typed_create(cur,cfg,kind,proposal))
                receipt=request(cur,profile,d['id'],d['version'])
                if p.get('scope_kind')=='company':
                    cur.execute("UPDATE saas_ai_administrator_approvals SET state='approved',approved_by=NULL,approved_at=now(),delegation_policy_id=%s::uuid WHERE tenant_id=%s::uuid AND id=%s::uuid AND state='pending'",(p['id'],tenant,receipt['id']))
                    _audit(cur,tenant,actor,'ai_administrator_delegated_authorization','ai_administrator_approval',receipt['id'],{'policy_id':str(p['id']),'draft_id':d['id'],'version':d['version'],'basis':basis})
                else:approve(cur,profile,receipt['id'],'approve')
                cur.execute('UPDATE saas_ai_administrator_approvals SET delegation_policy_id=%s::uuid WHERE tenant_id=%s::uuid AND id=%s::uuid',(p['id'],tenant,receipt['id']))
                _audit(cur,tenant,actor,'ai_training_verified_copy_authorized','ai_administrator_approval',receipt['id'],{'policy_id':str(p['id']),'basis':basis,'individual_owner_click':False})
                applied=(application.apply_knowledge_draft if kind=='knowledge' else application.apply_instruction_draft)(cur,profile,receipt['id'],d['id'],scoped_training_policy_id=p['id'])
                object_id=applied.get('source_id') or applied['lesson_id']
        material=_publication_object(cur,tenant,kind,object_id)
        result={'draft_id':d['id'],'version':d['version'],'object_id':object_id,'material_hash':_publication_hash(material),'kind':kind,'basis':basis,'eligible':not bool(reason),'clarification_reason':reason}
    else:
        draft_id=_training_uuid(data['draft_id']);object_id=_training_uuid(data['object_id'])
        prepared=_training_prepared(cur,p,draft_id,object_id);kind=prepared['kind']
        if kind not in p['kinds']:raise TenantLinearError('Material type outside policy.',403)
        cur.execute('SELECT * FROM saas_ai_administrator_drafts WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,draft_id));d=cur.fetchone()
        material=_publication_object(cur,tenant,kind,object_id)
        if not d or type(data['version']) is not int or d['version']!=data['version'] or d['state']=='rolled_back' or _publication_hash(material)!=data['material_hash']:raise TenantLinearError('Version conflict; reload material.',409)
        if action=='update':
            source,basis=_training_source(cur,profile,kind,_training_uuid(data['source_id']),p)
            if kind=='support_case':
                if str(source['id'])!=object_id:raise TenantLinearError('Cannot replace historical case evidence.',403)
                reason=None
                # Refreshes only the verified proposal; never copies another case's outcome.
                body={'problem':source['content']['title'],'evidence_message_ids':source['content']['source_message_ids']};title=source['content']['title']
            elif kind=='knowledge':
                if material['active'] or material['agent_id'] is not None:raise TenantLinearError('Withdraw publication before updating.',409)
                body={'content':source['content'],'evidence_ids':[]};title=source['title']
                save_knowledge(profile,{'title':title,'content':source['content'],'kind':source['kind'],'active':False},source_id=object_id,connection=cur.connection)
            else:
                if material['state']=='published':raise TenantLinearError('Withdraw publication before updating.',409)
                body={'doc':source['doc']};title=source['doc']['title']
                import tenant_instruction_library as lib
                lib.operation(profile,'save',{'expected_version':material['version'],'doc':source['doc']},object_id,connection=cur.connection)
            d=drafts.update(cur,cfg,{'id':draft_id,'expected_version':d['version'],'title':title,'body':body})
            result={'draft_id':draft_id,'object_id':object_id,'version':d['version'],'kind':kind,'basis':basis,'eligible':True,'clarification_reason':None,'material_hash':_publication_hash(_publication_object(cur,tenant,kind,object_id))}
        else:
            # Revalidate authoritative provenance AND exact prepared contents. A generic
            # draft edit cannot smuggle facts into a delegated publication.
            if prepared['basis'].get('kind') in {'owner_provided_draft','evidence_backed_draft'}:
                source,basis=_owner_draft_basis(cur,profile,prepared['basis']['id'],prepared['basis']['version'],prepared['basis']['hash'],p)
                if basis!=prepared['basis'] or material['content']!=source['body'].get('content','').strip() or material['title']!=source['title']:
                    raise TenantLinearError('Owner draft or prepared material changed.',409)
            else:
                source,basis=_training_basis(cur,profile,kind,prepared['basis']['id'],p)
                if basis!=prepared['basis'] or not prepared['eligible']:raise TenantLinearError('Evidence changed; clarification or refresh required.',409)
                if kind=='knowledge' and (material['content']!=source['content'] or material['title']!=source['title']):raise TenantLinearError('Knowledge differs from verified evidence.',409)
            if kind=='instruction':
                from tenant_instruction_library import document
                if document(material['doc'])!=document(source['doc']):raise TenantLinearError('Instruction differs from verified evidence.',409)
            grant=publication_grant(cur,profile,{'draft_id':draft_id,'version':d['version'],'kind':kind,'object_id':object_id,'agent_id':str(p['agent_id']),'channel_id':str(p['channel_id']),'ttl_minutes':30,'review_confirmed':True,'material_hash':data['material_hash']},policy=p)
            result=publication_execute(cur,cfg,grant['grant_id'])
    cur.execute('INSERT INTO saas_ai_training_operations(policy_id,request_id,action,request_hash,result) VALUES(%s::uuid,%s::uuid,%s,%s,%s::jsonb)',(p['id'],request_id,action,digest,_json(result)))
    _audit(cur,tenant,actor,'ai_training_delegated_'+action,'ai_training_policy',p['id'],{'request_id':request_id,'result':result})
    return result


def training_definitions():
    base={'policy_id':{'type':'string','format':'uuid'},'request_id':{'type':'string','format':'uuid'}}
    target={'draft_id':{'type':'string','format':'uuid'},'object_id':{'type':'string','format':'uuid'},'version':{'type':'integer','minimum':1},'material_hash':{'type':'string','minLength':64,'maxLength':64}}
    company_target={'target_agent_id':{'type':'string','format':'uuid'},'target_channel_id':{'type':'string','format':'uuid'}}
    specs={
      'create':({'kind':{'type':'string','enum':['knowledge','instruction','support_case']},'source_id':{'type':'string','format':'uuid'},'source_version':{'type':'integer','minimum':1},'source_hash':{'type':'string','minLength':64,'maxLength':64}},'Prepare a private material from an existing reviewed source under an owner policy. Under an explicit company-wide owner policy, a knowledge source may instead be an exact owner-provided or evidence-backed knowledge draft, identified by its version and hash; it is published as delegated content with its source evidence, caveats and owner additions intact, never as individually approved or CRM-verified. Non-knowledge drafts are rejected. Support cases retain real evidence and outcome guards.'),
      'update':({**target,'source_id':{'type':'string','format':'uuid'}},'Update an unpublished policy material from verified evidence, with exact versions/hashes. No arbitrary facts or cross-case outcomes.'),
      'publish':(target,'Publish one exact eligible material after it has been discussed with the owner in chat. This separate tool invocation is not a chat-consent check. Rechecks evidence, target scope, versions, policy and revocation. No background or batch publishing, messages or public links.'),
      'rollback':({'grant_id':{'type':'string','format':'uuid'}},'Withdraw an unchanged policy publication, preserving history. Safe withdrawal remains possible after policy expiry/revocation.')}
    result=[{'name':'administrator_training_policies','description':'Read owner-created training delegation policies and their audit receipts for this OAuth agent/channel. Cannot grant policies.',
      'inputSchema':{'type':'object','properties':{},'additionalProperties':False},'annotations':{'readOnlyHint':True},'securitySchemes':[{'type':'oauth2','scopes':[PUBLISH_SCOPE]}]}]
    result.append({'name':'administrator_training_sources','description':'List reviewed, policy-eligible source materials for one kind. Administrator drafts are deliberately excluded; use one returned source_id with administrator_training_create.',
      'inputSchema':{'type':'object','properties':{'policy_id':{'type':'string','format':'uuid'},'kind':{'type':'string','enum':['knowledge','instruction','support_case']},'limit':{'type':'integer','minimum':1,'maximum':50},**company_target},'required':['policy_id','kind'],'additionalProperties':False},
      'annotations':{'readOnlyHint':True},'securitySchemes':[{'type':'oauth2','scopes':[PUBLISH_SCOPE]}]})
    result.append({'name':'administrator_training_owner_drafts','description':'List exact owner-provided or evidence-backed knowledge drafts eligible for this company policy. Returns preview, current version and hash; does not publish. Requires existing draft and publish OAuth scopes.',
      'inputSchema':{'type':'object','properties':{'policy_id':{'type':'string','format':'uuid'},'target_agent_id':{'type':'string','format':'uuid'},'target_channel_id':{'type':'string','format':'uuid'},'limit':{'type':'integer','minimum':1,'maximum':50}},'required':['policy_id','target_agent_id','target_channel_id'],'additionalProperties':False},
      'annotations':{'readOnlyHint':True,'destructiveHint':False,'openWorldHint':False},'securitySchemes':[{'type':'oauth2','scopes':[PUBLISH_SCOPE,DRAFT_SCOPE]}]})
    for action,(extra,description) in specs.items():
        fields={**base,**extra,**company_target}
        required=set(base)|set(extra)-({'source_version','source_hash'} if action=='create' else set())
        result.append({'name':'administrator_training_'+action,'description':description,
          'inputSchema':{'type':'object','properties':fields,'required':sorted(required),'additionalProperties':False},
          'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False},
          'securitySchemes':[{'type':'oauth2','scopes':[PUBLISH_SCOPE]+([DRAFT_SCOPE] if action in {'create','update'} else [])}]})
    return result
