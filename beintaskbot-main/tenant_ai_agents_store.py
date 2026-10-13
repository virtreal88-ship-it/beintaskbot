"""Tenant-scoped persistence for agent settings and knowledge sources."""
import hashlib
import uuid

from tenant_ai_agents_policy import (MAX_AGENTS, MAX_SOURCES, agent as validate_agent,
    company_settings, knowledge as validate_knowledge, chunk_text, trigger as validate_trigger)
from tenant_ai_agents_schema import ensure_schema
from tenant_linear_policy import TenantLinearError, identifier
from tenant_platform import _connect, _json


def _authorize(cur, profile):
    if not isinstance(profile, dict):
        raise TenantLinearError('İcazə yoxdur.', 403)
    tenant_id = identifier(profile.get('tenant_id'))
    try:
        actor = int(profile.get('telegram_id'))
    except (TypeError, ValueError):
        raise TenantLinearError('İcazə yoxdur.', 403) from None
    cur.execute('''SELECT m.role,m.active,t.status,t.onboarding
      FROM saas_tenant_members m JOIN saas_tenants t ON t.id=m.tenant_id
      WHERE m.tenant_id=%s::uuid AND m.telegram_id=%s FOR SHARE OF m,t''', (tenant_id, actor))
    row = cur.fetchone()
    if not row or not row.get('active') or row.get('status') != 'active':
        raise TenantLinearError('İcazə yoxdur.', 403)
    if row.get('role') == 'owner':
        return tenant_id, actor
    if row.get('role') != 'admin':
        raise TenantLinearError('AI Agents idarəetmə icazəsi yoxdur.', 403)
    cur.execute('''SELECT value FROM saas_tenant_workflow_policies
      WHERE tenant_id=%s::uuid AND policy_key='members' ''', (tenant_id,))
    policies = cur.fetchone() or {}
    members = policies.get('value') or {}
    if (members.get(str(actor)) or {}).get('ai_agents_manage') is not True:
        raise TenantLinearError('AI Agents idarəetmə icazəsi yoxdur.', 403)
    return tenant_id, actor


def _audit(cur, tenant_id, actor, action, entity, entity_id, payload=None):
    cur.execute('''INSERT INTO saas_tenant_audit_events
      (id,tenant_id,actor_telegram_id,action,entity_type,entity_id,payload)
      VALUES(%s,%s::uuid,%s,%s,%s,%s,%s::jsonb)''',
      (uuid.uuid4(), tenant_id, actor, action, entity, str(entity_id or ''), _json(payload or {})))


def _row(row):
    if not row:
        return None
    value = dict(row)
    for key in ('id', 'tenant_id'):
        if key in value:
            value[key] = str(value[key])
    for key in ('created_at', 'updated_at'):
        if value.get(key):
            value[key] = value[key].isoformat()
    return value


def _scopes_overlap(left, right):
    for a in left if isinstance(left, list) else []:
        for b in right if isinstance(right, list) else []:
            if not isinstance(a,dict) or not isinstance(b,dict):
                continue
            if (str(a.get('pipeline_id'))==str(b.get('pipeline_id'))
                    and str(a.get('stage_id'))==str(b.get('stage_id'))):
                channel_a=str(a.get('channel_id') or '').casefold()
                channel_b=str(b.get('channel_id') or '').casefold()
                if not channel_a or not channel_b or channel_a==channel_b:
                    return True
    return False


def _ensure_no_active_scope_overlap(cur, tenant, selected_scopes, exclude_agent=None):
    cur.execute('''SELECT id,scopes FROM saas_ai_agents WHERE tenant_id=%s::uuid AND enabled=TRUE
      AND (%s::uuid IS NULL OR id<>%s::uuid)''', (tenant,exclude_agent,exclude_agent))
    if any(_scopes_overlap(selected_scopes,row.get('scopes')) for row in cur.fetchall()):
        raise TenantLinearError('Aktiv agentlərin vərəq, mərhələ və kanal sahələri üst-üstə düşə bilməz.',409)


def read_settings(profile):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, _ = _authorize(cur, profile)
            cur.execute('''INSERT INTO saas_ai_agent_settings(tenant_id) VALUES(%s::uuid)
              ON CONFLICT(tenant_id) DO NOTHING''', (tenant,))
            cur.execute('''SELECT company_instructions,enabled,auto_send_enabled,crm_actions_enabled,max_replies_per_chat,updated_at
              FROM saas_ai_agent_settings WHERE tenant_id=%s::uuid''', (tenant,))
            result = _row(cur.fetchone())
        conn.commit()
    return result


