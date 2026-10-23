"""Tenant-scoped persistence for agent settings and knowledge sources."""
import hashlib
import json
import uuid
from datetime import datetime, timezone
from tenant_ai_dialog_scope import channel_scope, lock_dialog, assert_resolved

from tenant_ai_agents_policy import (MAX_AGENTS, MAX_SOURCES, agent as validate_agent,
    company_settings, knowledge as validate_knowledge, chunk_text, trigger as validate_trigger, safety_policy,
    setup_key as validate_setup_key)
from tenant_ai_agents_schema import ensure_schema
from tenant_linear_policy import TenantLinearError, identifier
from tenant_ai_agent_access import tenant_status_allows_ai, tenant_status_allows_management
from tenant_ai_agent_runtime_flags import enabled as runtime_flag
from tenant_ai_agent_key_policy import (platform_key_ready, platform_model,
    platform_model_choices, platform_model_allowed, platform_service_ready)
from tenant_platform import _connect, _json


def _support_case_extraction_runtime_enabled(tenant_id=None):
    if not runtime_flag('SUPPORT_CASES_LIVE_EXTRACTION_ENABLED'):
        return False
    # The existing exact pilot allowlist remains a second server-side boundary.
    from tenant_knowledge_live_queue import pilot_scope
    scope=pilot_scope()
    return bool(scope and (tenant_id is None or str(scope.get('tenant_id'))==str(tenant_id)))


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
    if not row:
        raise TenantLinearError('Bu hesabın seçilmiş şirkətdə aktiv üzvlüyü yoxdur. Şirkəti dəyişin və ya yenidən daxil olun.', 403)
    if not row.get('active'):
        raise TenantLinearError('Bu şirkətdəki üzvlüyünüz deaktiv edilib. Şirkət sahibindən girişinizi yoxlamağı xahiş edin.', 403)
    if not tenant_status_allows_management(row.get('status')):
        raise TenantLinearError('Bu şirkət arxivdədir və ya dayandırılıb. AI Agents yalnız aktiv şirkətdə idarə oluna bilər.', 403)
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
    for key in ('id', 'tenant_id', 'agent_id'):
        if key in value and value[key] is not None:
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
            if (str(a.get('provider') or 'kommo')==str(b.get('provider') or 'kommo')
                    and str(a.get('pipeline_id') or '')==str(b.get('pipeline_id') or '')
                    and str(a.get('stage_id') or '')==str(b.get('stage_id') or '')):
                channel_a=str(a.get('channel_id') or '').casefold()
                channel_b=str(b.get('channel_id') or '').casefold()
                if not channel_a or not channel_b or channel_a==channel_b:
                    return True
    return False


def _explicit_assignment_separates(cur,tenant,agent_id,other,selected_scopes):
    """Existing exact dialogue bindings/group coordinator can arbitrate shared scopes.

    Unbound private dialogues with multiple current candidates fail closed in admission.
    A group still needs its own coordinator; this does not grant any group authority.
    """
    if not agent_id: return False
    checked=False
    for left in selected_scopes:
        for right in other.get('scopes') or []:
            if not _scopes_overlap([left],[right]): continue
            if left.get('provider','kommo')!='connect' or not left.get('channel_id'): return False
            channel=left['channel_id'];checked=True
            cur.execute("""SELECT DISTINCT agent_id::text AS agent FROM saas_ai_conversation_state
              WHERE tenant_id=%s::uuid AND provider='connect' AND channel_id=%s AND agent_assignment_id IS NOT NULL
              AND agent_id IN (%s::uuid,%s::uuid)""",(tenant,channel,str(agent_id),str(other['id'])))
            if {r['agent'] for r in cur.fetchall()}=={str(agent_id),str(other['id'])}: continue
            if left.get('allow_group_messages') and right.get('allow_group_messages'):
                cur.execute("SELECT assignments FROM saas_ai_group_coordination WHERE tenant_id=%s::uuid AND provider='connect'",(tenant,))
                safe=False
                for row in cur.fetchall():
                    slots=[x for x in row['assignments'] if x.get('channel_id')==channel and x.get('agent_id') in {str(agent_id),str(other['id'])}]
                    if len(slots)==2 and len({x['agent_id'] for x in slots})==2 and slots[0]['priority']!=slots[1]['priority']: safe=True
                if safe: continue
            return False
    return checked


def _ensure_no_active_scope_overlap(cur, tenant, selected_scopes, exclude_agent=None, schedule=None):
    cur.execute('''SELECT id,scopes,schedule FROM saas_ai_agents WHERE tenant_id=%s::uuid AND enabled=TRUE AND role='primary'
      AND (%s::uuid IS NULL OR id<>%s::uuid)''', (tenant,exclude_agent,exclude_agent))
    from tenant_ai_agent_schedule import overlaps
    if any(_scopes_overlap(selected_scopes,row.get('scopes')) and overlaps(schedule or {},row.get('schedule') or {})
           and not _explicit_assignment_separates(cur,tenant,exclude_agent,row,selected_scopes) for row in list(cur.fetchall())):
        raise TenantLinearError('AI_PRIMARY_SCHEDULE_CONFLICT: overlapping active primary scopes and shifts.',409)


