"""Employee source scopes. Existing connections are never assigned by migration.

Only Connect has an immutable, tenant-verified source identifier in this
application. Kommo self/department is deliberately unavailable until its source
identity and employee mapping have been approved, rather than guessed from a
channel label or a deal's responsible user.
"""
import json
import uuid
from tenant_linear_policy import TenantLinearError


def migrate_dialog_access_schema(cur):
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_dialog_source_members (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      provider TEXT NOT NULL DEFAULT 'connect' CHECK(provider='connect'),
      source_id UUID NOT NULL,employee_id BIGINT NOT NULL,
      updated_by BIGINT NOT NULL,updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,provider,source_id,employee_id),
      FOREIGN KEY(tenant_id,employee_id) REFERENCES saas_tenant_members(tenant_id,telegram_id))''')
    cur.execute('''CREATE TABLE IF NOT EXISTS saas_dialog_source_settings (
      tenant_id UUID NOT NULL REFERENCES saas_tenants(id) ON DELETE CASCADE,
      source_id UUID NOT NULL,revision BIGINT NOT NULL DEFAULT 0,
      PRIMARY KEY(tenant_id,source_id))''')
    cur.execute('''CREATE INDEX IF NOT EXISTS saas_dialog_source_employee_idx
      ON saas_dialog_source_members(tenant_id,employee_id,source_id)''')


def scope(policy, action='view'):
    return policy.action_scope('dialogs', action)


def sql_filter(policy, provider='provider', source='channel_id', action='view'):
    """Internal SQL expressions only; all identity literals are typed/validated.

    Keeping parameters unchanged preserves the shared projection's legacy
    calling convention. No arbitrary profile string is interpolated into SQL.
    Department membership is read from current tenant rows, never client data.
    """
    selected=scope(policy,action)
    if selected is None or selected=='all':return 'TRUE'
    if selected=='none':return 'FALSE'
    tenant=str(uuid.UUID(str(policy.profile['tenant_id'])))
    actor=int(policy.profile['telegram_id'])
    own=f'm.telegram_id={actor}' if selected=='self' else f'''m.department_id IS NOT NULL AND m.department_id=(
      SELECT me.department_id FROM saas_tenant_members me WHERE me.tenant_id='{tenant}'::uuid
        AND me.telegram_id={actor} AND me.active=TRUE)'''
    return f'''({provider}='connect' AND EXISTS(SELECT 1 FROM saas_dialog_source_members ds
      JOIN saas_tenant_members m ON m.tenant_id=ds.tenant_id AND m.telegram_id=ds.employee_id
      WHERE ds.tenant_id='{tenant}'::uuid AND ds.provider='connect'
        AND ds.source_id::text={source} AND m.active=TRUE AND ({own})))'''


def require_source(cur, tenant, policy, provider, source='', action='view'):
    """Hold source/membership grants through the caller's transaction."""
    selected=scope(policy,action)
    if selected is None:return
    if str(tenant)!=str(policy.profile.get('tenant_id')) or policy.profile.get('active') is False:
        raise TenantLinearError('Источник недоступен.',403)
    if selected=='none':raise TenantLinearError('Это действие с диалогом не разрешено.',403)
    if provider!='connect':
        if provider=='kommo' and selected=='all':return
        raise TenantLinearError('Для этого источника ещё не настроена проверяемая привязка сотрудников.',403)
    try: source=str(uuid.UUID(str(source)))
    except (TypeError,ValueError):raise TenantLinearError('Источник недоступен.',404) from None
    cur.execute('''SELECT connect_channel_id FROM saas_connect_channels
      WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid FOR SHARE''',(tenant,source))
    if not cur.fetchone():raise TenantLinearError('Источник недоступен.',404)
    if selected=='all':return
    # Lock all existing source bindings and active employees. Changing or
    # removing a binding or department waits until an authorized send finishes.
    cur.execute('''SELECT m.telegram_id,m.department_id,m.active FROM saas_dialog_source_members ds
      JOIN saas_tenant_members m ON m.tenant_id=ds.tenant_id AND m.telegram_id=ds.employee_id
      WHERE ds.tenant_id=%s::uuid AND ds.provider='connect' AND ds.source_id=%s::uuid
      ORDER BY m.telegram_id FOR SHARE OF ds,m''',(tenant,source))
    members=cur.fetchall()
    actor=int(policy.profile['telegram_id'])
    department=policy.profile.get('department_id')
    allowed=any(m.get('active') is True and (int(m['telegram_id'])==actor if selected=='self' else
      department is not None and str(m.get('department_id'))==str(department)) for m in members)
    if not allowed:raise TenantLinearError('Источник недоступен по вашим правам.',403)


def allowed_channels(cur,tenant,policy,action='view'):
    if scope(policy,action) is None:return None
    cur.execute('SELECT connect_channel_id::text AS channel_id FROM saas_connect_channels WHERE tenant_id=%s::uuid AND '+
      sql_filter(policy,"'connect'",'connect_channel_id::text',action),(tenant,))
    channels=sorted(row['channel_id'] for row in cur.fetchall())
    # Group history and metadata perform subsequent reads. Retain each source
    # grant, not just the primary route, until those reads complete.
    for channel in channels:require_source(cur,tenant,policy,'connect',channel,action)
    return channels


def apply_capabilities(cur,tenant,policy,dialog):
    """UI is a projection of the same independent server action scopes."""
    if not policy.has_scoped_access('dialogs'):return dialog
    can_update=True
    try:require_source(cur,tenant,policy,dialog['provider'],dialog['channel_id'],'update')
    except TenantLinearError:can_update=False
    dialog['can_send']=bool(dialog.get('can_send') and can_update)
    if not can_update:dialog['send_block_reason']='permission_denied'
    dialog['can_use_copilot']=bool(dialog.get('can_use_copilot',True) and independent_ai_allowed(policy))
    dialog['can_edit_display_name']=bool(dialog.get('can_edit_display_name',dialog.get('provider')=='connect') and can_update)
    # Dialog access does not grant AI-management rights. Existing dedicated
    # AI endpoints still recheck their own independent permissions.
    if not can_update or not independent_ai_allowed(policy,management=True):
        dialog['controls_allowed']=False
        dialog['available_actions']=[]
        dialog['resume_allowed']=False
        dialog.pop('agent_assignment',None)
        dialog.pop('group_assignment_preview',None)
    if dialog.get('group_coordination'):filter_group(cur,tenant,policy,dialog['group_coordination'])
    return dialog


def sources(profile,payload=None):
    """Owner-reviewed mapping only. Does not modify Connect configuration."""
    from tenant_platform import _connect
    from tenant_scoped_access_store import read_access_profile
    with _connect() as conn:
        with conn.cursor() as cur:
            tenant=str(uuid.UUID(str(profile['tenant_id'])))
            actor=int(profile['telegram_id'])
            current=read_access_profile(cur,tenant,actor,lock=True)
            if not current or current.get('active') is not True or current.get('role')!='owner':
                raise TenantLinearError('Источники распределяет владелец компании.',403)
            if payload is not None:
                from tenant_scoped_access import scoped_access_enabled
                if not scoped_access_enabled():
                    raise TenantLinearError('Новая модель доступа ещё не включена.',409)
                source=str(uuid.UUID(str(payload.get('source_id'))))
                expected=payload.get('expected_revision')
                if type(expected) is not int or expected<0:
                    raise TenantLinearError('Обновите список источников перед сохранением.',409)
                ids=payload.get('employee_ids')
                if not isinstance(ids,list) or len(ids)>200 or any(type(x) is not int or x<=0 for x in ids):
                    raise TenantLinearError('Выберите сотрудников компании.')
                ids=sorted(set(ids))
                cur.execute('''SELECT connect_channel_id FROM saas_connect_channels WHERE tenant_id=%s::uuid
                  AND connect_channel_id=%s::uuid FOR UPDATE''',(tenant,source))
                if not cur.fetchone():raise TenantLinearError('Источник не найден.',404)
                cur.execute('SELECT revision FROM saas_dialog_source_settings WHERE tenant_id=%s::uuid AND source_id=%s::uuid FOR UPDATE',(tenant,source))
                revision=int((cur.fetchone() or {}).get('revision',0))
                if revision!=expected:raise TenantLinearError('Привязки источника уже изменились. Обновите список.',409)
                cur.execute('''SELECT telegram_id FROM saas_tenant_members WHERE tenant_id=%s::uuid
                  AND telegram_id=ANY(%s::bigint[]) AND active=TRUE ORDER BY telegram_id FOR SHARE''',(tenant,ids))
                if {int(row['telegram_id']) for row in cur.fetchall()}!=set(ids):
                    raise TenantLinearError('Сотрудник недоступен в этой компании.',403)
                cur.execute("DELETE FROM saas_dialog_source_members WHERE tenant_id=%s::uuid AND provider='connect' AND source_id=%s::uuid",(tenant,source))
                for employee in ids:
                    cur.execute('''INSERT INTO saas_dialog_source_members(tenant_id,provider,source_id,employee_id,updated_by)
                      VALUES(%s::uuid,'connect',%s::uuid,%s,%s)''',(tenant,source,employee,actor))
                cur.execute('''INSERT INTO saas_dialog_source_settings(tenant_id,source_id,revision) VALUES(%s::uuid,%s::uuid,1)
                  ON CONFLICT(tenant_id,source_id) DO UPDATE SET revision=saas_dialog_source_settings.revision+1''',(tenant,source))
                cur.execute('''INSERT INTO saas_tenant_audit_events(id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
                  VALUES(%s::uuid,%s::uuid,%s,'dialog_source_members_updated','dialog_source',%s,%s::jsonb)''',
                  (str(uuid.uuid4()),tenant,actor,source,json.dumps({'employee_ids':ids})))
            cur.execute('''SELECT 'connect' AS provider,ch.connect_channel_id::text AS source_id,ch.name,ch.active,
              COALESCE((SELECT jsonb_agg(ds.employee_id ORDER BY ds.employee_id) FROM saas_dialog_source_members ds
                WHERE ds.tenant_id=ch.tenant_id AND ds.provider='connect' AND ds.source_id=ch.connect_channel_id),'[]'::jsonb) AS employee_ids,COALESCE((SELECT revision FROM saas_dialog_source_settings st WHERE st.tenant_id=ch.tenant_id AND st.source_id=ch.connect_channel_id),0) AS revision
              FROM saas_connect_channels ch WHERE ch.tenant_id=%s::uuid ORDER BY ch.name,ch.connect_channel_id''',(tenant,))
            rows=cur.fetchall()
            return {'sources':rows,'preflight':activation_preflight(cur,tenant)}


def activation_preflight(cur,tenant):
    """Describe unresolved existing sources without assigning or enabling any."""
    cur.execute('''SELECT ch.connect_channel_id::text AS source_id,ch.name FROM saas_connect_channels ch
      WHERE ch.tenant_id=%s::uuid AND NOT EXISTS(SELECT 1 FROM saas_dialog_source_members ds
        JOIN saas_tenant_members m ON m.tenant_id=ds.tenant_id AND m.telegram_id=ds.employee_id AND m.active=TRUE
        WHERE ds.tenant_id=ch.tenant_id AND ds.source_id=ch.connect_channel_id AND ds.provider='connect')
      ORDER BY ch.name,ch.connect_channel_id''',(tenant,))
    unassigned=cur.fetchall()
    cur.execute("SELECT EXISTS(SELECT 1 FROM saas_crm_messages WHERE tenant_id=%s::uuid AND channel NOT LIKE 'connect:%%') AS present",(tenant,))
    kommo=bool((cur.fetchone() or {}).get('present'))
    return {'ready':not unassigned and not kommo,'unassigned_sources':unassigned,
      'kommo_source_mapping_required':kommo}


def validate_activation(cur,tenant,scopes):
    """Called in employee-card save before enabling dialog scopes."""
    if not isinstance(scopes,dict) or not any(value in {'self','department'} for value in (scopes.get('dialogs') or {}).values()):return
    check=activation_preflight(cur,tenant)
    if not check['ready']:
        raise TenantLinearError('Права диалогов пока нельзя активировать: сначала владелец должен проверить привязки всех источников; источники Kommo требуют отдельного согласования.',409)


def require_lead(cur,tenant,policy,lead,action='view'):
    """Guard older lead-based readers without inheriting CRM permissions."""
    if scope(policy,action) is None:return
    cur.execute('''SELECT connect_channel_id FROM saas_connect_conversations
      WHERE tenant_id=%s::uuid AND lead_id=%s''',(tenant,int(lead)))
    row=cur.fetchone()
    require_source(cur,tenant,policy,'connect' if row else 'kommo',str(row['connect_channel_id']) if row else '',action)


def independent_ai_allowed(policy,*,management=False):
    """Scoped dialog access never creates an implicit AI/configuration grant."""
    from tenant_scoped_access import scoped_access_enabled
    if scoped_access_enabled() and policy.profile.get('role') != 'owner' and policy.has_scoped_access('ai_agents'):
        return bool(not management and policy.can_use_chat_ai())
    if not scoped_access_enabled() or not policy.has_scoped_access('dialogs'):return True
    return bool(policy.allows('ai_agents') or (not management and policy.can_use_chat_ai()))


def require_independent_ai(policy,*,management=False):
    if not independent_ai_allowed(policy,management=management):
        raise TenantLinearError('Для этого действия требуется отдельное право AI.',403)


def filter_group(cur,tenant,policy,group):
    if scope(policy) is None:return group
    allowed=set(allowed_channels(cur,tenant,policy) or [])
    group['channels']=[c for c in group.get('channels',[]) if str(c['channel_id']) in allowed]
    group['assignments']=[a for a in group.get('assignments',[]) if str(a.get('channel_id')) in allowed]
    if str(group.get('sending_channel_id')) not in allowed:
        group['sending_channel_id']=None
        group['primary_agent_id']=None
    return group


def lead_message_filter(cur,tenant,policy,lead,*,column='channel'):
    """Bind old lead-only APIs to the route's channel, even for corrupt cache rows.

    saas_connect_conversations has UNIQUE(lead_id), so this legacy route has
    one canonical source. Group unions use separately authorized conversation
    routes and must not be inferred from mixed-channel cache rows for one lead.
    A lead ID does not prove a message source. The channel expression is an
    internal constant and the only embedded identity is a validated DB UUID.
    """
    if scope(policy) is None:return 'TRUE'
    cur.execute('''SELECT connect_channel_id FROM saas_connect_conversations
      WHERE tenant_id=%s::uuid AND lead_id=%s''',(tenant,int(lead)))
    row=cur.fetchone()
    if row:
        source=str(uuid.UUID(str(row['connect_channel_id'])))
        require_source(cur,tenant,policy,'connect',source)
        return column+"='connect:"+source+"'"
    require_source(cur,tenant,policy,'kommo')
    return column+" NOT LIKE 'connect:%%'"