def save_settings(profile, data):
    patch = company_settings(data)
    if not patch:
        raise TenantLinearError('Dəyişdiriləcək ayar yoxdur.')
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, actor = _authorize(cur, profile)
            if any(patch.get(key) is True for key in ('auto_send_enabled','crm_actions_enabled')) and profile.get('role') != 'owner':
                raise TenantLinearError('Avtomatik göndərmə və CRM yazılarını yalnız şirkət sahibi aça bilər.',403)
            if patch.get('enabled') is True:
                cur.execute("SELECT secrets,metadata FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='chat_ai'", (tenant,))
                integration = cur.fetchone() or {}
                metadata = integration.get('metadata') or {}
                if not integration.get('secrets') or metadata.get('enabled') is not True or metadata.get('consent') is not True:
                    raise TenantLinearError('Əvvəlcə mövcud Chat AI ayarlarını və razılığı tamamlayın.', 409)
            cur.execute('''INSERT INTO saas_ai_agent_settings(tenant_id) VALUES(%s::uuid)
              ON CONFLICT(tenant_id) DO NOTHING''', (tenant,))
            cur.execute('''SELECT company_instructions,enabled,auto_send_enabled,crm_actions_enabled,max_replies_per_chat FROM saas_ai_agent_settings
              WHERE tenant_id=%s::uuid FOR UPDATE''', (tenant,))
            previous = cur.fetchone() or {}
            values = {'company_instructions': str(previous.get('company_instructions') or ''),
                      **{key: bool(previous.get(key, False)) for key in ('enabled','auto_send_enabled','crm_actions_enabled')},
                      'max_replies_per_chat': int(previous.get('max_replies_per_chat') or 20)}
            values.update(patch)
            cur.execute('''UPDATE saas_ai_agent_settings SET company_instructions=%s,enabled=%s,auto_send_enabled=%s,
              crm_actions_enabled=%s,max_replies_per_chat=%s,updated_by=%s,updated_at=now() WHERE tenant_id=%s::uuid
              RETURNING company_instructions,enabled,auto_send_enabled,crm_actions_enabled,max_replies_per_chat,updated_at''',
              (values['company_instructions'], values['enabled'], values['auto_send_enabled'], values['crm_actions_enabled'],
               values['max_replies_per_chat'], actor, tenant))
            result = _row(cur.fetchone())
            _audit(cur, tenant, actor, 'ai_agents_settings_updated', 'ai_agents', '', {'enabled': values['enabled']})
        conn.commit()
    return result


def list_agents(profile):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, _ = _authorize(cur, profile)
            cur.execute('''SELECT id,tenant_id,name,instructions,enabled,mode,language,style,delay_seconds,
              schedule,scopes,model,version,created_at,updated_at FROM saas_ai_agents
              WHERE tenant_id=%s::uuid ORDER BY created_at,id''', (tenant,))
            rows = [_row(row) for row in cur.fetchall()]
    return rows


