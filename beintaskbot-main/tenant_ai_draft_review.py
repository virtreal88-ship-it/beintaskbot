"""Versioned review of existing drafts. No generation or new delivery transport."""
import hashlib
import json
from tenant_linear_policy import TenantLinearError, identifier
from tenant_platform import _connect
from tenant_ai_agents_schema import ensure_schema
from tenant_operator_access import access, assert_route


def schema(cur):
    cur.execute('ALTER TABLE saas_ai_agent_drafts ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1')
    cur.execute('ALTER TABLE saas_ai_agent_drafts ADD COLUMN IF NOT EXISTS original_draft TEXT')
    cur.execute('ALTER TABLE saas_ai_agent_drafts ADD COLUMN IF NOT EXISTS edited_by BIGINT')
    cur.execute('ALTER TABLE saas_ai_agent_drafts ADD COLUMN IF NOT EXISTS edited_at TIMESTAMPTZ')


def snapshot(cur, profile, draft_id, *, locked=False):
    from tenant_ai_agents_store import _authorize
    tenant,actor=_authorize(cur,profile)
    _,_,policy=access(cur,profile,'dialogs')
    cur.execute('''SELECT d.*,a.name AS agent_name,a.mode,a.enabled,a.role,a.scopes,a.version AS agent_version,e.provider,e.payload
      FROM saas_ai_agent_drafts d JOIN saas_ai_agents a ON a.tenant_id=d.tenant_id AND a.id=d.agent_id
      JOIN saas_tenant_webhook_events e ON e.tenant_id=d.tenant_id AND e.fingerprint=d.source_fingerprint
      WHERE d.tenant_id=%s::uuid AND d.id=%s::uuid'''+(' FOR UPDATE OF d' if locked else ''),(tenant,identifier(draft_id)))
    row=cur.fetchone()
    if not row:raise TenantLinearError('draft_not_found',404)
    d=dict(row);payload=d['payload'] or {};provider=d['provider'];channel=str(payload.get('channel_id') or '')
    route=assert_route(cur,tenant,policy,provider,d['chat_id'],channel,d['lead_id'])
    if provider=='connect' and (not channel or payload.get('conversation_id')!=d['chat_id']):
        raise TenantLinearError('draft_scope_unverified',409)
    history_channel='connect:'+channel if provider=='connect' else 'kommo'
    cur.execute('''SELECT revision,state,last_inbound_message_id,agent_id FROM saas_ai_conversation_state
      WHERE tenant_id=%s::uuid AND provider=%s AND channel_id=%s AND conversation_id=%s''',
      (tenant,provider,channel if provider=='connect' else '',d['chat_id']))
    state=dict(cur.fetchone() or {})
    group=provider=='connect' and d['chat_id'].endswith('@g.us')
    cur.execute('''SELECT m.id,m.external_id,m.body,m.message_type,m.happened_at,m.created_at,m.channel
      FROM saas_crm_messages m WHERE m.tenant_id=%s::uuid AND m.kommo_lead_id=%s AND m.channel=%s
        AND m.external_id=%s''',(tenant,d['lead_id'],history_channel,('connect:' if provider=='connect' else '')+d['source_message_id']))
    source=cur.fetchone()
    # Include every exact group channel in the freshness fence, but never expand send scope.
    cur.execute('''SELECT m.id,m.created_at FROM saas_crm_messages m WHERE m.tenant_id=%s::uuid
      AND m.direction='incoming' AND ((m.kommo_lead_id=%s AND m.channel=%s) OR
      (%s AND EXISTS(SELECT 1 FROM saas_connect_conversations c WHERE c.tenant_id=m.tenant_id
        AND c.conversation_id=%s AND c.lead_id=m.kommo_lead_id AND m.channel='connect:'||c.connect_channel_id::text)))
      ORDER BY m.created_at DESC,m.id DESC LIMIT 1''',(tenant,d['lead_id'],history_channel,group,d['chat_id']))
    latest=dict(cur.fetchone() or {})
    newer=bool(latest and latest['created_at']>d['created_at']) or bool(state.get('last_inbound_message_id') and state['last_inbound_message_id']!=d['source_message_id'])
    cur.execute('SELECT revision,state FROM saas_ai_group_coordination WHERE tenant_id=%s::uuid AND group_jid=%s',(tenant,d['chat_id']))
    coordination=dict(cur.fetchone() or {}) if group else {}
    token=hashlib.sha256(json.dumps([str(d['id']),d['version'],d['agent_version'],state,latest,coordination],sort_keys=True,default=str).encode()).hexdigest()
    cur.execute('SELECT 1 FROM saas_ai_expert_tasks WHERE tenant_id=%s::uuid AND parent_run_id=%s::uuid LIMIT 1',(tenant,d['run_id']))
    expert=bool(cur.fetchone())
    reason='handoff' if d['handoff_after_send'] else 'expert_called' if expert else 'unknown'
    name=route.get('manual_display_name') or (route.get('display_name') if group else route.get('contact_name')) or route.get('display_name') or route.get('name') or ''
    if name==d['chat_id']:name=''
    channel_name=''
    if provider=='connect':
        cur.execute('SELECT name FROM saas_connect_channels WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid',(tenant,channel))
        channel_name=(cur.fetchone() or {}).get('name') or ''
    messages=[]
    if source:
        m=dict(source);messages=[{'id':str(m['id']),'text':m['body'] or '', 'message_type':m['message_type'],
          'happened_at':str(m['happened_at'] or m['created_at']), 'source_channel_id':channel,
          'external_id':m['external_id'],'media_available':m['message_type'] in {'image','audio','video','document','file'}}]
    view={k:d[k] for k in ('draft','version','state','handoff_after_send','source_message_id')}
    view.update(id=str(d['id']),agent_id=str(d['agent_id']),agent_name=d['agent_name'],created_at=str(d['created_at']),
      original_draft=d['original_draft'] if d['original_draft'] is not None else d['draft'],
      edited_by=d['edited_by'],edited_at=str(d['edited_at']) if d['edited_at'] else None,
      customer_name=name,channel_name=channel_name,provider=provider,channel_id=channel,conversation_id=d['chat_id'],lead_id=d['lead_id'],
      source_messages=messages,source_known=bool(messages),reason={'code':reason},has_new_messages=newer,context_token=token,
      dialog_route={'provider':provider,'channel_id':channel,'conversation_id':d['chat_id'],'lead_id':d['lead_id'],
                    'source_message_id':str(source['id']) if source else None},
      edit_allowed=d['state']=='pending',send_allowed=d['state']=='pending' and d['enabled'] and d['role']=='primary' and d['mode']=='manual' and state.get('state')=='active' and not payload.get('test_mode'))
    return view,d,state,(tenant,actor)