def read_settings(profile):
    from tenant_connect_transport import _schema as ensure_connect_schema
    with _connect() as conn:
        ensure_schema(conn)
        ensure_connect_schema(conn)
        with conn.cursor() as cur:
            tenant, _ = _authorize(cur, profile)
            # Older production schemas may still require operating_mode.
            # A newly materialized settings row must start in the safe Manual
            # mode; ON CONFLICT keeps every existing company setting intact.
            cur.execute('''INSERT INTO saas_ai_agent_settings(tenant_id,operating_mode) VALUES(%s::uuid,'manual')
              ON CONFLICT(tenant_id) DO NOTHING''', (tenant,))
            cur.execute('''SELECT company_instructions,enabled,emergency_stop,auto_send_enabled,crm_actions_enabled,native_crm_actions_enabled,
                operating_mode,support_case_extraction_enabled,support_case_extraction_started_at,max_replies_per_chat,updated_at
              FROM saas_ai_agent_settings WHERE tenant_id=%s::uuid''', (tenant,))
            result = _row(cur.fetchone())
            result['configured_operating_mode']=result.get('operating_mode')
            # NULL is the compatibility state for companies configured before
            # the company-level selector existed. Derive its current effective
            # behavior without rewriting any existing agent or company setting.
            if not result.get('operating_mode'):
                cur.execute("SELECT to_regclass('saas_ai_simulation_configs') IS NOT NULL AS available")
                sim_available=bool((cur.fetchone() or {}).get('available'))
                sim_row={}
                if sim_available:
                    cur.execute("SELECT EXISTS(SELECT 1 FROM saas_ai_simulation_configs WHERE tenant_id=%s::uuid AND enabled=TRUE AND state='running') AS running",(tenant,))
                    sim_row=cur.fetchone() or {}
                if sim_row.get('running') is True:
                    result['operating_mode']='simulation'
                else:
                    cur.execute("SELECT EXISTS(SELECT 1 FROM saas_ai_agents WHERE tenant_id=%s::uuid AND role='primary' AND enabled=TRUE AND mode='automatic') AS automatic",(tenant,))
                    automatic=(cur.fetchone() or {}).get('automatic') is True
                    active_ready=bool(result.get('enabled') and result.get('auto_send_enabled')
                                      and runtime_flag('AI_AGENTS_AUTO_SEND_ENABLED') and automatic)
                    result['operating_mode']='active' if active_ready else 'manual'
            result['server_native_crm_actions_enabled']=runtime_flag('AI_NATIVE_CRM_ACTIONS_ENABLED')
            # This summary is intentionally scoped by the already-authorized
            # tenant.  It lets the browser distinguish a saved company switch
            # from a missing Connect route without exposing other companies.
            cur.execute('''SELECT
                EXISTS(SELECT 1 FROM saas_tenant_integrations WHERE tenant_id=%s::uuid
                  AND provider='connect' AND status='connected') AS connect_connected,
                (SELECT count(*) FROM saas_connect_channels WHERE tenant_id=%s::uuid AND active=TRUE) AS active_connect_channels,
                (SELECT count(*) FROM saas_ai_agents WHERE tenant_id=%s::uuid AND enabled=TRUE) AS enabled_agents,
                (SELECT metadata->>'daily_limit' FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='chat_ai') AS daily_ai_limit''',
                (tenant, tenant, tenant, tenant))
            runtime = dict(cur.fetchone() or {})
            from tenant_ai_agent_key_policy import platform_token_limit
            runtime['platform_token_limit']=platform_token_limit(tenant)
            result['runtime'] = {
                'tenant_id': tenant,
                'daily_ai_limit':int(runtime.get('daily_ai_limit') or 50),
                'platform_token_limit':runtime['platform_token_limit'],
                'connect_connected': runtime.get('connect_connected') is True,
                'active_connect_channels': int(runtime.get('active_connect_channels') or 0),
                'enabled_agents': int(runtime.get('enabled_agents') or 0),
                'simulation_runtime_enabled': runtime_flag('AI_SIMULATION_ENABLED'),
                'simulation_runtime_block_reason': '' if runtime_flag('AI_SIMULATION_ENABLED') else 'global_feature_flag_disabled',
                'support_case_extraction_runtime_enabled': _support_case_extraction_runtime_enabled(tenant),
                'support_case_extraction_block_reason': '' if _support_case_extraction_runtime_enabled(tenant) else 'server_or_pilot_scope_disabled',
            }
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
            if any(patch.get(key) is True for key in ('auto_send_enabled','crm_actions_enabled','native_crm_actions_enabled')) and profile.get('role') != 'owner':
                raise TenantLinearError('Avtomatik göndərmə və CRM yazılarını yalnız şirkət sahibi aça bilər.',403)
            if patch.get('enabled') is True and not platform_service_ready():
                cur.execute("SELECT secrets,metadata FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='chat_ai'", (tenant,))
                integration = cur.fetchone() or {}
                metadata = integration.get('metadata') or {}
                if not integration.get('secrets') or metadata.get('enabled') is not True or metadata.get('consent') is not True:
                    raise TenantLinearError('Əvvəlcə mövcud Chat AI ayarlarını və razılığı tamamlayın.', 409)
            # Keep the compatibility insert safe for NOT NULL legacy schemas.
            # This only supplies a default for a missing row; existing mode and
            # auto-send state are protected by ON CONFLICT DO NOTHING.
            cur.execute('''INSERT INTO saas_ai_agent_settings(tenant_id,operating_mode) VALUES(%s::uuid,'manual')
              ON CONFLICT(tenant_id) DO NOTHING''', (tenant,))
            cur.execute('''SELECT company_instructions,enabled,emergency_stop,auto_send_enabled,crm_actions_enabled,native_crm_actions_enabled,
                operating_mode,support_case_extraction_enabled,support_case_extraction_started_at,max_replies_per_chat FROM saas_ai_agent_settings
              WHERE tenant_id=%s::uuid FOR UPDATE''', (tenant,))
            previous = cur.fetchone() or {}
            values = {'company_instructions': str(previous.get('company_instructions') or ''),
                      **{key: bool(previous.get(key, False)) for key in ('enabled','auto_send_enabled','crm_actions_enabled','native_crm_actions_enabled')},
                      'emergency_stop': bool(previous.get('emergency_stop',False)),
                      'operating_mode': previous.get('operating_mode'),
                      'support_case_extraction_enabled': bool(previous.get('support_case_extraction_enabled',False)),
                      'support_case_extraction_started_at': previous.get('support_case_extraction_started_at'),
                      'max_replies_per_chat': int(previous.get('max_replies_per_chat') or 20)}
            values.update(patch)
            if values.get('operating_mode')=='simulation' and not runtime_flag('AI_SIMULATION_ENABLED'):
                raise TenantLinearError('Simulation hazırda serverdə söndürülüb; şirkət rejimi dəyişdirilmədi.',409)
            if values.get('support_case_extraction_enabled') and not _support_case_extraction_runtime_enabled(tenant):
                raise TenantLinearError('Dəstək case-lərinin avtomatik təhlili serverdə söndürülüb; parametr aktivləşdirilmədi.',409)
            was_case_extraction=bool(previous.get('support_case_extraction_enabled'))
            if values.get('support_case_extraction_enabled') and not was_case_extraction:
                values['support_case_extraction_started_at']=datetime.now(timezone.utc)
            elif not values.get('support_case_extraction_enabled'):
                values['support_case_extraction_started_at']=None
            cur.execute('''UPDATE saas_ai_agent_settings SET company_instructions=%s,enabled=%s,emergency_stop=%s,
              auto_send_enabled=%s,crm_actions_enabled=%s,native_crm_actions_enabled=%s,operating_mode=%s,
              support_case_extraction_enabled=%s,support_case_extraction_started_at=%s,
              max_replies_per_chat=%s,updated_by=%s,updated_at=now() WHERE tenant_id=%s::uuid
              RETURNING company_instructions,enabled,emergency_stop,auto_send_enabled,crm_actions_enabled,native_crm_actions_enabled,
                operating_mode,support_case_extraction_enabled,support_case_extraction_started_at,max_replies_per_chat,updated_at''',
              (values['company_instructions'], values['enabled'], values['emergency_stop'], values['auto_send_enabled'], values['crm_actions_enabled'], values['native_crm_actions_enabled'],
               values['operating_mode'],values['support_case_extraction_enabled'],values['support_case_extraction_started_at'],
               values['max_replies_per_chat'], actor, tenant))
            result = _row(cur.fetchone())
            if (('operating_mode' in patch and values.get('operating_mode')!='active')
                    or values['emergency_stop'] or not values['enabled']):
                cur.execute("UPDATE saas_tenant_webhook_events SET state='skipped',completed_at=now(),error_code='company_mode_not_active' WHERE tenant_id=%s::uuid AND state IN ('queued','processing') AND fingerprint IN (SELECT event_fingerprint FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND state IN ('queued','processing'))",(tenant,tenant))
                cur.execute("UPDATE saas_ai_agent_jobs SET state='skipped',revision=revision+1,lease_token=NULL,lease_until=NULL,error_code='company_mode_not_active',updated_at=now() WHERE tenant_id=%s::uuid AND state IN ('queued','processing')",(tenant,))
            if 'operating_mode' in patch and values.get('operating_mode')!='simulation':
                cur.execute("SELECT to_regclass('saas_ai_simulation_queue') IS NOT NULL AS available")
                if (cur.fetchone() or {}).get('available'):
                    cur.execute("UPDATE saas_ai_simulation_queue SET state='skipped',error_code='company_mode_changed',completed_at=now(),lease_token=NULL,lease_until=NULL WHERE tenant_id=%s::uuid AND state='queued'",(tenant,))
                cur.execute("SELECT to_regclass('saas_ai_simulation_configs') IS NOT NULL AS available")
                if (cur.fetchone() or {}).get('available'):
                    cur.execute("UPDATE saas_ai_simulation_configs SET enabled=FALSE,state='stopped',updated_by=%s,updated_at=now(),version=version+1 WHERE tenant_id=%s::uuid AND enabled=TRUE",(actor,tenant))
            if was_case_extraction and not values.get('support_case_extraction_enabled'):
                cur.execute("SELECT to_regclass('saas_knowledge_live_queue') IS NOT NULL AS available")
                if (cur.fetchone() or {}).get('available'):
                    cur.execute("UPDATE saas_knowledge_live_queue SET state='blocked',lease_until=NULL,error_code='live_extraction_disabled',updated_at=now() WHERE tenant_id=%s::uuid AND state='pending'",(tenant,))
            # A company-wide stop revokes work already admitted by any agent mode.
            if values['emergency_stop'] or not values['enabled']:
                cur.execute("UPDATE saas_tenant_webhook_events SET state='skipped',completed_at=now(),error_code='ai_emergency_stop' WHERE tenant_id=%s::uuid AND state IN ('queued','processing') AND fingerprint IN (SELECT event_fingerprint FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND state IN ('queued','processing'))", (tenant, tenant))
                cur.execute("UPDATE saas_ai_agent_jobs SET state='skipped',revision=revision+1,lease_token=NULL,lease_until=NULL,error_code='ai_emergency_stop',updated_at=now() WHERE tenant_id=%s::uuid AND state IN ('queued','processing')", (tenant,))
            _audit(cur, tenant, actor, 'ai_agents_settings_updated', 'ai_agents', '', {
                'enabled': values['enabled'],'operating_mode':values['operating_mode'],
                'support_case_extraction_enabled':values['support_case_extraction_enabled']})
        conn.commit()
    # Return the same tenant-scoped effective state as GET, including runtime
    # gates used by the company-mode controls.
    return read_settings(profile)


def list_agents(profile):
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, _ = _authorize(cur, profile)
            cur.execute('''SELECT id,tenant_id,name,instructions,enabled,mode,language,style,delay_seconds,
              schedule,scopes,model,safety,response_policy,role,expert_policy,version,created_at,updated_at FROM saas_ai_agents
              WHERE tenant_id=%s::uuid ORDER BY created_at,id''', (tenant,))
            rows = [_row(row) for row in cur.fetchall()]
    return rows


def configuration_options(profile):
    """Tenant-scoped labels for the agent editor; never trusts browser IDs."""
    # Connect was introduced after the AI editor.  Ensure its additive tables
    # exist even for tenants that have never opened Connect, so the editor
    # remains available without a Kommo integration.
    from tenant_connect_transport import _schema as ensure_connect_schema
    with _connect() as conn:
        ensure_schema(conn)
        ensure_connect_schema(conn)
        with conn.cursor() as cur:
            tenant, _ = _authorize(cur, profile)
            cur.execute("SELECT pipeline_id,name FROM saas_tenant_pipelines WHERE tenant_id=%s::uuid AND active=TRUE ORDER BY name,pipeline_id",(tenant,))
            pipelines=[dict(r) for r in cur.fetchall()]
            cur.execute("SELECT pipeline_id,stage_id,name FROM saas_tenant_pipeline_stages WHERE tenant_id=%s::uuid ORDER BY pipeline_id,sort_order,stage_id",(tenant,))
            stages=[dict(r) for r in cur.fetchall()]
            cur.execute("SELECT metadata,secrets FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='chat_ai'",(tenant,))
            chat=cur.fetchone() or {}; meta=chat.get('metadata') or {}
            # Kommo channel machine IDs are discovered from tenant-owned
            # inbound jobs only.  We never accept a browser-supplied channel.
            cur.execute('''SELECT DISTINCT channel_id AS id,channel_id AS name
              FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND provider='kommo' AND channel_id<>''
              ORDER BY channel_id''', (tenant,))
            channels=[dict(row) for row in cur.fetchall()]
            cur.execute('''SELECT connect_channel_id AS id,name FROM saas_connect_channels
              WHERE tenant_id=%s::uuid AND active=TRUE ORDER BY name,connect_channel_id''', (tenant,))
            connect_channels=[{'id':str(row['id']),'name':str(row.get('name') or row['id'])} for row in cur.fetchall()]
            platform_available=platform_service_ready()
    configured=str(meta.get('model') or '').strip()
    company_models=meta.get('models') if isinstance(meta.get('models'),list) else []
    company_models=[str(x).strip() for x in company_models if isinstance(x,str) and x.strip()]
    if configured and configured not in company_models: company_models.insert(0,configured)
    models = platform_model_choices() if platform_available else []
    model_ids = {item['id'] for item in models}
    for item in company_models:
        if item not in model_ids:
            models.append({'id':item,'label':item,'source':'company'})
            model_ids.add(item)
    company_ready=bool(chat.get('secrets') and meta.get('enabled') is True and meta.get('consent') is True)
    permitted=bool(platform_available or company_ready)
    visible_models=models if permitted else []
    return {'pipelines':pipelines,'stages':stages,'channels':channels,'connect_channels':connect_channels,
            'models':visible_models,
            'model_catalog':{'state':'ready' if visible_models else 'unavailable',
              'source':'platform' if platform_available else ('company' if company_ready else ''),
              'reason':'' if visible_models else ('no_models_configured' if permitted else 'chat_ai_provider_not_ready')}}


def refresh_configuration_catalog(profile, remote_pipelines):
    """Mirror the tenant's complete live Kommo catalogue for agent scope picks.

    The previous editor read only the workflow selections, so an agent's
    already-saved scope could accidentally become the only visible catalogue.
    A live Kommo read is the source of truth; it never changes any agent scope.
    """
    normalized = []
    for pipeline in remote_pipelines if isinstance(remote_pipelines, list) else []:
        pipeline_id = str((pipeline or {}).get('id') or '').strip()
        if not pipeline_id:
            continue
        statuses = ((pipeline or {}).get('_embedded') or {}).get('statuses') or []
        normalized.append((pipeline_id, str((pipeline or {}).get('name') or pipeline_id)[:180], statuses))
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, _ = _authorize(cur, profile)
            current_ids = set()
            for pipeline_id, name, statuses in normalized:
                current_ids.add(pipeline_id)
                cur.execute('''INSERT INTO saas_tenant_pipelines(tenant_id,pipeline_id,name,active,settings)
                  VALUES(%s::uuid,%s,%s,TRUE,'{}'::jsonb)
                  ON CONFLICT(tenant_id,pipeline_id) DO UPDATE SET name=EXCLUDED.name,active=TRUE,updated_at=now()''',
                            (tenant, pipeline_id, name))
                for position, stage in enumerate(statuses):
                    stage_id = str((stage or {}).get('id') or '').strip()
                    if not stage_id:
                        continue
                    cur.execute('''INSERT INTO saas_tenant_pipeline_stages(tenant_id,pipeline_id,stage_id,name,sort_order,stage_type,settings)
                      VALUES(%s::uuid,%s,%s,%s,%s,%s,'{}'::jsonb)
                      ON CONFLICT(tenant_id,pipeline_id,stage_id) DO UPDATE SET name=EXCLUDED.name,sort_order=EXCLUDED.sort_order,updated_at=now()''',
                                (tenant, pipeline_id, stage_id, str((stage or {}).get('name') or stage_id)[:180],
                                 int((stage or {}).get('sort') or position), str((stage or {}).get('type') or 'open')[:40]))
            if current_ids:
                cur.execute('UPDATE saas_tenant_pipelines SET active=FALSE,updated_at=now() WHERE tenant_id=%s::uuid AND NOT (pipeline_id=ANY(%s))',
                            (tenant, list(current_ids)))
        conn.commit()
    return configuration_options(profile)