def save_agent(profile, data, *, agent_id=None):
    creating = agent_id is None
    clean_data = {key: value for key, value in data.items() if key != 'expected_version'} if isinstance(data, dict) else data
    patch = validate_agent(clean_data, creating=creating)
    if not patch:
        raise TenantLinearError('Dəyişdiriləcək agent ayarı yoxdur.')
    if not creating and type(data.get('expected_version')) is not int:
        raise TenantLinearError('Agent versiyası yoxdur.', 409)
    model = patch.get('model')
    if model:
        # Agents share the current Chat AI credential and may select only the
        # model already configured there. They never create a provider key.
        with _connect() as conn:
            ensure_schema(conn)
            with conn.cursor() as cur:
                tenant, _ = _authorize(cur, profile)
                cur.execute("SELECT metadata FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='chat_ai'", (tenant,))
                config = (cur.fetchone() or {}).get('metadata') or {}
                if model != str(config.get('model') or ''):
                    raise TenantLinearError('Əvvəlcə mövcud Chat AI ayarlarında modeli seçin.', 409)
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, actor = _authorize(cur, profile)
            for scope in patch.get('scopes', []):
                cur.execute('''SELECT 1 FROM saas_tenant_pipelines p
                  JOIN saas_tenant_pipeline_stages s ON s.tenant_id=p.tenant_id AND s.pipeline_id=p.pipeline_id
                  WHERE p.tenant_id=%s::uuid AND p.pipeline_id=%s AND s.stage_id=%s
                    AND p.active=TRUE''', (tenant, scope['pipeline_id'], scope['stage_id']))
                if not cur.fetchone():
                    raise TenantLinearError('Vərəq və mərhələ bu şirkətdə aktiv deyil.')
            if creating:
                cur.execute('SELECT count(*) AS total FROM saas_ai_agents WHERE tenant_id=%s::uuid', (tenant,))
                if int((cur.fetchone() or {}).get('total') or 0) >= MAX_AGENTS:
                    raise TenantLinearError('Agent sayı həddə çatıb.')
                if patch['enabled']:
                    _ensure_no_active_scope_overlap(cur,tenant,patch['scopes'])
                    cur.execute('''SELECT s.enabled,i.secrets,i.metadata FROM saas_ai_agent_settings s
                      LEFT JOIN saas_tenant_integrations i ON i.tenant_id=s.tenant_id AND i.provider='chat_ai'
                      WHERE s.tenant_id=%s::uuid''', (tenant,))
                    readiness = cur.fetchone() or {}
                    metadata = readiness.get('metadata') or {}
                    if readiness.get('enabled') is not True or not readiness.get('secrets') or metadata.get('enabled') is not True or metadata.get('consent') is not True:
                        raise TenantLinearError('Əvvəlcə AI Agents və mövcud Chat AI ayarlarını tamamlayın.', 409)
                agent_uuid = uuid.uuid4()
                cur.execute('''INSERT INTO saas_ai_agents(id,tenant_id,name,instructions,enabled,mode,language,style,
                  delay_seconds,schedule,scopes,model,created_by) VALUES(%s,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s)
                  RETURNING id,tenant_id,name,instructions,enabled,mode,language,style,delay_seconds,schedule,scopes,model,version,created_at,updated_at''',
                  (agent_uuid, tenant, patch['name'], patch['instructions'], patch['enabled'], patch['mode'], patch['language'], patch['style'],
                   patch['delay_seconds'], _json(patch['schedule']), _json(patch['scopes']), patch['model'], actor))
                result = _row(cur.fetchone())
                _audit(cur, tenant, actor, 'ai_agent_created', 'ai_agent', agent_uuid, {'name': patch['name']})
            else:
                agent_uuid = identifier(agent_id)
                cur.execute('''SELECT * FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE''', (tenant, agent_uuid))
                previous = cur.fetchone()
                if not previous:
                    raise TenantLinearError('Agent tapılmadı.', 404)
                if previous['version'] != data['expected_version']:
                    raise TenantLinearError('Agent dəyişib. Səhifəni yeniləyin.', 409)
                merged = {key: previous[key] for key in ('name','instructions','enabled','mode','language','style','delay_seconds','schedule','scopes','model')}
                merged.update(patch)
                if merged['enabled']:
                    _ensure_no_active_scope_overlap(cur,tenant,merged['scopes'],exclude_agent=agent_uuid)
                if merged['enabled'] and not previous['enabled']:
                    cur.execute('''SELECT s.enabled,i.secrets,i.metadata FROM saas_ai_agent_settings s
                      LEFT JOIN saas_tenant_integrations i ON i.tenant_id=s.tenant_id AND i.provider='chat_ai'
                      WHERE s.tenant_id=%s::uuid''', (tenant,))
                    readiness = cur.fetchone() or {}
                    metadata = readiness.get('metadata') or {}
                    if readiness.get('enabled') is not True or not readiness.get('secrets') or metadata.get('enabled') is not True or metadata.get('consent') is not True:
                        raise TenantLinearError('Əvvəlcə AI Agents və mövcud Chat AI ayarlarını tamamlayın.', 409)
                cur.execute('''UPDATE saas_ai_agents SET name=%s,instructions=%s,enabled=%s,mode=%s,language=%s,
                  style=%s,delay_seconds=%s,schedule=%s::jsonb,scopes=%s::jsonb,model=%s,version=version+1,updated_at=now()
                  WHERE tenant_id=%s::uuid AND id=%s::uuid
                  RETURNING id,tenant_id,name,instructions,enabled,mode,language,style,delay_seconds,schedule,scopes,model,version,created_at,updated_at''',
                  (merged['name'], merged['instructions'], merged['enabled'], merged['mode'], merged['language'], merged['style'], merged['delay_seconds'],
                   _json(merged['schedule']), _json(merged['scopes']), merged['model'], tenant, agent_uuid))
                result = _row(cur.fetchone())
                _audit(cur, tenant, actor, 'ai_agent_updated', 'ai_agent', agent_uuid, {'version': result['version']})
        conn.commit()
    return result


def delete_agent(profile, agent_id):
    agent_uuid = identifier(agent_id)
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, actor = _authorize(cur, profile)
            cur.execute('DELETE FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid RETURNING id', (tenant, agent_uuid))
            row = cur.fetchone()
            if not row:
                raise TenantLinearError('Agent tapılmadı.', 404)
            _audit(cur, tenant, actor, 'ai_agent_deleted', 'ai_agent', agent_uuid)
        conn.commit()
    return {'deleted': True}