def read(profile,draft_id,connect=None):
    with (connect or _connect)() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:return snapshot(cur,profile,draft_id)[0]


def edit(profile,draft_id,data):
    text=data.get('draft')
    if not isinstance(text,str) or not text.strip() or len(text)>20000:raise TenantLinearError('draft_text_invalid',400)
    from tenant_ai_agents_store import _audit
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            view,d,_,(tenant,actor)=snapshot(cur,profile,draft_id,locked=True)
            if d['state']!='pending' or (type(data.get('expected_version')) is not int or data['expected_version']!=d['version']):raise TenantLinearError('draft_version_conflict',409)
            cur.execute('''UPDATE saas_ai_agent_drafts SET original_draft=COALESCE(original_draft,draft),draft=%s,
              version=version+1,edited_by=%s,edited_at=clock_timestamp() WHERE tenant_id=%s::uuid AND id=%s::uuid''',(text,actor,tenant,draft_id))
            _audit(cur,tenant,actor,'ai_draft_edited','ai_agent_draft',draft_id,{'previous_version':d['version'],'version':d['version']+1})
        conn.commit()
    return read(profile,draft_id)


def validate_send(cur,profile,draft_id,data):
    view,d,state,(tenant,actor)=snapshot(cur,profile,draft_id,locked=True)
    if (type(data.get('expected_version')) is not int or data['expected_version']!=d['version']):raise TenantLinearError('draft_version_conflict',409)
    if data.get('expected_context_token')!=view['context_token']:raise TenantLinearError('draft_context_changed',409)
    if d['provider']=='connect':
        from tenant_ai_agent_connect import scope_matches
        if not view['source_known'] or not scope_matches(d['scopes'],view['channel_id']):raise TenantLinearError('draft_scope_unverified',409)
        if state.get('agent_id') and str(state['agent_id'])!=str(d['agent_id']):raise TenantLinearError('draft_agent_changed',409)
    if d['mode']!='manual' or d['state']!='pending' or d['role']!='primary':raise TenantLinearError('draft_send_unavailable',409)
    if view['has_new_messages']:
        if data.get('confirm_new_messages') is not True:raise TenantLinearError('draft_new_messages_review_required',409)
        # Do not compete with a newer generation or a newer pending proposal.
        cur.execute('''SELECT 1 FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND provider=%s
          AND channel_id=%s AND conversation_id=%s AND state IN ('queued','processing','unknown')
          UNION ALL SELECT 1 FROM saas_ai_agent_drafts x JOIN saas_tenant_webhook_events e
            ON e.tenant_id=x.tenant_id AND e.fingerprint=x.source_fingerprint
          WHERE x.tenant_id=%s::uuid AND x.chat_id=%s AND e.provider=%s AND e.payload->>'channel_id'=%s
            AND x.state IN ('pending','sending','unknown') AND x.created_at>%s LIMIT 1''',
          (tenant,d['provider'],view['channel_id'],d['chat_id'],tenant,d['chat_id'],d['provider'],view['channel_id'],d['created_at']))
        if cur.fetchone():raise TenantLinearError('draft_newer_work_exists',409)
        if d['provider']!='connect':raise TenantLinearError('draft_stale_kommo_refresh_required',409)
        return state.get('last_inbound_message_id') or ''
    return ''