def save_agent(profile, data, *, agent_id=None):
    creating = agent_id is None
    clean_data = {key: value for key, value in data.items() if key != 'expected_version'} if isinstance(data, dict) else data
    patch = validate_agent(clean_data, creating=creating)
    if not patch:
        raise TenantLinearError('Dəyişdiriləcək agent ayarı yoxdur.')
    if not creating and type(data.get('expected_version')) is not int:
        raise TenantLinearError('Agent versiyası yoxdur.', 409)
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, actor = _authorize(cur, profile)
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('ai-agent-config:'+tenant,))
            # A retry after a browser/network interruption must return the
            # original disabled agent.  The key is also employee-scoped so an
            # administrator cannot accidentally resume another employee's
            # unfinished wizard.
            if creating and patch.get('setup_key'):
                cur.execute('''SELECT id,tenant_id,name,instructions,enabled,mode,language,style,delay_seconds,
                  schedule,scopes,model,safety,response_policy,role,expert_policy,version,created_at,updated_at FROM saas_ai_agents
                  WHERE tenant_id=%s::uuid AND created_by=%s AND setup_key=%s FOR SHARE''',
                            (tenant, actor, patch['setup_key']))
                existing = _row(cur.fetchone())
                if existing:
                    return existing
                # The original version of the night-operator wizard did not
                # send a setup key.  Match only one *identical*, disabled,
                # employee-owned draft and attach the key to it.  This makes
                # the failed historical flow recoverable without deleting or
                # guessing at any existing agent.
                cur.execute('''SELECT id,tenant_id,name,instructions,enabled,mode,language,style,delay_seconds,
                  schedule,scopes,model,safety,response_policy,role,expert_policy,version,created_at,updated_at FROM saas_ai_agents
                  WHERE tenant_id=%s::uuid AND created_by=%s AND setup_key IS NULL
                    AND role='primary' AND expert_policy='{}'::jsonb AND %s='primary' AND %s::jsonb='{}'::jsonb
                    AND name=%s AND instructions=%s AND enabled=%s AND mode=%s AND language=%s AND style=%s
                    AND delay_seconds=%s AND schedule=%s::jsonb AND scopes=%s::jsonb AND model=%s AND safety=%s::jsonb
                  ORDER BY created_at DESC LIMIT 2 FOR UPDATE''',
                            (tenant, actor, patch['role'], _json(patch['expert_policy']), patch['name'], patch['instructions'], patch['enabled'], patch['mode'],
                             patch['language'], patch['style'], patch['delay_seconds'], _json(patch['schedule']),
                             _json(patch['scopes']), patch['model'], _json(patch['safety'])))
                legacy = cur.fetchall()
                if len(legacy) == 1:
                    cur.execute('''UPDATE saas_ai_agents SET setup_key=%s
                      WHERE tenant_id=%s::uuid AND id=%s::uuid AND setup_key IS NULL
                      RETURNING id,tenant_id,name,instructions,enabled,mode,language,style,delay_seconds,
                        schedule,scopes,model,safety,response_policy,role,expert_policy,version,created_at,updated_at''',
                                (patch['setup_key'], tenant, legacy[0]['id']))
                    recovered = _row(cur.fetchone())
                    if recovered:
                        _audit(cur, tenant, actor, 'ai_agent_setup_recovered', 'ai_agent', recovered['id'])
                        conn.commit()
                        return recovered
            if 'model' in patch:
                cur.execute("SELECT metadata,secrets FROM saas_tenant_integrations WHERE tenant_id=%s::uuid AND provider='chat_ai'", (tenant,))
                integration = cur.fetchone() or {}
                config = integration.get('metadata') or {}
                allowed_models = [str(item).strip() for item in config.get('models', []) if isinstance(item, str) and item.strip()]
                configured = str(config.get('model') or '').strip()
                if configured and configured not in allowed_models:
                    allowed_models.append(configured)
                requested = str(patch.get('model') or '')
                platform_selected = platform_service_ready() and (not requested or platform_model_allowed(requested))
                company_selected = requested in allowed_models
                if not platform_selected and (not integration.get('secrets') or config.get('enabled') is not True
                                              or config.get('consent') is not True or not company_selected):
                    raise TenantLinearError('Model yalnız aktiv mövcud Chat AI provayderindən seçilə bilər.', 409)
            for scope in patch.get('scopes', []):
                if scope.get('provider', 'kommo') == 'connect':
                    cur.execute('''SELECT 1 FROM saas_connect_channels
                      WHERE tenant_id=%s::uuid AND connect_channel_id=%s::uuid AND active=TRUE''',
                                (tenant, scope['channel_id']))
                else:
                    cur.execute('''SELECT 1 FROM saas_tenant_pipelines p
                      JOIN saas_tenant_pipeline_stages s ON s.tenant_id=p.tenant_id AND s.pipeline_id=p.pipeline_id
                      WHERE p.tenant_id=%s::uuid AND p.pipeline_id=%s AND s.stage_id=%s
                        AND p.active=TRUE''', (tenant, scope['pipeline_id'], scope['stage_id']))
                if not cur.fetchone():
                    raise TenantLinearError('Seçilmiş mənbə və ya kanal bu şirkətdə aktiv deyil.')
            if creating:
                from tenant_ai_expert_policy import validate_binding
                validate_binding(cur,tenant,None,patch)
                if patch['response_policy']['enabled']:
                    if patch['role']!='primary':
                        raise TenantLinearError('No-answer behavior is configured only on a Primary agent.',409)
                    if patch['response_policy']['action']=='call_expert':
                        from tenant_ai_expert_policy import helper_ids
                        if not helper_ids(patch.get('expert_policy') or {}):
                            raise TenantLinearError('Bind an allowed internal Expert before choosing the Expert no-answer action.',409)
                cur.execute('SELECT count(*) AS total FROM saas_ai_agents WHERE tenant_id=%s::uuid', (tenant,))
                if int((cur.fetchone() or {}).get('total') or 0) >= MAX_AGENTS:
                    raise TenantLinearError('Agent sayı həddə çatıb.')
                if patch['enabled']:
                    _ensure_no_active_scope_overlap(cur,tenant,patch['scopes'],schedule=patch['schedule'])
                    cur.execute('''SELECT s.enabled,i.secrets,i.metadata FROM saas_ai_agent_settings s
                      LEFT JOIN saas_tenant_integrations i ON i.tenant_id=s.tenant_id AND i.provider='chat_ai'
                      WHERE s.tenant_id=%s::uuid''', (tenant,))
                    readiness = cur.fetchone() or {}
                    metadata = readiness.get('metadata') or {}
                    platform_selected = platform_service_ready() and (not patch.get('model') or platform_model_allowed(patch.get('model')))
                    if readiness.get('enabled') is not True or (not platform_selected and (not readiness.get('secrets') or metadata.get('enabled') is not True or metadata.get('consent') is not True)):
                        raise TenantLinearError('Əvvəlcə AI Agents və mövcud Chat AI ayarlarını tamamlayın.', 409)
                agent_uuid = uuid.uuid4()
                cur.execute('''INSERT INTO saas_ai_agents(id,tenant_id,name,instructions,enabled,mode,language,style,
                  delay_seconds,schedule,scopes,model,safety,response_policy,created_by,setup_key,role,expert_policy) VALUES(%s,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s::jsonb)
                  RETURNING id,tenant_id,name,instructions,enabled,mode,language,style,delay_seconds,schedule,scopes,model,safety,response_policy,role,expert_policy,version,created_at,updated_at''',
                  (agent_uuid, tenant, patch['name'], patch['instructions'], patch['enabled'], patch['mode'], patch['language'], patch['style'],
                   patch['delay_seconds'], _json(patch['schedule']), _json(patch['scopes']), patch['model'], _json(patch['safety']), _json(patch['response_policy']), actor,
                   patch.get('setup_key'),patch['role'],_json(patch['expert_policy'])))
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
                merged = {key: previous.get(key) for key in ('name','instructions','enabled','mode','language','style','delay_seconds','schedule','scopes','model','safety','response_policy','role','expert_policy')}
                merged.update(patch)
                from tenant_ai_expert_policy import validate_binding
                validate_binding(cur,tenant,agent_uuid,merged,previous)
                merged['safety'] = safety_policy(merged.get('safety'))
                from tenant_ai_agent_no_answer import validate_policy
                merged['response_policy'] = validate_policy(merged.get('response_policy'))
                if merged['response_policy']['enabled']:
                    if merged['role']!='primary':
                        raise TenantLinearError('No-answer behavior is configured only on a Primary agent.',409)
                    if merged['response_policy']['action']=='call_expert':
                        from tenant_ai_expert_policy import helper_ids
                        if not helper_ids(merged.get('expert_policy') or {}):
                            raise TenantLinearError('Bind an allowed internal Expert before choosing the Expert no-answer action.',409)
                if merged['enabled']:
                    _ensure_no_active_scope_overlap(cur,tenant,merged['scopes'],exclude_agent=agent_uuid,schedule=merged['schedule'])
                if merged['enabled'] and not previous['enabled']:
                    cur.execute('''SELECT s.enabled,i.secrets,i.metadata FROM saas_ai_agent_settings s
                      LEFT JOIN saas_tenant_integrations i ON i.tenant_id=s.tenant_id AND i.provider='chat_ai'
                      WHERE s.tenant_id=%s::uuid''', (tenant,))
                    readiness = cur.fetchone() or {}
                    metadata = readiness.get('metadata') or {}
                    platform_selected = platform_service_ready() and (not merged.get('model') or platform_model_allowed(merged.get('model')))
                    if readiness.get('enabled') is not True or (not platform_selected and (not readiness.get('secrets') or metadata.get('enabled') is not True or metadata.get('consent') is not True)):
                        raise TenantLinearError('Əvvəlcə AI Agents və mövcud Chat AI ayarlarını tamamlayın.', 409)
                cur.execute('''UPDATE saas_ai_agents SET name=%s,instructions=%s,enabled=%s,mode=%s,language=%s,
                  style=%s,delay_seconds=%s,schedule=%s::jsonb,scopes=%s::jsonb,model=%s,safety=%s::jsonb,response_policy=%s::jsonb,role=%s,expert_policy=%s::jsonb,version=version+1,updated_at=now()
                  WHERE tenant_id=%s::uuid AND id=%s::uuid
                  RETURNING id,tenant_id,name,instructions,enabled,mode,language,style,delay_seconds,schedule,scopes,model,safety,response_policy,role,expert_policy,version,created_at,updated_at''',
                  (merged['name'], merged['instructions'], merged['enabled'], merged['mode'], merged['language'], merged['style'], merged['delay_seconds'],
                  _json(merged['schedule']), _json(merged['scopes']), merged['model'], _json(merged['safety']), _json(merged['response_policy']), merged['role'],_json(merged['expert_policy']),tenant, agent_uuid))
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
            # Serialize with explicit assignment's shared agent lock. Cascading
            # deletion must not erase its Human Mode and allow another agent in.
            cur.execute('SELECT id FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE', (tenant,agent_uuid))
            if not cur.fetchone():
                raise TenantLinearError('Agent tapılmadı.', 404)
            cur.execute('''SELECT 1 FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid
              AND provider='connect' AND agent_id=%s::uuid AND agent_assignment_id IS NOT NULL LIMIT 1''', (tenant,agent_uuid))
            if cur.fetchone():
                raise TenantLinearError('Агент явно назначен диалогам. Выключите его вместо удаления, чтобы сохранить их состояние.',409)
            cur.execute('SELECT 1 FROM saas_ai_test_sessions WHERE tenant_id=%s::uuid AND agent_id=%s::uuid LIMIT 1', (tenant,agent_uuid))
            if cur.fetchone():
                raise TenantLinearError('У агента есть история виртуальных тестов. Выключите агента вместо удаления, чтобы сохранить историю и учёт лимитов.',409)
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
                cur.execute('''SELECT s.id,s.tenant_id,s.agent_id,a.name AS agent_name,s.title,s.kind,s.content,s.active,s.content_hash,s.created_by,s.provenance,
                    CASE WHEN s.provenance ? 'instruction_id' THEN 'instruction'
                         WHEN s.provenance ? 'candidate_id' THEN 'support_case' ELSE 'knowledge_source' END AS origin_type,
                    COALESCE(s.provenance->>'instruction_id',s.provenance->>'candidate_id') AS origin_id,
                    s.created_at,s.updated_at
                  FROM saas_ai_knowledge_sources s LEFT JOIN saas_ai_agents a ON a.tenant_id=s.tenant_id AND a.id=s.agent_id
                  WHERE s.tenant_id=%s::uuid AND s.id=%s::uuid''', (tenant, source))
                row = _row(cur.fetchone())
                if not row:
                    raise TenantLinearError('Bilik mənbəyi tapılmadı.', 404)
                return _enrich_knowledge_rows(cur,[row],tenant)[0]
            cur.execute('''SELECT s.id,s.tenant_id,s.agent_id,a.name AS agent_name,s.title,s.kind,s.active,s.content_hash,s.created_by,s.provenance,
              CASE WHEN s.provenance ? 'instruction_id' THEN 'instruction'
                   WHEN s.provenance ? 'candidate_id' THEN 'support_case' ELSE 'knowledge_source' END AS origin_type,
              COALESCE(s.provenance->>'instruction_id',s.provenance->>'candidate_id') AS origin_id,s.created_at,s.updated_at,
              (SELECT count(*) FROM saas_ai_knowledge_chunks c WHERE c.tenant_id=s.tenant_id AND c.source_id=s.id) AS chunks
              FROM saas_ai_knowledge_sources s LEFT JOIN saas_ai_agents a ON a.tenant_id=s.tenant_id AND a.id=s.agent_id
              WHERE s.tenant_id=%s::uuid ORDER BY s.created_at DESC,s.id''', (tenant,))
            rows=[_row(row) for row in cur.fetchall()]
            return _enrich_knowledge_rows(cur,rows,tenant)