def list_knowledge(profile, source_id=None):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, _ = _authorize(cur, profile)
            if source_id:
                source = identifier(source_id)
                cur.execute('''SELECT id,tenant_id,title,kind,content,active,content_hash,created_at,updated_at
                  FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid AND id=%s::uuid''', (tenant, source))
                row = _row(cur.fetchone())
                if not row:
                    raise TenantLinearError('Bilik mənbəyi tapılmadı.', 404)
                return row
            cur.execute('''SELECT id,tenant_id,title,kind,active,content_hash,created_at,updated_at,
              (SELECT count(*) FROM saas_ai_knowledge_chunks c WHERE c.tenant_id=s.tenant_id AND c.source_id=s.id) AS chunks
              FROM saas_ai_knowledge_sources s WHERE tenant_id=%s::uuid ORDER BY created_at DESC,id''', (tenant,))
            return [_row(row) for row in cur.fetchall()]


def save_knowledge(profile, data, *, source_id=None):
    creating = source_id is None
    patch = validate_knowledge(data, creating=creating)
    if not patch:
        raise TenantLinearError('Dəyişdiriləcək bilik ayarı yoxdur.')
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, actor = _authorize(cur, profile)
            if creating:
                cur.execute('SELECT count(*) AS total FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid', (tenant,))
                if int((cur.fetchone() or {}).get('total') or 0) >= MAX_SOURCES:
                    raise TenantLinearError('Bilik mənbələrinin sayı həddə çatıb.')
                source = uuid.uuid4()
                digest = hashlib.sha256(patch['content'].encode('utf-8')).hexdigest()
                cur.execute('''INSERT INTO saas_ai_knowledge_sources(id,tenant_id,title,kind,content,active,content_hash,created_by)
                  VALUES(%s,%s::uuid,%s,%s,%s,%s,%s,%s) RETURNING id,tenant_id,title,kind,content,active,content_hash,created_at,updated_at''',
                  (source, tenant, patch['title'], patch['kind'], patch['content'], patch['active'], digest, actor))
                result = _row(cur.fetchone())
            else:
                source = identifier(source_id)
                cur.execute('''SELECT * FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE''', (tenant, source))
                previous = cur.fetchone()
                if not previous:
                    raise TenantLinearError('Bilik mənbəyi tapılmadı.', 404)
                merged = {key: previous[key] for key in ('title','kind','content','active')}
                merged.update(patch)
                digest = hashlib.sha256(merged['content'].encode('utf-8')).hexdigest()
                cur.execute('''UPDATE saas_ai_knowledge_sources SET title=%s,kind=%s,content=%s,active=%s,
                  content_hash=%s,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid
                  RETURNING id,tenant_id,title,kind,content,active,content_hash,created_at,updated_at''',
                  (merged['title'], merged['kind'], merged['content'], merged['active'], digest, tenant, source))
                result = _row(cur.fetchone())
            if creating or 'content' in patch or (not creating and result['content_hash'] != previous.get('content_hash')):
                cur.execute('DELETE FROM saas_ai_knowledge_chunks WHERE tenant_id=%s::uuid AND source_id=%s::uuid', (tenant, source))
                for index, chunk in enumerate(chunk_text(result['content'])):
                    cur.execute('INSERT INTO saas_ai_knowledge_chunks(tenant_id,source_id,chunk_no,content) VALUES(%s::uuid,%s::uuid,%s,%s)',
                                (tenant, source, index, chunk))
            _audit(cur, tenant, actor, 'ai_knowledge_created' if creating else 'ai_knowledge_updated', 'knowledge_source', source,
                   {'kind': result['kind'], 'content_hash': result['content_hash']})
        conn.commit()
    return result


def delete_knowledge(profile, source_id):
    source = identifier(source_id)
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, actor = _authorize(cur, profile)
            cur.execute('DELETE FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid AND id=%s::uuid RETURNING id', (tenant, source))
            if not cur.fetchone():
                raise TenantLinearError('Bilik mənbəyi tapılmadı.', 404)
            _audit(cur, tenant, actor, 'ai_knowledge_deleted', 'knowledge_source', source)
        conn.commit()
    return {'deleted': True}


