"""Existing membership permissions for the operator workspace, separate from AI configuration."""
from tenant_ai_agent_access import tenant_status_allows_management
from tenant_linear_policy import TenantLinearError, identifier
from tenant_platform import _read_tenant_workflow
from tenant_policy import TenantPolicy


def access(cur, profile, permission='dialogs',action='view'):
    if not isinstance(profile,dict): raise TenantLinearError('Нет доступа.',403)
    tenant=identifier(profile.get('tenant_id'))
    try: actor=int(profile.get('telegram_id'))
    except (TypeError,ValueError): raise TenantLinearError('Нет доступа.',403) from None
    from tenant_scoped_access_store import read_access_profile
    row=read_access_profile(cur,tenant,actor,lock=True)
    if not row or row.get('active') is not True or not tenant_status_allows_management(row.get('tenant_status')):
        raise TenantLinearError('Нет доступа к активной компании.',403)
    policy=TenantPolicy({**row,'tenant_id':tenant})
    scoped=policy.action_scope(permission,action)
    if not (scoped!='none' if scoped is not None else policy.legacy_allows(permission)): raise TenantLinearError('Владелец должен выдать доступ к этому разделу.',403)
    return tenant,actor,policy


def authorize(cur,profile,permission='support'):
    tenant,actor,_=access(cur,profile,permission)
    return tenant,actor


def assert_route(cur,tenant,policy,provider,conversation='',channel='',lead_id=None,*,allow_merged=False,action='view'):
    if provider=='connect':
        cur.execute('''SELECT c.* FROM saas_connect_conversations c WHERE c.tenant_id=%s::uuid
          AND (%s::bigint IS NULL OR c.lead_id=%s::bigint)
          AND (%s='' OR c.conversation_id=%s) AND (%s='' OR c.connect_channel_id::text=%s)''',
          (tenant,lead_id,lead_id,conversation,conversation,channel,channel))
        rows=cur.fetchall()
        if allow_merged and not rows and lead_id is not None and channel:
            # Exact, audited physical-merge redirect; never guess by name/phone.
            # Retain old support/evidence links without exposing another route.
            cur.execute("SELECT to_regclass('saas_connect_direct_merge_backups') AS present")
            if cur.fetchone()['present']:
                cur.execute('''SELECT c.* FROM saas_connect_direct_merge_backups b
                  JOIN saas_connect_conversations c ON c.tenant_id=b.tenant_id
                    AND c.connect_channel_id=b.channel_id AND c.conversation_id=b.canonical_conversation_id
                  WHERE b.tenant_id=%s::uuid AND b.channel_id::text=%s AND b.state='merged'
                    AND b.snapshot->>'version'='2' AND (b.snapshot->'leads'->>1)::bigint=%s
                    AND (%s='' OR b.split_conversation_id=%s)''', (tenant,channel,int(lead_id),conversation,conversation))
                rows=cur.fetchall()
        if allow_merged and len(rows)==1:
            from tenant_connect_dialog_equivalence import canonical
            original=rows[0]
            target=canonical(cur,tenant,str(original['connect_channel_id']),original['conversation_id'])
            if target!=original['conversation_id']:
                cur.execute('''SELECT * FROM saas_connect_conversations WHERE tenant_id=%s::uuid
                  AND connect_channel_id=%s::uuid AND conversation_id=%s''',
                  (tenant,str(original['connect_channel_id']),target))
                rows=cur.fetchall()
        if len(rows)!=1: raise TenantLinearError('Диалог недоступен в этой компании или требуется выбрать канал.',404)
        from tenant_dialog_access import require_source
        require_source(cur,tenant,policy,'connect',str(rows[0]['connect_channel_id']),action)
        return dict(rows[0])
    if provider!='kommo': raise TenantLinearError('Некорректный источник.')
    cur.execute('''SELECT d.* FROM saas_crm_deals d WHERE d.tenant_id=%s::uuid AND d.deleted_at IS NULL
      AND ((%s::bigint IS NOT NULL AND d.kommo_lead_id=%s::bigint) OR (%s::bigint IS NULL AND EXISTS(
        SELECT 1 FROM saas_ai_agent_jobs j WHERE j.tenant_id=d.tenant_id AND j.provider='kommo'
          AND j.lead_id=d.kommo_lead_id AND j.conversation_id=%s)))''',(tenant,lead_id,lead_id,lead_id,conversation))
    rows=cur.fetchall()
    from tenant_dialog_access import require_source,scope
    if len(rows)!=1 or (scope(policy,action) is None and policy.profile.get('role')!='owner' and not policy.can_access_deal(rows[0])):
        raise TenantLinearError('Переписка недоступна по вашим правам.',404)
    require_source(cur,tenant,policy,'kommo',channel,action)
    return dict(rows[0])