def _enrich_knowledge_rows(cur,rows,tenant):
    parsed=[];candidate_ids=set();instruction_ids=set()
    for row in rows:
        provenance=row.get('provenance') or {}
        if isinstance(provenance,str):
            try: provenance=json.loads(provenance)
            except (TypeError,ValueError): provenance={}
        if not isinstance(provenance,dict): provenance={}
        if provenance.get('candidate_id'): candidate_ids.add(str(provenance['candidate_id']))
        if provenance.get('instruction_id'): instruction_ids.add(str(provenance['instruction_id']))
        parsed.append((row,provenance))
    candidates={}
    if candidate_ids:
        cur.execute("SELECT id::text AS id,status,reviewed_by FROM saas_knowledge_candidates WHERE tenant_id=%s::uuid AND id=ANY(%s::uuid[])",(tenant,list(candidate_ids)))
        candidates={str(item['id']):item for item in cur.fetchall()}
    instructions={};cur.execute("SELECT to_regclass('saas_instruction_lessons') IS NOT NULL AS available")
    if instruction_ids and bool((cur.fetchone() or {}).get('available')):
        cur.execute("SELECT id::text AS id,state,published_doc->>'review_status' AS review_status FROM saas_instruction_lessons WHERE tenant_id=%s::uuid AND id::text=ANY(%s::text[])",(tenant,list(instruction_ids)))
        instructions={str(item['id']):item for item in cur.fetchall()}
    for row,provenance in parsed:
        origin_id=str(provenance.get('instruction_id') or provenance.get('candidate_id') or '')
        approved_by=str(provenance.get('approved_by') or '')
        origin_type=row.get('origin_type')
        review='not_reviewed';published=False;ai_available=False
        if origin_type=='support_case':
            candidate=candidates.get(origin_id) or {};review=str(candidate.get('status') or 'missing')
            confirmed=bool(candidate.get('reviewed_by') is not None and approved_by and str(candidate.get('reviewed_by'))==approved_by and review=='approved')
            published=confirmed and row.get('active') is True
            ai_available=published
        elif origin_type=='instruction':
            lesson=instructions.get(origin_id) or {};review_status=str(provenance.get('review_status') or 'not_reviewed')
            review='approved' if review_status=='approved' and approved_by else review_status
            published=bool(lesson.get('state')=='published' and lesson.get('review_status')=='approved' and review=='approved' and row.get('active') is True)
            ai_available=published
        else:
            review='available' if row.get('active') is True else 'revoked'
            published=row.get('active') is True;ai_available=row.get('active') is True
        clean={key:provenance[key] for key in ('review_status','approved_by','instruction_id','candidate_id') if key in provenance}
        row['provenance']=clean;row['review_status']=review;row['approved_by']=approved_by
        row['material_status']='revoked' if row.get('active') is not True else ('published' if published else review)
        row['ai_available']=bool(ai_available);row['access_scope']='agent' if row.get('agent_id') else 'company'
        row['origin_id']=origin_id
    return rows


def save_knowledge(profile, data, *, source_id=None, connection=None):
    creating = source_id is None
    if not isinstance(data, dict):
        raise TenantLinearError('Bilik mənbəyi düzgün deyil.')
    if not creating and 'agent_id' in data:
        raise TenantLinearError('Bilik mənbəyinin agenti dəyişdirilə bilməz.')
    if not creating and 'setup_key' in data:
        raise TenantLinearError('Yenidən cəhd açarı dəyişdirilə bilməz.')
    retry_key = validate_setup_key(data['setup_key']) if data.get('setup_key') is not None else None
    patch = validate_knowledge({k:v for k,v in data.items() if k not in {'agent_id','setup_key'}}, creating=creating)
    if not patch:
        raise TenantLinearError('Dəyişdiriləcək bilik ayarı yoxdur.')
    from contextlib import nullcontext
    with (nullcontext(connection) if connection is not None else _connect()) as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, actor = _authorize(cur, profile)
            if creating:
                bound_agent = identifier(data['agent_id']) if data.get('agent_id') else None
                if retry_key and not bound_agent:
                    raise TenantLinearError('Bilik bazasını yenidən yükləmək üçün agent seçilməlidir.')
                if bound_agent:
                    cur.execute('SELECT id FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid FOR SHARE', (tenant,bound_agent))
                    if not cur.fetchone(): raise TenantLinearError('Agent tapılmadı.',404)
                digest = hashlib.sha256(patch['content'].encode('utf-8')).hexdigest()
                if retry_key:
                    cur.execute('''SELECT id,tenant_id,agent_id,title,kind,content,active,content_hash,created_at,updated_at
                      FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND setup_key=%s FOR SHARE''',
                                (tenant, bound_agent, retry_key))
                    existing = _row(cur.fetchone())
                    if existing:
                        if existing['content_hash'] != digest:
                            raise TenantLinearError('Eyni yenidən cəhd açarı ilə başqa bilik faylı yüklənə bilməz.', 409)
                        return existing
                    # Recover a source created by the pre-retry-key wizard,
                    # but only when it is an exact match owned by this user.
                    # More than one match is deliberately left untouched.
                    cur.execute('''SELECT id,tenant_id,agent_id,title,kind,content,active,content_hash,created_at,updated_at
                      FROM saas_ai_knowledge_sources
                      WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND created_by=%s AND setup_key IS NULL
                        AND title=%s AND kind=%s AND active=%s AND content_hash=%s
                      ORDER BY created_at DESC LIMIT 2 FOR UPDATE''',
                                (tenant, bound_agent, actor, patch['title'], patch['kind'], patch['active'], digest))
                    legacy = cur.fetchall()
                    if len(legacy) == 1:
                        cur.execute('''UPDATE saas_ai_knowledge_sources SET setup_key=%s
                          WHERE tenant_id=%s::uuid AND id=%s::uuid AND setup_key IS NULL
                          RETURNING id,tenant_id,agent_id,title,kind,content,active,content_hash,created_at,updated_at''',
                                    (retry_key, tenant, legacy[0]['id']))
                        recovered = _row(cur.fetchone())
                        if recovered:
                            _audit(cur, tenant, actor, 'ai_knowledge_setup_recovered', 'knowledge_source', recovered['id'])
                            if connection is None: conn.commit()
                            return recovered
                cur.execute('SELECT count(*) AS total FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid', (tenant,))
                if int((cur.fetchone() or {}).get('total') or 0) >= MAX_SOURCES:
                    raise TenantLinearError('Bilik mənbələrinin sayı həddə çatıb.')
                source = uuid.uuid4()
                cur.execute('''INSERT INTO saas_ai_knowledge_sources(id,tenant_id,title,kind,content,active,content_hash,created_by,agent_id,setup_key)
                  VALUES(%s,%s::uuid,%s,%s,%s,%s,%s,%s,%s::uuid,%s) RETURNING id,tenant_id,agent_id,title,kind,content,active,content_hash,created_at,updated_at''',
                  (source, tenant, patch['title'], patch['kind'], patch['content'], patch['active'], digest, actor, bound_agent, retry_key))
                result = _row(cur.fetchone())
            else:
                source = identifier(source_id)
                cur.execute('''SELECT * FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE''', (tenant, source))
                previous = cur.fetchone()
                if not previous:
                    raise TenantLinearError('Bilik mənbəyi tapılmadı.', 404)
                provenance = previous.get('provenance') or {}
                if isinstance(provenance, str):
                    try: provenance = json.loads(provenance)
                    except (TypeError, ValueError): provenance = {}
                if isinstance(provenance, dict) and provenance.get('instruction_id'):
                    raise TenantLinearError('Dərc edilmiş təlimat kitabxanasından idarə olunur. Dəyişiklik və deaktivasiya üçün Təlimatlar bölməsindən istifadə edin.', 409)
                merged = {key: previous[key] for key in ('title','kind','content','active')}
                merged.update(patch)
                digest = hashlib.sha256(merged['content'].encode('utf-8')).hexdigest()
                cur.execute('''UPDATE saas_ai_knowledge_sources SET title=%s,kind=%s,content=%s,active=%s,
                  content_hash=%s,updated_at=now() WHERE tenant_id=%s::uuid AND id=%s::uuid
                  RETURNING id,tenant_id,agent_id,title,kind,content,active,content_hash,created_at,updated_at''',
                  (merged['title'], merged['kind'], merged['content'], merged['active'], digest, tenant, source))
                result = _row(cur.fetchone())
            if creating or 'content' in patch or (not creating and result['content_hash'] != previous.get('content_hash')):
                cur.execute('DELETE FROM saas_ai_knowledge_chunks WHERE tenant_id=%s::uuid AND source_id=%s::uuid', (tenant, source))
                for index, chunk in enumerate(chunk_text(result['content'])):
                    cur.execute('INSERT INTO saas_ai_knowledge_chunks(tenant_id,source_id,chunk_no,content) VALUES(%s::uuid,%s::uuid,%s,%s)',
                                (tenant, source, index, chunk))
            _audit(cur, tenant, actor, 'ai_knowledge_created' if creating else 'ai_knowledge_updated', 'knowledge_source', source,
                   {'kind': result['kind'], 'content_hash': result['content_hash']})
        if connection is None: conn.commit()
    return result