def search_knowledge(profile, query, *, limit=6):
    """Bounded tenant-only retrieval for the later manual draft workflow."""
    query = str(query or '').strip()[:1000]
    if not query:
        return []
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, _ = _authorize(cur, profile)
            cur.execute('''SELECT s.id AS source_id,s.title,c.chunk_no,c.content,
              ts_rank(c.search_vector,plainto_tsquery('simple',%s)) AS rank
              FROM saas_ai_knowledge_chunks c JOIN saas_ai_knowledge_sources s
                ON s.tenant_id=c.tenant_id AND s.id=c.source_id
              WHERE c.tenant_id=%s::uuid AND s.active=TRUE
                AND (c.search_vector @@ plainto_tsquery('simple',%s) OR c.content ILIKE %s)
              ORDER BY rank DESC,s.updated_at DESC,c.chunk_no LIMIT %s''',
              (query, tenant, query, '%' + query[:120] + '%', max(1, min(int(limit), 12))))
            return [{**dict(row), 'source_id': str(row['source_id'])} for row in cur.fetchall()]


def list_drafts(profile, *, limit=50):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, _ = _authorize(cur, profile)
            cur.execute('''SELECT d.id,d.agent_id,a.name AS agent_name,d.lead_id,d.talk_id,d.chat_id,
              d.source_message_id,d.draft,d.source_ids,d.state,d.created_at
              FROM saas_ai_agent_drafts d JOIN saas_ai_agents a
                ON a.tenant_id=d.tenant_id AND a.id=d.agent_id
              WHERE d.tenant_id=%s::uuid AND EXISTS (SELECT 1 FROM saas_ai_agent_test_mode b
                JOIN saas_tenant_integrations i ON i.tenant_id=b.tenant_id AND i.provider='kommo' AND i.status='connected'
                WHERE b.tenant_id=d.tenant_id AND b.enabled=TRUE AND b.verified=TRUE AND b.emergency_stop=FALSE
                  AND b.account_domain=i.account_domain AND b.lead_id=d.lead_id AND b.contact_id=(
                    SELECT CASE WHEN e.payload->>'contact_id' ~ '^[0-9]{1,18}$' THEN (e.payload->>'contact_id')::bigint END FROM saas_tenant_webhook_events e
                    WHERE e.tenant_id=d.tenant_id AND e.fingerprint=d.source_fingerprint)
                  AND b.talk_id=d.talk_id AND b.chat_id=d.chat_id
                  AND b.channel_id=(SELECT j.channel_id FROM saas_ai_agent_jobs j
                    WHERE j.tenant_id=d.tenant_id AND j.agent_id=d.agent_id AND j.conversation_id=d.chat_id))
              AND a.enabled=TRUE AND EXISTS (SELECT 1 FROM saas_ai_agent_settings s WHERE s.tenant_id=d.tenant_id AND s.enabled=TRUE)
              ORDER BY d.created_at DESC,d.id LIMIT %s''',
              (tenant, max(1, min(int(limit), 100))))
            rows = cur.fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item['id'] = str(item['id']); item['agent_id'] = str(item['agent_id'])
        if item.get('created_at'):
            item['created_at'] = item['created_at'].isoformat()
        result.append(item)
    return result


def list_triggers(profile,agent_id):
    agent=identifier(agent_id)
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,_=_authorize(cur,profile)
            cur.execute('''SELECT id,agent_id,name,trigger_type,condition,actions,enabled,version,created_at,updated_at
              FROM saas_ai_agent_triggers WHERE tenant_id=%s::uuid AND agent_id=%s::uuid ORDER BY created_at,id''',(tenant,agent))
            rows=cur.fetchall()
    result=[]
    for row in rows:
        item=dict(row);item['id']=str(item['id']);item['agent_id']=str(item['agent_id'])
        for key in ('created_at','updated_at'):
            if item.get(key):item[key]=item[key].isoformat()
        result.append(item)
    return result


