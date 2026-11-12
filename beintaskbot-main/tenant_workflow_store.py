"""Tenant-owned pipelines, stages and workflow policy storage."""
import uuid

from tenant_policy import ROLE_PERMISSIONS, validate_workflow_patch
from tenant_platform_common import _permissions
from tenant_platform_core import TenantPlatformError, _connect, _ensure_schema, _json

_ROLE_PERMISSIONS = ROLE_PERMISSIONS


def _member(tenant_id: str, telegram_id: int) -> dict | None:
    from tenant_member_store import member
    return member(tenant_id, telegram_id)


def _read_tenant_workflow(cur, tenant_id: str) -> dict:
    """Read configuration on the existing membership DB connection."""
    cur.execute("""
                SELECT pipeline_id, name, active, owner_telegram_id, settings
                FROM saas_tenant_pipelines WHERE tenant_id=%s::uuid
                ORDER BY name, pipeline_id
            """, (str(tenant_id),))
    pipelines = cur.fetchall()
    cur.execute("""
                SELECT pipeline_id, stage_id, name, sort_order, stage_type, settings
                FROM saas_tenant_pipeline_stages WHERE tenant_id=%s::uuid
                ORDER BY pipeline_id, sort_order, name
            """, (str(tenant_id),))
    stages = cur.fetchall()
    cur.execute("""
                SELECT policy_key, value, updated_by, updated_at
                FROM saas_tenant_workflow_policies WHERE tenant_id=%s::uuid
                ORDER BY policy_key
            """, (str(tenant_id),))
    policies = cur.fetchall()
    def normalize(row: dict) -> dict:
        item = dict(row)
        if not isinstance(item.get("settings"), dict): item["settings"] = {}
        if item.get("updated_at"): item["updated_at"] = item["updated_at"].isoformat()
        return item
    return {
        "pipelines": [normalize(row) for row in pipelines],
        "stages": [normalize(row) for row in stages],
        "policies": [normalize(row) for row in policies],
    }


def list_tenant_workflow_config(*, tenant_id: str, owner_id: int) -> dict:
    """Return configurable pipelines, stages and workflow policies."""
    current = _member(str(tenant_id), int(owner_id))
    if not current or current.get("role") != "owner":
        raise TenantPlatformError("Yalnız şirkət sahibi workflow ayarlarını görə bilər.")
    return current["workflow"]