def delete_knowledge(profile, source_id):
    source = identifier(source_id)
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, actor = _authorize(cur, profile)
            cur.execute('SELECT provenance FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid AND id=%s::uuid FOR UPDATE', (tenant, source))
            row = cur.fetchone()
            if not row:
                raise TenantLinearError('Bilik mənbəyi tapılmadı.', 404)
            provenance = row.get('provenance') or {}
            if isinstance(provenance, str):
                try: provenance = json.loads(provenance)
                except (TypeError, ValueError): provenance = {}
            if isinstance(provenance, dict) and provenance.get('instruction_id'):
                raise TenantLinearError('Dərc edilmiş təlimat kitabxanasından idarə olunur. Təlimatlar bölməsindən nəşri ləğv edin.', 409)
            cur.execute('DELETE FROM saas_ai_knowledge_sources WHERE tenant_id=%s::uuid AND id=%s::uuid RETURNING id', (tenant, source))
            cur.fetchone()
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
    native_actions={'native_find_create_contact','native_find_create_company','native_find_create_deal','native_move_stage','native_assign_responsible','native_create_task','native_get_deal_status','native_get_task_status'}
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,actor=_authorize(cur,profile)
            cur.execute("SELECT role,expert_policy FROM saas_ai_agents WHERE tenant_id=%s::uuid AND id=%s::uuid",(tenant,agent))
            principal=cur.fetchone()
            if not principal:raise TenantLinearError('Agent tapılmadı.',404)
            if principal['role']!='primary':raise TenantLinearError('Internal agents cannot run client triggers.',409)
            from tenant_ai_expert_policy import helper_ids
            if not set((patch.get('condition') or {}).get('expert_agent_ids',[]))<=set(helper_ids(principal['expert_policy'] or {})):
                raise TenantLinearError('Select only this primary agent’s internal helpers.',409)
            existing = None
            if creating and patch.get('setup_key'):
                cur.execute('''SELECT id,agent_id,name,trigger_type,condition,actions,enabled,version,created_at,updated_at
                  FROM saas_ai_agent_triggers WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND setup_key=%s FOR SHARE''',
                            (tenant,agent,patch['setup_key']))
                existing = cur.fetchone()
                if not existing:
                    cur.execute('''SELECT id,agent_id,name,trigger_type,condition,actions,enabled,version,created_at,updated_at
                      FROM saas_ai_agent_triggers
                      WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND setup_key IS NULL
                        AND name=%s AND trigger_type=%s AND condition=%s::jsonb AND actions=%s::jsonb AND enabled=%s
                      ORDER BY created_at DESC LIMIT 2 FOR UPDATE''',
                                (tenant, agent, patch['name'], patch['trigger_type'], _json(patch['condition']),
                                 _json(patch['actions']), patch['enabled']))
                    legacy = cur.fetchall()
                    if len(legacy) == 1:
                        cur.execute('''UPDATE saas_ai_agent_triggers SET setup_key=%s
                          WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND id=%s::uuid AND setup_key IS NULL
                          RETURNING id,agent_id,name,trigger_type,condition,actions,enabled,version,created_at,updated_at''',
                                    (patch['setup_key'], tenant, agent, legacy[0]['id']))
                        existing = cur.fetchone()
                        if existing:
                            _audit(cur,tenant,actor,'ai_agent_trigger_setup_recovered','ai_agent_trigger',existing['id'])
            if existing:
                result=dict(existing)
            if not existing and any(action in patch.get('actions',[]) for action in ('create_task','update_phone','move_stage')):
                cur.execute('SELECT crm_actions_enabled FROM saas_ai_agent_settings WHERE tenant_id=%s::uuid FOR SHARE',(tenant,))
                settings=cur.fetchone() or {}
                if patch.get('enabled') and settings.get('crm_actions_enabled') is not True:
                    raise TenantLinearError('Əvvəlcə şirkət səviyyəsində AI CRM əməliyyatlarını aktiv edin.',409)
            if patch.get('enabled') and native_actions.intersection(patch.get('actions',[])):
                cur.execute('SELECT native_crm_actions_enabled FROM saas_ai_agent_settings WHERE tenant_id=%s::uuid FOR SHARE',(tenant,))
                if profile.get('role')!='owner' or (cur.fetchone() or {}).get('native_crm_actions_enabled') is not True:
                    raise TenantLinearError('Native CRM əməliyyatları yalnız sahib tərəfindən ayrıca aktivləşdirilə bilər.',403)
            if creating:
                if not existing:
                    trigger_uuid=uuid.uuid4()
                    cur.execute('''INSERT INTO saas_ai_agent_triggers(tenant_id,id,agent_id,name,trigger_type,condition,actions,enabled,setup_key)
                      VALUES(%s::uuid,%s,%s::uuid,%s,%s,%s::jsonb,%s::jsonb,%s,%s)
                      RETURNING id,agent_id,name,trigger_type,condition,actions,enabled,version,created_at,updated_at''',
                      (tenant,trigger_uuid,agent,patch['name'],patch['trigger_type'],_json(patch['condition']),_json(patch['actions']),patch['enabled'],patch.get('setup_key')))
                    result=dict(cur.fetchone())
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
                if merged['enabled'] and native_actions.intersection(merged['actions']):
                    cur.execute('SELECT native_crm_actions_enabled FROM saas_ai_agent_settings WHERE tenant_id=%s::uuid',(tenant,))
                    if profile.get('role')!='owner' or (cur.fetchone() or {}).get('native_crm_actions_enabled') is not True:
                        raise TenantLinearError('Native CRM əməliyyatları söndürülüb.',409)
                cur.execute('''UPDATE saas_ai_agent_triggers SET name=%s,trigger_type=%s,condition=%s::jsonb,actions=%s::jsonb,
                  enabled=%s,version=version+1,updated_at=now() WHERE tenant_id=%s::uuid AND agent_id=%s::uuid AND id=%s::uuid
                  RETURNING id,agent_id,name,trigger_type,condition,actions,enabled,version,created_at,updated_at''',
                  (merged['name'],merged['trigger_type'],_json(merged['condition']),_json(merged['actions']),merged['enabled'],tenant,agent,trigger_uuid))
                result=dict(cur.fetchone())
                _audit(cur,tenant,actor,'ai_agent_trigger_updated','ai_agent_trigger',trigger_uuid,{'version':previous['version']+1})
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


def _conversation_provider_allowed(cur, tenant, provider, conversation, channel_id=''):
    """Verify the server-owned tenant/provider route before changing a chat."""
    if provider == 'connect':
        if channel_id:
            cur.execute('''SELECT c.connect_channel_id::text AS channel_id FROM saas_connect_conversations c
              JOIN saas_connect_channels ch ON ch.tenant_id=c.tenant_id AND ch.connect_channel_id=c.connect_channel_id AND ch.active=TRUE
              WHERE c.tenant_id=%s::uuid AND c.conversation_id=%s AND c.connect_channel_id=%s::uuid''',
                        (tenant, conversation, channel_id))
        else:
            cur.execute('''SELECT c.connect_channel_id::text AS channel_id FROM saas_connect_conversations c
              JOIN saas_connect_channels ch ON ch.tenant_id=c.tenant_id AND ch.connect_channel_id=c.connect_channel_id AND ch.active=TRUE
              WHERE c.tenant_id=%s::uuid AND c.conversation_id=%s ORDER BY c.connect_channel_id LIMIT 2''', (tenant, conversation))
        rows = cur.fetchall()
        if len(rows) != 1:
            raise TenantLinearError('Connect dialoqu bu şirkətə aid deyil və ya kanal seçilməyib.', 404)
        assert_resolved(cur, tenant, str(rows[0]['channel_id']), conversation)
        return str(rows[0]['channel_id'])
    if provider != 'kommo':
        raise TenantLinearError('Mesaj mənbəyi düzgün deyil.')
    cur.execute('''SELECT 1 FROM saas_ai_conversation_state cs
      JOIN saas_tenant_integrations i ON i.tenant_id=cs.tenant_id AND i.provider='kommo' AND i.status='connected'
      WHERE cs.tenant_id=%s::uuid AND cs.provider='kommo' AND cs.conversation_id=%s
        AND EXISTS (SELECT 1 FROM saas_ai_agent_jobs j WHERE j.tenant_id=cs.tenant_id
          AND j.provider='kommo' AND j.conversation_id=cs.conversation_id) LIMIT 1''',(tenant,conversation))
    if not cur.fetchone():
        raise TenantLinearError('Conversation is not connected to this company.',404)
    return ''