def save_trigger(profile,agent_id,data,*,trigger_id=None):
    agent=identifier(agent_id);creating=trigger_id is None
    patch=validate_trigger({key:value for key,value in data.items() if key!='expected_version'} if isinstance(data,dict) else data,creating=creating)
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,actor=_authorize(cur,profile)
            cur.execute("SELECT 1 FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid",(tenant,agent))
            if not cur.fetchone():raise TenantLinearError('Agent tapılmadı.',404)
            if any(action in patch.get('actions',[]) for action in ('create_task','update_phone','move_stage')):
                cur.execute('SELECT crm_actions_enabled FROM saas_ai_agent_settings WHERE tenant_id=%s::uuid FOR SHARE',(tenant,))
                settings=cur.fetchone() or {}
                if patch.get('enabled') and settings.get('crm_actions_enabled') is not True:
                    raise TenantLinearError('Əvvəlcə şirkət səviyyəsində AI CRM əməliyyatlarını aktiv edin.',409)
            if creating:
                trigger_uuid=uuid.uuid4()
                cur.execute('''INSERT INTO saas_ai_agent_triggers(tenant_id,id,agent_id,name,trigger_type,condition,actions,enabled)
                  VALUES(%s::uuid,%s,%s::uuid,%s,%s,%s::jsonb,%s::jsonb,%s)
                  RETURNING id,agent_id,name,trigger_type,condition,actions,enabled,version,created_at,updated_at''',
                  (tenant,trigger_uuid,agent,patch['name'],patch['trigger_type'],_json(patch['condition']),_json(patch['actions']),patch['enabled']))
                _audit(cur,tenant,actor,'ai_agent_trigger_created','ai_agent_trigger',trigger_uuid,{'type':patch['trigger_type']})
            else:
                trigger_uuid=identifier(trigger_id)
                if type(data.get('expected_version')) is not int:raise TenantLinearError('Trigger versiyası yoxdur.',409)
                cur.execute('SELECT * FROM saas_ai_agent_triggers WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND id=%s::uuid FOR UPDATE',(tenant,agent,trigger_uuid))
                previous=cur.fetchone()
                if not previous:raise TenantLinearError('Trigger tapılmadı.',404)
                if previous['version']!=data['expected_version']:raise TenantLinearError('Trigger dəyişib. Səhifəni yeniləyin.',409)
                merged={key:previous[key] for key in ('name','trigger_type','condition','actions','enabled')};merged.update(patch)
                if merged['enabled'] and any(action in merged['actions'] for action in ('create_task','update_phone','move_stage')):
                    cur.execute('SELECT crm_actions_enabled FROM saas_ai_agent_settings WHERE tenant_id=%s::uuid',(tenant,))
                    if (cur.fetchone() or {}).get('crm_actions_enabled') is not True:raise TenantLinearError('AI CRM əməliyyatları söndürülüb.',409)
                cur.execute('''UPDATE saas_ai_agent_triggers SET name=%s,trigger_type=%s,condition=%s::jsonb,actions=%s::jsonb,
                  enabled=%s,version=version+1,updated_at=now() WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND id=%s::uuid
                  RETURNING id,agent_id,name,trigger_type,condition,actions,enabled,version,created_at,updated_at''',
                  (merged['name'],merged['trigger_type'],_json(merged['condition']),_json(merged['actions']),merged['enabled'],tenant,agent,trigger_uuid))
                _audit(cur,tenant,actor,'ai_agent_trigger_updated','ai_agent_trigger',trigger_uuid,{'version':previous['version']+1})
            result=dict(cur.fetchone())
        conn.commit()
    result['id']=str(result['id']);result['agent_id']=str(result['agent_id'])
    for key in ('created_at','updated_at'):
        if result.get(key):result[key]=result[key].isoformat()
    return result


def delete_trigger(profile,agent_id,trigger_id):
    agent=identifier(agent_id);trigger=identifier(trigger_id)
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,actor=_authorize(cur,profile)
            cur.execute('DELETE FROM saas_ai_agent_triggers WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND id=%s::uuid RETURNING id',(tenant,agent,trigger))
            if not cur.fetchone():raise TenantLinearError('Trigger tapılmadı.',404)
            _audit(cur,tenant,actor,'ai_agent_trigger_deleted','ai_agent_trigger',trigger)
        conn.commit()
    return {'deleted':True}


def set_conversation_state(profile,conversation_id,state,reason=''):
    conversation=str(conversation_id or '').strip()
    if not conversation or len(conversation)>160 or state not in {'human','active','paused'}:
        raise TenantLinearError('Söhbət vəziyyəti düzgün deyil.')
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,actor=_authorize(cur,profile)
            from tenant_ai_agent_test_mode import conversation_allowed
            if not conversation_allowed(cur,tenant,conversation):
                raise TenantLinearError('Conversation is outside the verified Test Mode binding.',409)
            cur.execute('''INSERT INTO saas_ai_conversation_state(tenant_id,provider,conversation_id,state,reason)
              VALUES(%s::uuid,'kommo',%s,%s,%s) ON CONFLICT(tenant_id,provider,conversation_id) DO UPDATE
              SET state=EXCLUDED.state,reason=EXCLUDED.reason,revision=saas_ai_conversation_state.revision+1,updated_at=now()''',
              (tenant,conversation,state,str(reason)[:240]))
            if state in {'human','paused'}:
                cur.execute("UPDATE saas_tenant_webhook_events SET state='skipped',completed_at=now(),error_code='human_handoff' WHERE tenant_id=%s::uuid AND fingerprint IN (SELECT event_fingerprint FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND provider='kommo' AND conversation_id=%s AND state IN ('queued','processing'))",(tenant,tenant,conversation))
                cur.execute('''UPDATE saas_ai_agent_jobs SET state='skipped',revision=revision+1,lease_token=NULL,lease_until=NULL,
                  error_code='human_handoff',updated_at=now() WHERE tenant_id=%s::uuid AND provider='kommo' AND conversation_id=%s
                  AND state IN ('queued','processing')''',(tenant,conversation))
            _audit(cur,tenant,actor,'ai_conversation_'+state,'conversation',conversation,{'reason':str(reason)[:240]})
        conn.commit()
    return {'conversation_id':conversation,'state':state,'reason':str(reason)[:240]}