def save_tenant_workflow_config(*, tenant_id: str, owner_id: int,
                                pipelines: list[dict] | None = None,
                                stages: list[dict] | None = None,
                                policies: dict | None = None,
                                members: list[dict] | None = None,
                                member_pipeline_changes: list[dict] | None = None) -> dict:
    """Replace the owner-managed workflow configuration atomically."""
    current = _member(str(tenant_id), int(owner_id))
    if not current or current.get("role") != "owner":
        raise TenantPlatformError("Yalnız şirkət sahibi workflow ayarlarını dəyişə bilər.")
    try:
        validate_workflow_patch(pipelines, stages, policies)
    except ValueError as exc:
        raise TenantPlatformError(str(exc)) from exc
    pipelines = pipelines if isinstance(pipelines, list) else []
    stages = stages if isinstance(stages, list) else []
    policies = policies if isinstance(policies, dict) else {}
    if members is not None and not isinstance(members, list):
        raise TenantPlatformError('Əməkdaşlar siyahı olmalıdır.')
    changes = member_pipeline_changes if member_pipeline_changes is not None else []
    if not isinstance(changes, list):
        raise TenantPlatformError('Vərəq dəyişiklikləri siyahı olmalıdır.')
    seen = set()
    for change in changes:
        if (not isinstance(change, dict) or set(change) != {'telegram_id', 'expected_pipeline_ids', 'pipeline_ids'}
                or not str(change.get('telegram_id') or '').isdigit()
                or not isinstance(change.get('pipeline_ids'), list)
                or not all(isinstance(pid, str) and pid for pid in change['pipeline_ids'])
                or (change.get('expected_pipeline_ids') is not None and
                    not isinstance(change['expected_pipeline_ids'], list))):
            raise TenantPlatformError('Vərəq dəyişikliyi düzgün deyil.')
        identity = int(change['telegram_id'])
        if identity in seen:
            raise TenantPlatformError('Əməkdaş təkrar göstərilib.')
        seen.add(identity)
    if changes and any(str(key).strip()[:120] == 'members' for key in policies):
        raise TenantPlatformError('Əməkdaş ayarlarını ayrıca saxlayın.')
    members = members or []
    for item in members:
        if not isinstance(item, dict) or not str(item.get('telegram_id') or '').isdigit() or item.get('role') not in {'admin','manager','worker','master'}:
            raise TenantPlatformError('Əməkdaş növü və hesabı düzgün deyil.')
        if not str(item.get('display_name') or '').strip() or not isinstance(item.get('permissions'), list):
            raise TenantPlatformError('Əməkdaşın adı və hüquqları düzgün deyil.')
    with _connect() as conn:
        _ensure_schema(conn)
        with conn.cursor() as cur:
            # Match employee settings: tenant, policy keys, then memberships.
            cur.execute("SELECT owner_telegram_id,status FROM saas_tenants WHERE id=%s::uuid FOR SHARE", (str(tenant_id),))
            company = cur.fetchone()
            if not company or company['owner_telegram_id'] != int(owner_id) or company['status'] != 'active':
                raise TenantPlatformError('Yalnız aktiv şirkətin sahibi ayarları dəyişə bilər.')
            locked_policies = {}
            policy_keys = {str(key or '').strip()[:120] for key in policies}
            if changes:
                policy_keys.add('members')
            for key in sorted(policy_keys - {'', 'summary_language'}):
                cur.execute("""INSERT INTO saas_tenant_workflow_policies (tenant_id,policy_key,value,updated_by)
                    VALUES (%s::uuid,%s,'{}'::jsonb,%s) ON CONFLICT (tenant_id,policy_key) DO NOTHING""",
                    (str(tenant_id), key, int(owner_id)))
                cur.execute("SELECT value FROM saas_tenant_workflow_policies WHERE tenant_id=%s::uuid AND policy_key=%s FOR UPDATE", (str(tenant_id), key))
                locked_policies[key] = cur.fetchone()['value']
            # Default-deny is an audited company-wide transition, never a side
            # effect of an old/full settings form or an echoed policy document.
            if any(str(key or '').strip()[:120] == 'employee_access_defaults' and key != 'employee_access_defaults' for key in policies):
                raise TenantPlatformError('employee_default_deny_requires_audited_transition')
            if 'employee_access_defaults' in policies and policies['employee_access_defaults'] != locked_policies.get('employee_access_defaults'):
                raise TenantPlatformError('employee_default_deny_requires_audited_transition')
            # Matrix rights have their own version and source preflight in the
            # employee editor. Old/full workflow forms may echo or omit them,
            # but may neither change them nor erase a newer saved matrix.
            from tenant_scoped_access import preserve_workflow_access_scopes
            scoped_member_policies = {}
            for raw_key, value in list(policies.items()):
                if str(raw_key or '').strip()[:120] == 'members':
                    try:
                        scoped_member_policies[raw_key] = preserve_workflow_access_scopes(
                            locked_policies.get('members'), value)
                    except ValueError as exc:
                        raise TenantPlatformError(str(exc)) from exc
            # This setting has its own optimistic version contract. The
            # existing workflow editor may echo it, but cannot bypass that
            # contract or overwrite a newer language policy on an old form.
            if any(str(key or '').strip()[:120] == 'summary_language' and key != 'summary_language' for key in policies):
                raise TenantPlatformError('Настройку языка сводок сохраняйте через её версионную форму.')
            if 'summary_language' in policies:
                cur.execute("SELECT value FROM saas_tenant_workflow_policies WHERE tenant_id=%s::uuid AND policy_key='summary_language'", (str(tenant_id),))
                current_policy = cur.fetchone()
                if not current_policy or current_policy['value'] != policies['summary_language']:
                    raise TenantPlatformError('Настройка языка сводок изменилась. Сохраните её в настройках компании.')
                policies = {key: value for key, value in policies.items() if key != 'summary_language'}
            # Validate links against this company's current membership before
            # writing grants. IDs from another company must never grant access.
            # Lock only actors whose authority or grants this write uses.
            # Unrelated directory readers hold membership SHARE locks; taking
            # every employee here would block independent pipeline updates.
            actor_ids = {int(owner_id)}
            actor_ids.update(int(item['telegram_id']) for item in members)
            actor_ids.update(int(item['telegram_id']) for item in changes)
            actor_ids.update(int(key) for key in (policies.get('members') or {}))
            actor_ids.update(int(item['owner_telegram_id']) for item in pipelines if item.get('owner_telegram_id'))
            for item in stages:
                trigger = (item.get('settings') or {}).get('native_task') or {}
                if trigger.get('responsible_id'):
                    actor_ids.add(int(trigger['responsible_id']))
            updated_actor_ids = {int(item['telegram_id']) for item in members}
            locked_members = []
            for actor_id in sorted(actor_ids):
                mode = 'UPDATE' if actor_id in updated_actor_ids else 'SHARE'
                cur.execute("SELECT telegram_id,role,permissions FROM saas_tenant_members "
                            "WHERE tenant_id=%s::uuid AND active=TRUE AND telegram_id=%s FOR " + mode,
                            (str(tenant_id), actor_id))
                actor = cur.fetchone()
                if actor:
                    locked_members.append(actor)
            if not any(int(row['telegram_id']) == int(owner_id) and row['role'] == 'owner' for row in locked_members):
                raise TenantPlatformError('Yalnız şirkət sahibi ayarları dəyişə bilər.')
            cur.execute("SELECT telegram_id,role,permissions FROM saas_tenant_members WHERE tenant_id=%s::uuid AND active=TRUE", (str(tenant_id),))
            active_members = cur.fetchall()
            member_roles = {int(row['telegram_id']): row['role'] for row in active_members}
            task_members = {int(row['telegram_id']) for row in active_members if row['role']=='owner' or 'tasks' in (row.get('permissions') or [])}
            member_ids = set(member_roles)
            if any(int(item['telegram_id']) not in member_ids or int(item['telegram_id']) == int(owner_id) for item in members):
                raise TenantPlatformError('Yalnız bu şirkətin aktiv əməkdaşlarını dəyişə bilərsiniz.')
            member_roles.update({int(item['telegram_id']): item['role'] for item in members})
            for item in members:
                if item['role']=='owner' or 'tasks' in item.get('permissions',[]): task_members.add(int(item['telegram_id']))
                else: task_members.discard(int(item['telegram_id']))
            cur.execute("SELECT pipeline_id FROM saas_tenant_pipelines WHERE tenant_id=%s::uuid", (str(tenant_id),))
            pipeline_ids = {str(row['pipeline_id']) for row in cur.fetchall()} | {str(item['pipeline_id']) for item in pipelines}
            for item in pipelines:
                if item.get('owner_telegram_id') and int(item['owner_telegram_id']) not in member_ids:
                    raise TenantPlatformError('Vərəqin sahibi bu şirkətin əməkdaşı deyil.')
                if item.get('owner_telegram_id') and member_roles[int(item['owner_telegram_id'])] not in {'owner','admin','manager'}:
                    raise TenantPlatformError('Vərəqin sahibi vərəq istifadəçisi və ya administrator olmalıdır.')
            for item in stages:
                if str(item['pipeline_id']) not in pipeline_ids:
                    raise TenantPlatformError('Mərhələnin vərəqi bu şirkətdə yoxdur.')
                trigger=(item.get('settings') or {}).get('native_task') or {}
                if trigger.get('responsible_id') and int(trigger['responsible_id']) not in task_members:
                    raise TenantPlatformError('Avtomatik tapşırığın icraçısı bu şirkətdə aktiv tapşırıq icazəsinə malik olmalıdır.')
            for key, value in (policies.get('members') or {}).items():
                if int(key) not in member_ids or any(str(pid) not in pipeline_ids for pid in value.get('pipeline_ids', [])):
                    raise TenantPlatformError('Əməkdaş və vərəq eyni şirkətə aid olmalıdır.')
            if changes:
                existing = locked_policies.get('members')
                if not isinstance(existing, dict):
                    raise TenantPlatformError('Əməkdaş ayarları düzgün deyil.')
                updated = dict(existing)
                for change in changes:
                    identity = int(change['telegram_id'])
                    if member_roles.get(identity) != 'manager':
                        raise TenantPlatformError('Yalnız aktiv vərəq istifadəçisinin girişini dəyişə bilərsiniz.')
                    prior = existing.get(str(identity), {})
                    if not isinstance(prior, dict) or prior.get('pipeline_ids') != change['expected_pipeline_ids']:
                        raise TenantPlatformError('Vərəq girişi dəyişib. Ayarları yeniləyin.')
                    if any(pid not in pipeline_ids for pid in change['pipeline_ids']):
                        raise TenantPlatformError('Əməkdaş və vərəq eyni şirkətə aid olmalıdır.')
                    updated[str(identity)] = {**prior, 'pipeline_ids': change['pipeline_ids']}
                policies = {**policies, 'members': updated}
            for item in pipelines:
                pid = str(item.get("pipeline_id") or "").strip()[:120]
                if not pid: continue
                cur.execute("""
                    INSERT INTO saas_tenant_pipelines (tenant_id, pipeline_id, name, active, owner_telegram_id, settings)
                    VALUES (%s::uuid,%s,%s,%s,%s,%s::jsonb)
                    ON CONFLICT (tenant_id,pipeline_id) DO UPDATE SET
                      name=EXCLUDED.name, active=EXCLUDED.active,
                      owner_telegram_id=EXCLUDED.owner_telegram_id,
                      settings=EXCLUDED.settings, updated_at=now()
                """, (str(tenant_id), pid, str(item.get("name") or "")[:180], bool(item.get("active", True)),
                       int(item.get("owner_telegram_id") or 0) or None, _json(item.get("settings") if isinstance(item.get("settings"), dict) else {})))
            for item in stages:
                pid = str(item.get("pipeline_id") or "").strip()[:120]
                sid = str(item.get("stage_id") or "").strip()[:120]
                if not pid or not sid: continue
                cur.execute("""
                    INSERT INTO saas_tenant_pipeline_stages (tenant_id,pipeline_id,stage_id,name,sort_order,stage_type,settings)
                    VALUES (%s::uuid,%s,%s,%s,%s,%s,%s::jsonb)
                    ON CONFLICT (tenant_id,pipeline_id,stage_id) DO UPDATE SET
                      name=EXCLUDED.name, sort_order=EXCLUDED.sort_order,
                      stage_type=EXCLUDED.stage_type, settings=EXCLUDED.settings, updated_at=now()
                """, (str(tenant_id), pid, sid, str(item.get("name") or "")[:180], int(item.get("sort_order") or 0),
                       str(item.get("stage_type") or "open")[:40], _json(item.get("settings") if isinstance(item.get("settings"), dict) else {})))
            # Task admission takes policy row locks in canonical key order.
            # Stable sorting also preserves last-write wins for aliases that
            # normalize to the same existing policy key.
            policies.update(scoped_member_policies)
            policy_items = sorted(((str(key or "").strip()[:120], value)
                                   for key, value in policies.items()), key=lambda item: item[0])
            for policy_key, value in policy_items:
                if not policy_key: continue
                cur.execute("""
                    INSERT INTO saas_tenant_workflow_policies (tenant_id,policy_key,value,updated_by)
                    VALUES (%s::uuid,%s,%s::jsonb,%s)
                    ON CONFLICT (tenant_id,policy_key) DO UPDATE SET value=EXCLUDED.value, updated_by=EXCLUDED.updated_by, updated_at=now()
                """, (str(tenant_id), policy_key, _json(value), int(owner_id)))
            # Match task admission's numeric membership lock order. Stable
            # sorting keeps repeated IDs' existing last-update semantics.
            for item in sorted(members, key=lambda item: int(item['telegram_id'])):
                cur.execute("""
                    UPDATE saas_tenant_members SET display_name=%s,role=%s,permissions=%s::jsonb,updated_at=now()
                    WHERE tenant_id=%s::uuid AND telegram_id=%s AND role <> 'owner' AND active=TRUE
                """, (str(item['display_name']).strip()[:120], item['role'], _json(_permissions(item['permissions'],item['role'])), str(tenant_id), int(item['telegram_id'])))
            cur.execute("""
                INSERT INTO saas_tenant_audit_events (id,tenant_id,actor_telegram_id,action,entity_type,payload)
                VALUES (%s,%s::uuid,%s,'workflow_updated','workflow',%s::jsonb)
            """, (uuid.uuid4(), str(tenant_id), int(owner_id), _json({'pipelines': len(pipelines), 'stages': len(stages), 'policy_keys': list(policies)})))
        conn.commit()
    return list_tenant_workflow_config(tenant_id=tenant_id, owner_id=owner_id)