def _conversation_provider_read_allowed(cur, tenant, provider, conversation, channel_id=''):
    """Resolve a tenant-owned dialogue for read-only status display.

    Management actions deliberately use the stricter admission check above.
    A stopped company or disabled agent must nevertheless remain visible in the
    chat UI, otherwise a manager cannot learn why the agent is stopped.
    """
    if provider == 'connect':
        from tenant_connect_transport import _schema as connect_schema
        connect_schema(cur.connection)
        if channel_id:
            cur.execute('''SELECT c.connect_channel_id::text AS channel_id
              FROM saas_connect_conversations c
              WHERE c.tenant_id=%s::uuid AND c.conversation_id=%s
                AND c.connect_channel_id=%s::uuid''', (tenant, conversation, channel_id))
        else:
            cur.execute('''SELECT c.connect_channel_id::text AS channel_id
              FROM saas_connect_conversations c
              WHERE c.tenant_id=%s::uuid AND c.conversation_id=%s
              ORDER BY c.connect_channel_id LIMIT 2''', (tenant, conversation))
        rows = cur.fetchall()
        if len(rows) != 1:
            raise TenantLinearError('Connect dialoqu bu şirkətə aid deyil və ya kanal seçilməyib.', 404)
        return str(rows[0]['channel_id'])
    if provider != 'kommo':
        raise TenantLinearError('Mesaj mənbəyi düzgün deyil.')
    cur.execute('''SELECT 1 FROM saas_ai_conversation_state cs
      WHERE cs.tenant_id=%s::uuid AND cs.provider='kommo' AND cs.conversation_id=%s
        AND EXISTS (SELECT 1 FROM saas_ai_agent_jobs j WHERE j.tenant_id=cs.tenant_id
          AND j.provider='kommo' AND j.conversation_id=cs.conversation_id)''', (tenant, conversation))
    if not cur.fetchone():
        raise TenantLinearError('Conversation bu şirkətə aid deyil.', 404)
    return ''


def _resume_block_reason(row, provider):
    """Return a durable, safe reason why a stopped dialogue cannot resume."""
    if not runtime_flag('AI_AGENTS_ENABLED'):
        return 'server_ai_disabled'
    if row.get('company_enabled') is not True:
        return 'company_ai_disabled'
    if row.get('company_emergency_stop') is True:
        return 'emergency_stop'
    if not tenant_status_allows_ai(row.get('tenant_status'), provider):
        return 'tenant_ai_unavailable'
    if not row.get('agent_id'):
        return 'agent_not_found'
    if row.get('agent_enabled') is not True:
        return 'agent_disabled'
    if row.get('agent_mode') == 'automatic' and row.get('auto_send_enabled') is not True:
        return 'auto_send_disabled'
    return ''


def _decorate_conversation_state(item):
    """Expose a presentation-safe effective state without changing storage."""
    state = str(item.get('state') or 'active')
    provider = str(item.get('provider') or 'kommo')
    mandatory_block = _resume_block_reason(item, provider)
    transient_block = ''
    if state == 'active':
        if item.get('latest_job_error') and item.get('latest_job_state') in {'skipped', 'failed', 'unknown'}:
            transient_block = str(item['latest_job_error'])
        elif item.get('latest_event_error') and item.get('latest_event_state') in {'skipped', 'failed', 'unknown', 'superseded'}:
            transient_block = str(item['latest_event_error'])
    effective = 'blocked' if state == 'active' and (mandatory_block or transient_block) else state
    item['display_state'] = effective
    item['resume_allowed'] = state in {'human', 'paused'} and not mandatory_block
    item['resume_block_reason'] = mandatory_block
    # A Human Mode/Pause reason explains why the agent stopped; a separate
    # resume blocker explains why it cannot be started again yet.
    item['display_reason'] = (str(item.get('reason') or '') if state in {'human', 'paused'}
                              else mandatory_block or transient_block or str(item.get('reason') or ''))
    return item


def _assert_resume_allowed(cur, tenant, provider, conversation, channel_id):
    """Resume only a tracked, still-admissible dialogue; never revive old work."""
    cur.execute('''SELECT cs.state,COALESCE(cs.agent_id,j.agent_id)::text AS agent_id,
        a.enabled AS agent_enabled,a.mode AS agent_mode,a.scopes,s.enabled AS company_enabled,
        s.emergency_stop AS company_emergency_stop,s.auto_send_enabled,t.status AS tenant_status
      FROM saas_ai_conversation_state cs
      JOIN saas_ai_agent_settings s ON s.tenant_id=cs.tenant_id
      JOIN saas_tenants t ON t.id=cs.tenant_id
      LEFT JOIN LATERAL (SELECT j0.agent_id FROM saas_ai_agent_jobs j0
        WHERE j0.tenant_id=cs.tenant_id AND j0.provider=cs.provider
          AND j0.conversation_id=cs.conversation_id AND j0.scope_channel_id=cs.channel_id
        ORDER BY j0.updated_at DESC,j0.revision DESC LIMIT 1) j ON TRUE
      LEFT JOIN saas_ai_agents a ON a.tenant_id=cs.tenant_id AND a.id=COALESCE(cs.agent_id,j.agent_id)
      WHERE cs.tenant_id=%s::uuid AND cs.provider=%s AND cs.conversation_id=%s AND cs.channel_id=%s
      FOR SHARE OF cs,s,t''', (tenant, provider, conversation, channel_scope(provider, channel_id)))
    row = cur.fetchone()
    if not row or str(row.get('state') or '') not in {'human', 'paused'}:
        raise TenantLinearError('Bu dialoq Resume üçün dayandırılmış vəziyyətdə deyil.', 409)
    block = _resume_block_reason(row, provider)
    if block:
        messages = {
            'server_ai_disabled': 'AI Agents serverdə söndürülüb.',
            'company_ai_disabled': 'AI Agents bu şirkət üçün söndürülüb.',
            'emergency_stop': 'AI Agents üçün аварийная остановка включена.',
            'tenant_ai_unavailable': 'Bu şirkətdə AI Agents işləyə bilməz.',
            'agent_not_found': 'Bu dialoq üçün AI Agent tapılmadı.',
            'agent_disabled': 'Bu dialoqun AI Agent-i söndürülüb.',
        }
        raise TenantLinearError(messages.get(block, 'AI Resume hazırda əlçatan deyil.'), 409)
    if provider == 'connect':
        from tenant_ai_agent_connect import scope_matches
        if not scope_matches(row.get('scopes'), channel_id):
            raise TenantLinearError('Agent scope no longer includes this channel.', 409)
        cur.execute('''SELECT 1 FROM saas_tenant_integrations
          WHERE tenant_id=%s::uuid AND provider='connect' AND status='connected' ''', (tenant,))
        if not cur.fetchone():
            raise TenantLinearError('Connect inteqrasiyası qoşulu deyil.', 409)
    else:
        cur.execute('''SELECT 1 FROM saas_tenant_integrations
          WHERE tenant_id=%s::uuid AND provider='kommo' AND status='connected' ''', (tenant,))
        if not cur.fetchone():
            raise TenantLinearError('Kommo inteqrasiyası qoşulu deyil.', 409)
    return row


def _conversation_authorize(cur, profile, provider, conversation, channel_id=''):
    # Preserve existing configuration authority; ordinary operators get only their routes.
    try:
        return _authorize(cur,profile)
    except TenantLinearError as error:
        if error.status != 403: raise
    from tenant_operator_access import access, assert_route
    tenant,actor,policy=access(cur,profile,'dialogs')
    assert_route(cur,tenant,policy,provider,conversation,str(channel_id or ''))
    return tenant,actor


def set_conversation_state(profile,conversation_id,state,reason='',provider='kommo',channel_id=''):
    from tenant_support_inbox_store import create_in_transaction, summarize_created
    ticket = None
    conversation=str(conversation_id or '').strip()
    provider=str(provider or 'kommo').strip().lower()
    if not conversation or len(conversation)>160 or state not in {'human','active','paused'}:
        raise TenantLinearError('Söhbət vəziyyəti düzgün deyil.')
    with _connect() as conn:
        ensure_schema(conn)
        if provider=='connect':
            from tenant_connect_transport import _schema as connect_schema
            connect_schema(conn)
            conn.commit()  # Finish DDL before acquiring group/dialogue locks.
        with conn.cursor() as cur:
            tenant,actor=_conversation_authorize(cur,profile,provider,conversation,channel_id)
            resolved_channel = _conversation_provider_allowed(cur,tenant,provider,conversation,str(channel_id or ''))
            lock_dialog(cur, tenant, provider, resolved_channel, conversation)
            if provider=='connect':
                assert_resolved(cur,tenant,resolved_channel,conversation)
            from tenant_ai_group_coordination import group_jid, transition, read as read_group
            if provider == 'connect' and group_jid(conversation):
                previous_group = read_group(cur,tenant,conversation)
                transition(cur,tenant,conversation,state,str(reason)[:240],actor)
                _audit(cur,tenant,actor,'ai_group_'+state,'conversation',conversation,{'channel_id':resolved_channel})
                result = read_group(cur,tenant,conversation)
                if state=='human' and previous_group.get('state')!='human':
                    ticket=create_in_transaction(cur,tenant,resolved_channel,conversation,reason,
                        'group-handoff:'+conversation+':'+str(result['revision']),actor=actor)
                conn.commit()
                summarize_created(tenant,ticket)
                return {'provider':provider,'channel_id':resolved_channel,'conversation_id':conversation,'state':result['state'],'reason':result['reason']}

            if state == 'human' and provider == 'connect':
                cur.execute("SELECT state FROM saas_ai_conversation_state WHERE tenant_id=%s::uuid AND provider='connect' AND conversation_id=%s AND channel_id=%s FOR UPDATE", (tenant,conversation,resolved_channel))
                current = cur.fetchone() or {}
                if current.get('state') == 'human':
                    conn.commit()
                    return {'conversation_id':conversation,'provider':provider,'channel_id':resolved_channel,'state':state,'reason':str(reason)[:240]}
            if state == 'active':
                # Resume only changes the durable conversation gate.  It does
                # not queue a prior inbound event, so only a future customer
                # message can create fresh AI work after this transition.
                _assert_resume_allowed(cur, tenant, provider, conversation, resolved_channel)
            cur.execute('''INSERT INTO saas_ai_conversation_state(tenant_id,provider,conversation_id,channel_id,state,reason)
              VALUES(%s::uuid,%s,%s,%s,%s,%s) ON CONFLICT(tenant_id,provider,channel_id,conversation_id) DO UPDATE
              SET state=EXCLUDED.state,reason=EXCLUDED.reason,revision=saas_ai_conversation_state.revision+1,
                resumed_at=CASE WHEN EXCLUDED.state='active' THEN clock_timestamp() ELSE saas_ai_conversation_state.resumed_at END,
                updated_at=now()''',
              (tenant,provider,conversation,resolved_channel,state,str(reason)[:240]))
            if provider=='connect' and state=='active':
                from tenant_ai_legacy_permit import renew
                renew(cur,tenant,resolved_channel,conversation)
            if state in {'human','paused'}:
                stop_code = 'manager_pause' if state == 'paused' else 'human_handoff'
                cur.execute("UPDATE saas_tenant_webhook_events SET state='skipped',completed_at=now(),error_code=%s WHERE tenant_id=%s::uuid AND fingerprint IN (SELECT event_fingerprint FROM saas_ai_agent_jobs WHERE tenant_id=%s::uuid AND provider=%s AND conversation_id=%s AND scope_channel_id=%s AND state IN ('queued','processing'))",(stop_code,tenant,tenant,provider,conversation,resolved_channel))
                cur.execute('''UPDATE saas_ai_agent_jobs SET state='skipped',revision=revision+1,lease_token=NULL,lease_until=NULL,
                  error_code=%s,updated_at=now() WHERE tenant_id=%s::uuid AND provider=%s AND conversation_id=%s AND scope_channel_id=%s
                  AND state IN ('queued','processing')''',(stop_code,tenant,provider,conversation,resolved_channel))
            if state == 'human' and provider == 'connect':
                ticket = create_in_transaction(cur, tenant, resolved_channel, conversation, reason,
                    'manual-handoff:' + str(uuid.uuid4()), actor=actor)
            _audit(cur,tenant,actor,'ai_conversation_'+state,'conversation',conversation,{'reason':str(reason)[:240],'provider':provider,'channel_id':resolved_channel})
        conn.commit()
    summarize_created(tenant, ticket)
    return {'conversation_id':conversation,'provider':provider,'channel_id':resolved_channel,'state':state,'reason':str(reason)[:240]}