def read_conversation_state(profile,conversation_id):
    conversation=str(conversation_id or '').strip()
    if not conversation or len(conversation)>160:raise TenantLinearError('Söhbət identifikatoru düzgün deyil.')
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,_=_authorize(cur,profile)
            from tenant_ai_agent_test_mode import conversation_allowed
            if not conversation_allowed(cur,tenant,conversation):
                raise TenantLinearError('Conversation is outside the verified Test Mode binding.',404)
            cur.execute('''SELECT conversation_id,state,reason,assigned_to,last_inbound_message_id,last_agent_message_id,revision,updated_at
              FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='kommo' AND conversation_id=%s''',(tenant,conversation))
            row=cur.fetchone()
    if not row:return {'conversation_id':conversation,'state':'active','reason':'','revision':0}
    item=dict(row)
    if item.get('updated_at'):item['updated_at']=item['updated_at'].isoformat()
    return item


def review_draft(profile,draft_id,state):
    draft=identifier(draft_id)
    if state not in {'approved','rejected'}:raise TenantLinearError('Draft status düzgün deyil.')
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,actor=_authorize(cur,profile)
            cur.execute('''UPDATE saas_ai_agent_drafts d SET state=%s WHERE d.tenant_id=%s::uuid AND d.id=%s::uuid AND d.state='pending'
              AND EXISTS (SELECT 1 FROM saas_ai_agent_test_mode b JOIN saas_tenant_integrations i
                ON i.tenant_id=b.tenant_id AND i.provider='kommo' AND i.status='connected'
                WHERE b.tenant_id=d.tenant_id AND b.enabled=TRUE AND b.verified=TRUE AND b.emergency_stop=FALSE
                  AND b.account_domain=i.account_domain AND b.lead_id=d.lead_id AND b.talk_id=d.talk_id AND b.chat_id=d.chat_id)
              RETURNING id,lead_id,chat_id''',(state,tenant,draft))
            row=cur.fetchone()
            if not row:raise TenantLinearError('Draft tapılmadı və ya artıq nəzərdən keçirilib.',409)
            _audit(cur,tenant,actor,'ai_agent_draft_'+state,'ai_agent_draft',draft,{'lead_id':row['lead_id']})
        conn.commit()
    return {'id':str(row['id']),'state':state}


def reserve_draft_send(profile,draft_id):
    draft=identifier(draft_id)
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,actor=_authorize(cur,profile)
            cur.execute('''UPDATE saas_ai_agent_drafts d SET state='sending'
              FROM saas_ai_conversation_state cs,saas_ai_agents a,saas_ai_agent_settings s,saas_tenants t
              WHERE d.tenant_id=%s::uuid AND d.id=%s::uuid AND d.state='pending'
                AND a.tenant_id=d.tenant_id AND a.id=d.agent_id AND a.enabled=TRUE
                AND s.tenant_id=d.tenant_id AND s.enabled=TRUE AND t.id=d.tenant_id AND t.status='active'
                AND cs.tenant_id=d.tenant_id AND cs.provider='kommo' AND cs.conversation_id=d.chat_id
                AND cs.state='active' AND cs.last_inbound_message_id=d.source_message_id
                AND EXISTS (SELECT 1 FROM saas_ai_agent_test_mode b
                  JOIN saas_tenant_integrations i ON i.tenant_id=b.tenant_id AND i.provider='kommo' AND i.status='connected'
                  JOIN saas_tenant_webhook_events e ON e.tenant_id=d.tenant_id AND e.fingerprint=d.source_fingerprint
                  JOIN saas_ai_agent_jobs j ON j.tenant_id=d.tenant_id AND j.agent_id=d.agent_id AND j.conversation_id=d.chat_id
                  WHERE b.tenant_id=d.tenant_id AND b.enabled=TRUE AND b.verified=TRUE AND b.emergency_stop=FALSE
                    AND b.account_domain=i.account_domain AND b.lead_id=d.lead_id AND b.contact_id=CASE WHEN e.payload->>'contact_id' ~ '^[0-9]{1,18}$' THEN (e.payload->>'contact_id')::bigint END
                    AND b.talk_id=d.talk_id AND b.chat_id=d.chat_id AND b.channel_id=j.channel_id)
              RETURNING d.id,d.agent_id,d.lead_id,d.draft,d.talk_id,d.chat_id,d.source_message_id,
                (SELECT b.channel_id FROM saas_ai_agent_test_mode b WHERE b.tenant_id=d.tenant_id) AS channel_id''',(tenant,draft))
            row=cur.fetchone()
            if not row:raise TenantLinearError('Qaralama köhnəlib, menecerə verilib və ya artıq nəzərdən keçirilib.',409)
            _audit(cur,tenant,actor,'ai_agent_draft_send_reserved','ai_agent_draft',draft,{'lead_id':row['lead_id']})
        conn.commit()
    return {'tenant_id':tenant,'id':str(row['id']),'lead_id':int(row['lead_id']),'draft':str(row['draft']),
      'expected_route':{'talk_id':str(row['talk_id']),'chat_id':str(row['chat_id']),
        'message_id':str(row['source_message_id']),'channel_id':str(row.get('channel_id') or ''),
        'agent_id':str(row['agent_id'])}}


def finish_draft_send(tenant_id,draft_id,state):
    final={'accepted':'approved','unknown':'unknown'}.get(state)
    if state not in {'accepted','unknown','blocked'}:raise ValueError('Invalid draft send state')
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            if state=='blocked':
                cur.execute('''UPDATE saas_ai_agent_drafts d SET state=CASE WHEN EXISTS (
                    SELECT 1 FROM saas_ai_conversation_state cs WHERE cs.tenant_id=d.tenant_id AND cs.provider='kommo'
                      AND cs.conversation_id=d.chat_id AND cs.state='active' AND cs.last_inbound_message_id=d.source_message_id)
                    THEN 'pending' ELSE 'superseded' END
                  WHERE d.tenant_id=%s::uuid AND d.id=%s::uuid AND d.state='sending' RETURNING state''',(tenant_id,draft_id))
                row=cur.fetchone();final=row['state'] if row else 'superseded'
            else:
                cur.execute("UPDATE saas_ai_agent_drafts SET state=%s WHERE tenant_id=%s::uuid AND id=%s::uuid AND state='sending'",(final,tenant_id,draft_id))
        conn.commit()
    return final


def list_handoffs(profile,limit=50):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,_=_authorize(cur,profile)
            cur.execute('''SELECT cs.conversation_id,cs.reason,cs.updated_at,a.name AS agent_name,
              (SELECT j.lead_id FROM saas_ai_agent_jobs j WHERE j.tenant_id=cs.tenant_id AND j.provider=cs.provider
                AND j.conversation_id=cs.conversation_id ORDER BY j.updated_at DESC LIMIT 1) AS lead_id
              FROM saas_ai_conversation_state cs LEFT JOIN saas_ai_agents a ON a.tenant_id=cs.tenant_id AND a.id=cs.agent_id
              WHERE cs.tenant_id=%s::uuid AND cs.provider='kommo' AND cs.state='human'
                AND EXISTS (SELECT 1 FROM saas_ai_agent_test_mode b JOIN saas_tenant_integrations i
                  ON i.tenant_id=b.tenant_id AND i.provider='kommo' AND i.status='connected'
                  WHERE b.tenant_id=cs.tenant_id AND b.enabled=TRUE AND b.verified=TRUE AND b.emergency_stop=FALSE
                    AND b.account_domain=i.account_domain AND b.lead_id=(SELECT j.lead_id FROM saas_ai_agent_jobs j
                      WHERE j.tenant_id=cs.tenant_id AND j.provider=cs.provider AND j.conversation_id=cs.conversation_id
                      ORDER BY j.updated_at DESC LIMIT 1)
                    AND b.talk_id=(SELECT j.talk_id FROM saas_ai_agent_jobs j WHERE j.tenant_id=cs.tenant_id AND j.provider=cs.provider AND j.conversation_id=cs.conversation_id ORDER BY j.updated_at DESC LIMIT 1)
                    AND b.chat_id=cs.conversation_id)
              ORDER BY cs.updated_at DESC LIMIT %s''',(tenant,max(1,min(int(limit),100))))
            rows=cur.fetchall()
    result=[]
    for row in rows:
        item=dict(row)
        if item.get('updated_at'):item['updated_at']=item['updated_at'].isoformat()
        result.append(item)
    return result