def _group_state_overlay(item,group):
    if not group:return item
    item=dict(item)
    item.update(state=group['state'],reason=group['reason'],agent_id=group['primary_agent_id'],
                group_coordination=group,group_revision=group['revision'])
    item['display_state']=group['state'] if group['state']!='active' or group['primary_agent_id'] else 'blocked'
    item['display_reason']=group['reason'];item['blocked_reason']=group['reason']
    item['resume_allowed']=bool(group['primary_agent_id']) and group['state']!='active' and item.get('resume_allowed',False)
    return item


def read_conversation_state(profile,conversation_id,provider='kommo',channel_id=''):
    conversation=str(conversation_id or '').strip()
    provider=str(provider or 'kommo').strip().lower()
    if not conversation or len(conversation)>160:raise TenantLinearError('Söhbət identifikatoru düzgün deyil.')
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant,_=_conversation_authorize(cur,profile,provider,conversation,channel_id)
            resolved_channel = _conversation_provider_read_allowed(cur,tenant,provider,conversation,str(channel_id or ''))
            if provider == 'connect':
                try:
                    assert_resolved(cur, tenant, resolved_channel, conversation)
                except TenantLinearError as error:
                    if error.status != 409: raise
                    return {'conversation_id':conversation,'provider':provider,'channel_id':resolved_channel,
                        'state':'blocked','display_state':'blocked','reason':'legacy_channel_ambiguous',
                        'display_reason':'legacy_channel_ambiguous','blocked_reason':'legacy_channel_ambiguous',
                        'resume_allowed':False,'resume_block_reason':'legacy_channel_ambiguous','tracked':True}
            from tenant_ai_group_coordination import group_jid,view as group_view
            group_snapshot=group_view(cur,tenant,conversation) if provider=='connect' and group_jid(conversation) else None
            # The route was verified above against tenant-owned Connect data.
            # Diagnostics below intentionally expose only metadata and safe
            # state/error codes; message bodies and external secrets never
            # cross this boundary.
            cur.execute('''SELECT cs.conversation_id,cs.state,cs.reason,cs.assigned_to,
                COALESCE(cs.agent_id,j.agent_id)::text AS agent_id,a.name AS agent_name,a.enabled AS agent_enabled,
                s.enabled AS company_enabled,s.emergency_stop AS company_emergency_stop,t.status AS tenant_status,
                cs.last_inbound_message_id,cs.last_agent_message_id,
                cs.revision,cs.updated_at,m.happened_at AS last_inbound_at,
                j.state AS latest_job_state,j.error_code AS latest_job_error,j.updated_at AS latest_job_updated_at,
                r.state AS latest_run_state,r.updated_at AS latest_run_updated_at,
                e.state AS latest_event_state,e.error_code AS latest_event_error,e.received_at AS latest_event_received_at
              FROM saas_ai_conversation_state cs
              LEFT JOIN LATERAL (
                SELECT j0.agent_id,j0.event_fingerprint,j0.state,j0.error_code,j0.updated_at,j0.revision
                FROM saas_ai_agent_jobs j0
                WHERE j0.tenant_id=cs.tenant_id AND j0.provider=cs.provider
                  AND j0.conversation_id=cs.conversation_id AND j0.scope_channel_id=cs.channel_id
                  AND (cs.provider<>'connect' OR j0.channel_id=%s)
                ORDER BY j0.updated_at DESC,j0.revision DESC LIMIT 1
              ) j ON TRUE
              LEFT JOIN saas_ai_agents a ON a.tenant_id=cs.tenant_id AND a.id=COALESCE(cs.agent_id,j.agent_id)
              LEFT JOIN saas_ai_agent_settings s ON s.tenant_id=cs.tenant_id
              LEFT JOIN saas_tenants t ON t.id=cs.tenant_id
              LEFT JOIN LATERAL (
                SELECT r0.state,r0.updated_at FROM saas_ai_agent_runs r0
                WHERE r0.tenant_id=cs.tenant_id AND r0.agent_id=j.agent_id
                  AND r0.event_fingerprint=j.event_fingerprint
                ORDER BY r0.revision DESC LIMIT 1
              ) r ON TRUE
              LEFT JOIN saas_tenant_webhook_events e ON e.tenant_id=cs.tenant_id
                AND e.fingerprint=j.event_fingerprint
              LEFT JOIN LATERAL (
                SELECT m0.happened_at FROM saas_crm_messages m0
                WHERE m0.tenant_id=cs.tenant_id AND m0.direction='incoming'
                  AND (cs.provider<>'connect' OR m0.channel='connect:'||cs.channel_id)
                  AND m0.external_id=CASE WHEN cs.provider='connect'
                    THEN 'connect:' || cs.last_inbound_message_id ELSE cs.last_inbound_message_id END
                ORDER BY m0.happened_at DESC NULLS LAST,m0.created_at DESC LIMIT 1
              ) m ON TRUE
              WHERE cs.tenant_id=%s::uuid AND cs.provider=%s AND cs.conversation_id=%s AND cs.channel_id=%s''',
                        (resolved_channel,tenant,provider,conversation,resolved_channel))
            row=cur.fetchone()
            if not row:
                # It is possible to read a verified Connect conversation
                # before the first AI state row exists.  Return that fact
                # rather than silently reporting a made-up disabled agent.
                cur.execute('''SELECT e.state,e.error_code,e.received_at
                  FROM saas_tenant_webhook_events e
                  WHERE e.tenant_id=%s::uuid AND e.provider=%s
                    AND e.payload->>'conversation_id'=%s
                    AND (%s<>'connect' OR e.payload->>'channel_id'=%s)
                  ORDER BY e.received_at DESC LIMIT 1''',
                            (tenant,provider,conversation,provider,resolved_channel))
                event = cur.fetchone() or {}
                result = {'conversation_id':conversation,'provider':provider,'channel_id':resolved_channel,
                    'state':'active','reason':'','revision':0,'tracked':False,
                    'resume_allowed':False,'display_state':'blocked' if event.get('error_code') else 'active',
                    'latest_event_state':event.get('state') or '',
                    'latest_event_error':event.get('error_code') or ''}
                if event.get('received_at'):
                    result['latest_event_received_at']=event['received_at'].isoformat()
                if result['latest_event_error']:
                    result['blocked_reason']=result['latest_event_error']
                    result['display_reason']=result['latest_event_error']
                return _group_state_overlay(result,group_snapshot)
    item=dict(row)
    item['provider']=provider;item['channel_id']=resolved_channel;item['tracked']=True
    for key in ('updated_at','last_inbound_at','latest_job_updated_at','latest_run_updated_at','latest_event_received_at'):
        if item.get(key):item[key]=item[key].isoformat()
    # Only surface durable state and error codes; the browser maps them to a
    # clear operator-facing explanation without exposing message text.
    item = _decorate_conversation_state(item)
    item['blocked_reason'] = item['display_reason']
    return _group_state_overlay(item,group_snapshot)


def read_conversation_for_lead(profile, lead_id):
    """Resolve one tenant-owned transport route; ambiguous lead IDs fail closed."""
    try:
        lead = int(lead_id)
    except (TypeError, ValueError):
        raise TenantLinearError('Sövdələşmə identifikatoru düzgün deyil.') from None
    if lead <= 0:
        raise TenantLinearError('Sövdələşmə identifikatoru düzgün deyil.')
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, _ = _authorize(cur, profile)
            cur.execute("""SELECT DISTINCT ON (j.provider) j.provider,j.channel_id,j.conversation_id,
                cs.state,cs.reason,cs.revision,
                (j.provider='connect' AND ai_legacy_blocked(j.tenant_id,j.channel_id,j.conversation_id)) AS legacy_scope_unresolved
              FROM saas_ai_agent_jobs j
              LEFT JOIN saas_ai_conversation_state cs ON cs.tenant_id=j.tenant_id AND cs.provider=j.provider
                AND cs.conversation_id=j.conversation_id AND cs.channel_id=j.scope_channel_id
              WHERE j.tenant_id=%s::uuid AND j.lead_id=%s
                AND (j.provider='kommo' OR (j.provider='connect' AND EXISTS (
                  SELECT 1 FROM saas_connect_conversations c WHERE c.tenant_id=j.tenant_id
                    AND c.lead_id=j.lead_id AND c.conversation_id=j.conversation_id
                    AND c.connect_channel_id::text=j.channel_id)))
              ORDER BY j.provider,j.updated_at DESC LIMIT 2""", (tenant, lead))
            rows = cur.fetchall()
    if len(rows) != 1:
        return None
    row = rows[0]
    if row.get('legacy_scope_unresolved'):
        row = {**row, 'state':'blocked', 'reason':'legacy_channel_ambiguous'}
    return {'conversation_id': str(row['conversation_id']), 'state': row.get('state') or 'active',
            'provider': row['provider'], 'channel_id': row.get('channel_id') or '',
            'reason': row.get('reason') or '', 'revision': int(row.get('revision') or 0)}










def list_handoffs(profile,limit=50):
    """List tenant-owned dialogue state for the AI console.

    The historical endpoint name is retained for compatibility, but the
    console needs to see active chats as well as paused/human ones in order to
    explain why an agent is (or is not) responding.  Only transport metadata,
    durable state and error codes are returned; never message text or secrets.
    """
    with _connect() as conn:
        ensure_schema(conn)
        from tenant_connect_transport import _schema as ensure_connect_schema
        ensure_connect_schema(conn)
        with conn.cursor() as cur:
            tenant,_=_authorize(cur,profile)
            cur.execute('''SELECT cs.provider,cs.conversation_id,cs.state,cs.reason,cs.updated_at,
                COALESCE(cs.agent_id,j.agent_id)::text AS agent_id,cs.last_inbound_message_id,cs.last_agent_message_id,
                a.name AS agent_name,a.enabled AS agent_enabled,
                COALESCE(NULLIF(c.contact_name,''),NULLIF(c.phone,''),NULLIF(d.contact_name,''),NULLIF(d.name,''),cs.conversation_id) AS customer_name,
                CASE WHEN cs.provider='connect' THEN COALESCE(NULLIF(ch.name,''),j.channel_id,'WhatsApp')
                  ELSE COALESCE(NULLIF(j.channel_id,''),'Kommo') END AS channel_name,
                s.enabled AS company_enabled,s.emergency_stop AS company_emergency_stop,t.status AS tenant_status,
                j.lead_id,j.talk_id,j.channel_id,j.state AS latest_job_state,
                j.error_code AS latest_job_error,j.updated_at AS latest_job_updated_at,
                inbound.happened_at AS last_inbound_at,agent_message.happened_at AS last_agent_at
              FROM saas_ai_conversation_state cs
              LEFT JOIN LATERAL (SELECT agent_id,lead_id,talk_id,channel_id,state,error_code,updated_at
                FROM saas_ai_agent_jobs j0 WHERE j0.tenant_id=cs.tenant_id
                  AND j0.provider=cs.provider AND j0.conversation_id=cs.conversation_id AND j0.scope_channel_id=cs.channel_id
                ORDER BY j0.updated_at DESC,j0.revision DESC LIMIT 1) j ON TRUE
              LEFT JOIN saas_ai_agents a ON a.tenant_id=cs.tenant_id AND a.id=COALESCE(cs.agent_id,j.agent_id)
              LEFT JOIN saas_ai_agent_settings s ON s.tenant_id=cs.tenant_id
              LEFT JOIN saas_tenants t ON t.id=cs.tenant_id
              LEFT JOIN saas_connect_conversations c ON c.tenant_id=cs.tenant_id
                AND c.conversation_id=cs.conversation_id AND c.connect_channel_id::text=j.channel_id
              LEFT JOIN saas_connect_channels ch ON ch.tenant_id=cs.tenant_id AND ch.connect_channel_id::text=j.channel_id
              LEFT JOIN saas_crm_deals d ON d.tenant_id=cs.tenant_id AND d.kommo_lead_id=j.lead_id
              LEFT JOIN LATERAL (SELECT m.happened_at FROM saas_crm_messages m
                WHERE m.tenant_id=cs.tenant_id AND m.direction='incoming'
                  AND m.external_id=CASE WHEN cs.provider='connect'
                    THEN 'connect:' || cs.last_inbound_message_id ELSE cs.last_inbound_message_id END
                ORDER BY m.happened_at DESC NULLS LAST,m.created_at DESC LIMIT 1) inbound ON TRUE
              LEFT JOIN LATERAL (SELECT m.happened_at FROM saas_crm_messages m
                WHERE m.tenant_id=cs.tenant_id AND m.direction='outgoing'
                  AND m.external_id=CASE WHEN cs.provider='connect'
                    THEN 'connect:' || cs.last_agent_message_id ELSE cs.last_agent_message_id END
                ORDER BY m.happened_at DESC NULLS LAST,m.created_at DESC LIMIT 1) agent_message ON TRUE
              WHERE cs.tenant_id=%s::uuid
              ORDER BY cs.updated_at DESC LIMIT %s''',(tenant,max(1,min(int(limit),100))))
            rows=cur.fetchall()
    result=[]
    for row in rows:
        item=_decorate_conversation_state(dict(row))
        for key in ('updated_at','last_inbound_at','last_agent_at','latest_job_updated_at'):
            if item.get(key):item[key]=item[key].isoformat()
        result.append(item)
    return result


def list_connect_diagnostics(profile, limit=50):
    """Return tenant-local Connect processing checkpoints, without message text."""
    with _connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            tenant, _ = _authorize(cur, profile)
            return read_connect_diagnostics(cur, tenant, limit)


def read_connect_diagnostics(cur, tenant, limit=50):
    """Internal projection; caller must authorize and hold membership locks."""
    import os
    from tenant_ai_agent_runtime_flags import configured as runtime_configured, staging_mode
    cur.execute('''SELECT t.status AS tenant_status,s.tenant_id IS NOT NULL AS company_settings_configured,
        COALESCE(s.enabled,FALSE) AS company_ai_enabled,
        s.auto_send_enabled AS automatic_send_enabled,COALESCE(s.emergency_stop,FALSE) AS emergency_stop,
        COALESCE(s.auto_send_enabled,FALSE) AS auto_send_enabled,
        (SELECT count(*) FROM saas_tenant_integrations i WHERE i.tenant_id=t.id
          AND i.provider='connect' AND i.status='connected') AS connected_connect_accounts,
        (SELECT count(*) FROM saas_connect_channels c WHERE c.tenant_id=t.id AND c.active=TRUE) AS active_connect_channels
      FROM saas_tenants t LEFT JOIN saas_ai_agent_settings s ON s.tenant_id=t.id
      WHERE t.id=%s::uuid''', (tenant,))
    system = dict(cur.fetchone() or {})
    system['tenant_id'] = tenant
    cur.execute('''SELECT connect_channel_id::text AS channel_id,name,status,active
      FROM saas_connect_channels WHERE tenant_id=%s::uuid ORDER BY name,connect_channel_id''', (tenant,))
    channels = [dict(row) for row in cur.fetchall()]
    cur.execute('''SELECT id::text AS agent_id,name,enabled,mode,scopes
      FROM saas_ai_agents WHERE tenant_id=%s::uuid ORDER BY created_at,id''', (tenant,))
    agent_rows = cur.fetchall()
    agents = []
    for row in agent_rows:
        item = dict(row)
        scopes = item.get('scopes') if isinstance(item.get('scopes'), list) else []
        item['connect_scope_channel_ids'] = [str(scope.get('channel_id') or '')
            for scope in scopes if isinstance(scope, dict)
            and str(scope.get('provider') or '').casefold() == 'connect']
        item.pop('scopes', None)
        agents.append(item)
    cur.execute('''SELECT COALESCE(e.received_at,m.happened_at) AS received_at,
        CASE WHEN e.fingerprint IS NULL THEN 'stored_not_queued' ELSE e.state END AS event_state,
        e.error_code AS event_error,SUBSTRING(m.channel FROM 9) AS channel_id,
        a.name AS agent_name,a.enabled AS agent_enabled,a.mode AS agent_mode,
        s.enabled AS company_ai_enabled,s.auto_send_enabled,
        j.state AS job_state,j.error_code AS job_error,j.attempts,
        r.state AS run_state,r.updated_at AS run_updated_at
      FROM saas_crm_messages m
      LEFT JOIN saas_tenant_webhook_events e ON e.tenant_id=m.tenant_id AND e.provider='connect'
        AND e.external_id=SUBSTRING(m.external_id FROM 9)
      LEFT JOIN saas_ai_agent_jobs j ON j.tenant_id=e.tenant_id AND j.event_fingerprint=e.fingerprint
      LEFT JOIN saas_ai_agents a ON a.tenant_id=j.tenant_id AND a.id=j.agent_id
      LEFT JOIN saas_ai_agent_settings s ON s.tenant_id=m.tenant_id
      LEFT JOIN LATERAL (SELECT state,updated_at FROM saas_ai_agent_runs r0
        WHERE r0.tenant_id=e.tenant_id AND r0.agent_id=j.agent_id
          AND r0.event_fingerprint=e.fingerprint ORDER BY r0.revision DESC LIMIT 1) r ON TRUE
      WHERE m.tenant_id=%s::uuid AND m.direction='incoming' AND m.channel LIKE 'connect:%%'
      ORDER BY m.happened_at DESC LIMIT %s''',
                 (tenant, max(1, min(int(limit), 100))))
    rows = cur.fetchall()
    ai_enabled = runtime_flag('AI_AGENTS_ENABLED')
    auto_send_enabled = runtime_flag('AI_AGENTS_AUTO_SEND_ENABLED')
    system['enabled_agents'] = sum(1 for agent in agents if agent.get('enabled') is True)
    result = []
    for row in rows:
        item = dict(row)
        for key in ('received_at', 'run_updated_at'):
            if item.get(key):
                item[key] = item[key].isoformat()
        item['attempts'] = int(item.get('attempts') or 0)
        item['auto_send_block_reason'] = ''
        if item.get('job_state') in {'drafted', 'sent'}:
            if item.get('agent_mode') != 'automatic':
                item['auto_send_block_reason'] = 'manual_mode'
            elif item.get('auto_send_enabled') is not True:
                item['auto_send_block_reason'] = 'company_auto_send_disabled'
            elif not runtime_flag('AI_AGENTS_AUTO_SEND_ENABLED'):
                item['auto_send_block_reason'] = 'server_auto_send_flag_disabled'
        result.append(item)
    return {
        'system': system,
        'server_flags': {
            'ai_agents_enabled': ai_enabled,
            'auto_send_enabled': auto_send_enabled,
        },
        'runtime': {
            'service_name': str(os.environ.get('RAILWAY_SERVICE_NAME') or ''),
            'service_id': str(os.environ.get('RAILWAY_SERVICE_ID') or ''),
            'deployment_id': str(os.environ.get('RAILWAY_DEPLOYMENT_ID') or ''),
            'environment_name': str(os.environ.get('RAILWAY_ENVIRONMENT_NAME') or ''),
            'environment_id': str(os.environ.get('RAILWAY_ENVIRONMENT_ID') or ''),
            'staging_mode': staging_mode(),
            'configured_ai_agents_enabled': runtime_configured('AI_AGENTS_ENABLED'),
            'configured_auto_send_enabled': runtime_configured('AI_AGENTS_AUTO_SEND_ENABLED'),
            'ai_agents_enabled': ai_enabled,
            'auto_send_enabled': auto_send_enabled,
        },
        'channels': channels,
        'agents': agents,
        'events': result,
        'latest_event_found': bool(result),
    }
