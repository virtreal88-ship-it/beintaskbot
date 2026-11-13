"""Company workflow decisions, independent of Telegram names and integrations."""
from dataclasses import dataclass
from tenant_notifications import notification_catalog, validate_notification_preferences
from tenant_hot_order_policy import HotOrderPolicy, validate_hot_order_settings


ROLE_PERMISSIONS = {
    'owner': ['deals', 'tasks', 'customers', 'employees', 'integrations', 'finance', 'settings', 'hot_orders', 'linear', 'ai_agents', 'dialogs', 'support', 'night_report', 'operator_data'],
    'admin': ['deals', 'tasks', 'customers', 'employees', 'integrations', 'finance', 'settings', 'hot_orders', 'linear', 'ai_agents', 'dialogs', 'support'],
    'manager': ['deals', 'tasks', 'customers', 'dialogs', 'support'],
    'operator': ['tasks'],
    # Historical role keys remain readable for existing memberships.
    'worker': ['tasks'],
    'master': ['hot_orders'],
}


def positive_id(value: object) -> int:
    try:
        result = int(str(value))
        return result if result > 0 else 0
    except (TypeError, ValueError):
        return 0


def policy_values(config: dict) -> dict:
    rows = config.get('policies') or []
    if isinstance(rows, dict):
        return rows
    return {row['policy_key']: row.get('value') for row in rows
            if isinstance(row, dict) and row.get('policy_key')}


@dataclass(frozen=True)
class TenantPolicy:
    """Resolve authority and resource scope from a current membership snapshot."""
    profile: dict

    @property
    def config(self) -> dict:
        return self.profile.get('workflow') or {}

    @property
    def policies(self) -> dict:
        return policy_values(self.config)

    @property
    def member_settings(self) -> dict:
        return (self.policies.get('members') or {}).get(str(self.profile.get('telegram_id')), {})

    @property
    def privileged(self) -> bool:
        return self.profile.get('role') in {'owner', 'admin'}

    def action_scope(self, module: str, action: str):
        from tenant_scoped_access import configured_action_scope, ACTIVE_TENANT_STATUSES
        scope = configured_action_scope(self, module, action)
        if scope is None:
            return None
        if self.profile.get('active') is False or self.profile.get('tenant_status', 'active') not in ACTIVE_TENANT_STATUSES:
            return 'none'
        return 'all' if self.profile.get('role') == 'owner' else scope

    def has_scoped_access(self, module: str, action=None) -> bool:
        from tenant_scoped_access import CRUD_ACTIONS
        return (self.action_scope(module, action) is not None if action else
                any(self.action_scope(module, item) is not None for item in CRUD_ACTIONS))

    def allows_resource(self, module: str, action: str, resource: dict) -> bool:
        from tenant_scoped_access import allows_owned_resource, profile_actor, resource_metadata
        actor = profile_actor(self.profile)
        scope = self.action_scope(module, action)
        if scope is None:
            legacy_module = 'customers' if module in {'companies', 'contacts'} else module
            return (allows_owned_resource(actor, resource_metadata(resource), 'all')
                    and self.legacy_allows_action(legacy_module, action))
        return allows_owned_resource(actor, resource_metadata(resource), scope)

    def allows(self, module: str) -> bool:
        # Coarse callers cannot establish row/source scope. A module using the
        # matrix must go through resource-aware routes, never a legacy shortcut.
        if self.profile.get('role') != 'owner' and self.has_scoped_access(module):
            return False
        return self.legacy_allows(module)

    def legacy_allows(self, module: str) -> bool:
        if self.profile.get('active') is False:
            return False
        if self.profile.get('role') == 'owner':
            return True
        # AI Agents is a first-party product module, not a Kommo capability
        # or an onboarding module toggle. Owners always have it; admins only
        # see it after the owner explicitly grants the existing manage flag.
        if module in {'dialogs','support'}:
            if self.profile.get('role') == 'master':
                return False
            return module in set(self.profile.get('permissions') or []) or self.legacy_allows('ai_agents')
        if module == 'night_report':
            return self.profile.get('role') != 'master' and ('night_report' in set(self.profile.get('permissions') or []) or self.legacy_allows('ai_agents'))
        if module == 'ai_agents':
            return self.profile.get('role') == 'admin' and self.member_settings.get('ai_agents_manage') is True
        if self.profile.get('role') == 'master' and module != 'hot_orders':
            return False
        if self.profile.get('role') in {'worker', 'operator'} and module in {'deals', 'customers'}:
            return False
        modules = self.policies.get('modules', self.profile.get('modules') or {})
        if module not in {'settings', 'employees', 'integrations'} and not modules.get(module, False):
            return False
        permissions = set(self.profile.get('permissions') or [])
        if module == 'employees' and 'employees:manage' in permissions:
            return True
        return module in permissions

    def allows_action(self, module: str, action: str) -> bool:
        if module == 'employees' and action == 'manage' and self.profile.get('role') != 'owner' and self.has_scoped_access(module):
            return False
        scope = self.action_scope(module, action)
        if scope is not None:
            return scope != 'none'
        return self.legacy_allows_action('customers' if module in {'companies', 'contacts'} else module, action)

    def legacy_allows_action(self, module: str, action: str) -> bool:
        """Check a granular grant, while preserving legacy module grants.

        Existing module permissions continue to authorize their historical
        endpoints. New endpoints can require an explicit action grant; owners
        keep tenant-wide authority and inactive memberships fail closed.
        """
        allowed_actions = {'view', 'create', 'update', 'delete', 'assign', 'manage', 'link'}
        if action not in allowed_actions or self.profile.get('active') is False:
            return False
        if self.profile.get('role') == 'owner':
            return True
        if module == 'ai_agents' and action in {'view', 'create', 'update'}:
            return self.legacy_allows('ai_agents')
        # Native CRM CRUD overrides are independent of legacy module grants.
        # In particular, changing native delete must not revoke Kommo access or
        # convert a broad membership grant. Explicit false wins over :manage.
        if module in {'tasks', 'deals', 'customers'} and action in {'view', 'create', 'update', 'delete'}:
            native = self.member_settings.get('native_crm_actions', {})
            values = native.get(module, {}) if isinstance(native, dict) else {}
            override = values.get(action) if isinstance(values, dict) else None
            if type(override) is bool:
                return override
        permissions = set(self.profile.get('permissions') or [])
        action_grants = {f'{module}:{item}' for item in allowed_actions}
        if module in permissions and not (permissions & action_grants):
            return self.legacy_allows(module)  # legacy module grant
        return f'{module}:{action}' in permissions or f'{module}:manage' in permissions

    def pipeline_scope(self) -> list[dict]:
        """An empty scope means no access, never all pipelines."""
        configured = self.config.get('pipelines') or []
        assigned = {str(pid) for pid in self.member_settings.get('pipeline_ids', [])}
        result = []
        if configured:
            for pipeline in configured:
                pid = positive_id(pipeline.get('pipeline_id'))
                if not pid or not pipeline.get('active', True):
                    continue
                owns = positive_id(pipeline.get('owner_telegram_id')) == positive_id(self.profile.get('telegram_id'))
                if not self.privileged and (self.profile.get('role') != 'manager' or not (owns or str(pid) in assigned)):
                    continue
                stages = [stage for stage in self.config.get('stages', []) if str(stage.get('pipeline_id')) == str(pid)]
                visible = [positive_id(stage.get('stage_id')) for stage in stages
                           if (stage.get('settings') or {}).get('visible', True)]
                # A configured pipeline with all stages hidden is closed.
                if stages and not visible:
                    continue
                result.append({'pipeline_id': pid, 'status_ids': visible})
        else:
            # Existing onboarding stays usable for the company owner. Other
            # members need an explicit pipeline assignment before reading deals.
            selected = ((self.profile.get('onboarding') or {}).get('pipeline') or {}).get('selected') or []
            for pipeline in selected:
                pid = positive_id(pipeline.get('id'))
                if pid and (self.privileged or (self.profile.get('role') == 'manager' and str(pid) in assigned)):
                    result.append({'pipeline_id': pid, 'status_ids': [positive_id(s) for s in pipeline.get('stage_ids', []) if positive_id(s)]})
        return result

    def kommo_pipeline_scope(self) -> list[dict]:
        configured = self.config.get('pipelines') or []
        assigned = {str(pid) for pid in self.member_settings.get('pipeline_ids', [])}
        allowed = None if self.privileged else assigned
        result = []
        for pipeline in configured:
            pid = positive_id(pipeline.get('pipeline_id'))
            settings = pipeline.get('settings') or {}
            if not pid or not pipeline.get('active', True) or settings.get('provider') == 'beintaskbot' or (allowed is not None and str(pid) not in allowed):
                continue
            stages = [stage for stage in self.config.get('stages', []) if str(stage.get('pipeline_id')) == str(pid)]
            visible = [positive_id(stage.get('stage_id')) for stage in stages if (stage.get('settings') or {}).get('visible', True)]
            if stages and not visible:
                continue
            result.append({'pipeline_id': pid, 'status_ids': visible})
        if not configured and self.privileged:
            for pipeline in (((self.profile.get('onboarding') or {}).get('pipeline') or {}).get('selected') or []):
                pid = positive_id(pipeline.get('id'))
                if pid: result.append({'pipeline_id': pid, 'status_ids': [positive_id(s) for s in pipeline.get('stage_ids', []) if positive_id(s)]})
        return result

    def kommo_user_id(self) -> int:
        return positive_id(self.member_settings.get('kommo_user_id'))

    def kommo_access(self) -> bool:
        return self.profile.get('active') is True and (self.privileged or bool(self.kommo_pipeline_scope()) or bool(self.kommo_user_id()))

    def kommo_task_access(self) -> bool:
        """Task markers can authorize a member's own Kommo tasks without a deal pipeline."""
        return self.profile.get('active') is True and self.allows('tasks')

    def can_access_kommo_deal(self, deal: dict | None) -> bool:
        if not deal or str(deal.get('tenant_id')) != str(self.profile.get('tenant_id')): return False
        if self.privileged: return True
        if any(positive_id(deal.get('pipeline_id')) == item['pipeline_id'] and (not item['status_ids'] or positive_id(deal.get('status_id')) in item['status_ids']) for item in self.kommo_pipeline_scope()): return True
        raw = deal.get('raw') if isinstance(deal.get('raw'), dict) else {}
        creator = self.kommo_user_id()
        return bool(creator and positive_id(raw.get('created_by_id') or raw.get('created_by')) == creator)

    def kommo_task_scope(self) -> dict:
        return {'kind': 'all'} if self.privileged else {'kind': 'kommo', 'pipelines': self.kommo_pipeline_scope(), 'marker': self.task_marker(), 'kommo_user_id': self.kommo_user_id()}

    def legacy_crm_view_restricted(self, module='deals') -> bool:
        """An explicit employee read scope needs resource-aware rows.

        This is a restriction, not a grant: task pickers retain their separate
        task/pipeline entitlement when native view is absent or unbounded.
        """
        return (self.profile.get('role') != 'owner'
                and self.action_scope(module, 'view') not in (None, 'all'))

    def can_read_legacy_crm(self, module='deals') -> bool:
        """Do not satisfy an explicit read scope through another legacy grant.

        Provider snapshots lack employee ownership. Restricted reads use native
        resource-aware routes; all retains the existing pipeline/stage boundary.
        Unset view scopes retain legacy reads even when another action is scoped.
        Contact projections include deal context, so both read boundaries apply.
        """
        if module not in ('deals', 'customers'):
            return False
        if self.profile.get('active') is False:
            return False
        if self.profile.get('role') == 'owner':
            return True
        modules = ('deals', 'customers') if module == 'customers' else ('deals',)
        if any(self.legacy_crm_view_restricted(resource) for resource in modules):
            return False
        return self.legacy_allows('deals') or self.legacy_allows('customers')

    def can_access_deal(self, deal: dict | None) -> bool:
        if not deal or str(deal.get('tenant_id')) != str(self.profile.get('tenant_id')):
            return False
        if not self.can_read_legacy_crm():
            return False
        return any(positive_id(deal.get('pipeline_id')) == item['pipeline_id']
                   and (not item['status_ids'] or positive_id(deal.get('status_id')) in item['status_ids'])
                   for item in self.pipeline_scope())

    def task_marker(self) -> str:
        """Provider text marker; never a name or shared Kommo responsible user."""
        import hashlib
        identity = f"{self.profile.get('tenant_id')}:{positive_id(self.profile.get('telegram_id'))}"
        return '[CRM:' + hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32] + ']'

    def task_scope(self) -> dict:
        if not self.allows('tasks'):
            return {'kind': 'denied'}
        if self.privileged:
            return {'kind': 'all'}
        if self.profile.get('role') == 'manager':
            # A pipeline user must not fall back to markers when all stages
            # have been hidden or their pipeline has been disabled.
            return {'kind': 'pipelines', 'pipelines': self.pipeline_scope()}
        return {'kind': 'marker', 'marker': self.task_marker()}

    def task_creation_route(self, *, has_deal: bool, pipeline_id: int = 0) -> dict:
        """Executor routing plan, independent of the provider's single admin."""
        if not self.allows('tasks'):
            raise ValueError('Tapşırıqlar üçün icazəniz yoxdur.')
        if has_deal:
            return {'action': 'keep_deal', 'marker': self.task_marker()}
        # Administrators can see all pipelines, but their personal tasks must
        # route only to pipelines owned by or explicitly assigned to them.
        scope = TenantPolicy({**self.profile, 'role': 'manager'}).pipeline_scope() if self.privileged else self.pipeline_scope()
        if self.profile.get('role') == 'manager' or scope:
            choices = [item for item in scope if not pipeline_id or item['pipeline_id'] == positive_id(pipeline_id)]
            if not choices:
                raise ValueError('İcraçının aktiv vərəqi yoxdur.')
            if len(choices) > 1:
                raise ValueError('İcraçının vərəqini seçin.')
            target = choices[0]
            stages = sorted((s for s in self.config.get('stages', [])
                             if positive_id(s.get('pipeline_id')) == target['pipeline_id']
                             and s.get('stage_type', 'open') == 'open'
                             and positive_id(s.get('stage_id')) not in {142, 143}
                             and (s.get('settings') or {}).get('visible', True)),
                            key=lambda s: (s.get('sort_order', 0), positive_id(s.get('stage_id'))))
            if not stages:
                raise ValueError('İcraçının ilk açıq mərhələsini sazlayın.')
            return {'action': 'create_deal',
                    'pipeline_id': target['pipeline_id'], 'status_id': positive_id(stages[0]['stage_id']), 'marker': ''}
        return {'action': 'keep_deal' if has_deal else 'standalone', 'marker': self.task_marker()}

    def requires_task_approval(self, *, creator_id: int, executor_id: int, completion: bool = False) -> bool:
        settings = self.policies.get('task_approval') or {}
        if not completion and positive_id(creator_id) and positive_id(creator_id) == positive_id(executor_id) and settings.get('self_created_exempt', True):
            return False
        key = 'completion_requires_admin' if completion else 'creation_requires_admin'
        return bool(self.member_settings.get(key, settings.get(key, False)))

    def requires_deal_approval(self) -> bool:
        return bool(self.member_settings.get('deal_completion_requires_admin',
                    (self.policies.get('deal_completion') or {}).get('requires_admin', False)))

    def public_capabilities(self) -> dict:
        from tenant_member_access import page_capabilities, action_capabilities
        modules = {key: self.allows(key) for key in ROLE_PERMISSIONS['owner']}
        finance_scope = self.action_scope('finance', 'view')
        if finance_scope is not None:
            modules['finance'] = finance_scope != 'none'
        if self.has_scoped_access('support'):
            support_view=self.action_scope('support','view')
            modules['support'] = support_view != 'none' if support_view is not None else self.legacy_allows('support')
        from tenant_employee_policy import employee_action_allowed
        from tenant_scoped_access import scoped_access_enabled
        capabilities = {'can_create_employees': employee_action_allowed(self.profile, 'create'),
                'employee_creation_scoped': scoped_access_enabled(),
                'can_view_employees': employee_action_allowed(self.profile, 'view'),
                'can_update_employees': employee_action_allowed(self.profile, 'update'),
                'modules': modules,
                'pages': page_capabilities(self.profile),
                'actions': action_capabilities(self.profile),
                'deal_board': self.deal_board(),
                'chat_ai': self.can_use_chat_ai(),
                'hot_orders': HotOrderPolicy(self).capabilities(),
                'notification_events': notification_catalog(),
                'pipeline_scope': self.pipeline_scope(), 'kommo_pipeline_scope': self.kommo_pipeline_scope(), 'task_scope': self.task_scope(),
                'task_completion_requires_admin': self.requires_task_approval(creator_id=0, executor_id=0, completion=True),
                'deal_completion_requires_admin': self.requires_deal_approval()}
        if scoped_access_enabled():
            from tenant_finance_policy import finance_capabilities
            capabilities['finance'] = finance_capabilities(self)
            capabilities['support'] = {action: (self.action_scope('support',action)!='none'
                if self.action_scope('support',action) is not None else self.legacy_allows('support'))
                for action in ('view','create','update')}
        from tenant_ai_scoped_access import capabilities as ai_document_capabilities
        document_access = ai_document_capabilities(self)
        if document_access is not None:
            capabilities['ai_documents'] = document_access
        return capabilities

    def deal_board(self) -> list[dict]:
        """Display metadata only for pipelines/stages this membership may read."""
        if not self.can_read_legacy_crm():
            return []
        pipelines = {positive_id(p.get('pipeline_id')): p for p in self.config.get('pipelines', [])}
        result = []
        for scope in self.pipeline_scope():
            pid = scope['pipeline_id']
            stages = sorted((s for s in self.config.get('stages', [])
                             if positive_id(s.get('pipeline_id')) == pid
                             and positive_id(s.get('stage_id')) in scope['status_ids']),
                            key=lambda s: (s.get('sort_order', 0), positive_id(s.get('stage_id'))))
            result.append({'pipeline_id': pid, 'name': str(pipelines.get(pid, {}).get('name') or ''),
                           'stages': [{'stage_id': positive_id(s.get('stage_id')), 'name': str(s.get('name') or '')}
                                      for s in stages]})
        return result

    def can_use_chat_ai(self) -> bool:
        return (self.profile.get('active') is not False and self.can_read_legacy_crm()
                and (self.profile.get('role') == 'owner' or self.member_settings.get('ai_enabled') is True))


def validate_workflow_patch(pipelines: object, stages: object, policies: object) -> None:
    """Reject malformed rules before starting any database writes."""
    if pipelines is not None and not isinstance(pipelines, list):
        raise ValueError('Vərəqlər siyahı olmalıdır.')
    if stages is not None and not isinstance(stages, list):
        raise ValueError('Mərhələlər siyahı olmalıdır.')
    if policies is not None and not isinstance(policies, dict):
        raise ValueError('Workflow qaydaları düzgün deyil.')
    for item in pipelines or []:
        if not isinstance(item, dict) or not positive_id(item.get('pipeline_id')):
            raise ValueError('Vərəq ID-si düzgün deyil.')
        if 'active' in item and not isinstance(item['active'], bool):
            raise ValueError('Vərəqin aktivliyi düzgün deyil.')
        if not isinstance(item.get('settings', {}), dict):
            raise ValueError('Vərəq ayarları düzgün deyil.')
    for item in stages or []:
        if not isinstance(item, dict) or not positive_id(item.get('pipeline_id')) or not positive_id(item.get('stage_id')):
            raise ValueError('Mərhələ ID-si düzgün deyil.')
        if item.get('stage_type', 'open') not in {'open', 'won', 'lost'}:
            raise ValueError('Mərhələ növü düzgün deyil.')
        if not isinstance(item.get('settings', {}), dict):
            raise ValueError('Mərhələ ayarları düzgün deyil.')
        native_task=(item.get('settings') or {}).get('native_task')
        if native_task is not None:
            if not isinstance(native_task,dict) or set(native_task)-{'enabled','text','responsible_id','due_days'}:
                raise ValueError('Mərhələnin tapşırıq qaydası düzgün deyil.')
            if not isinstance(native_task.get('enabled',False),bool):
                raise ValueError('Mərhələnin tapşırıq qaydası düzgün deyil.')
            text=str(native_task.get('text') or '').strip()
            if len(text)>3500 or (native_task.get('enabled') and not text):
                raise ValueError('Avtomatik tapşırıq üçün mətn tələb olunur.')
            due=native_task.get('due_days',1)
            if not isinstance(due,int) or isinstance(due,bool) or due<0 or due>365:
                raise ValueError('Tapşırığın müddəti 0–365 gün olmalıdır.')
            if native_task.get('responsible_id') is not None and not positive_id(native_task.get('responsible_id')):
                raise ValueError('Tapşırığın icraçısı düzgün deyil.')
        if not isinstance(item.get('sort_order', 0), int):
            raise ValueError('Mərhələ sırası düzgün deyil.')
        if 'visible' in (item.get('settings') or {}) and not isinstance(item['settings']['visible'], bool):
            raise ValueError('Mərhələnin görünüşü düzgün deyil.')
    for key in ('members', 'task_approval', 'deal_completion', 'hot_orders', 'notifications', 'modules'):
        value = (policies or {}).get(key)
        if value is not None and not isinstance(value, dict):
            raise ValueError('Workflow qaydaları obyekt olmalıdır.')
    for key, item in ((policies or {}).get('members') or {}).items():
        if not positive_id(key) or not isinstance(item, dict):
            raise ValueError('Əməkdaş ayarları düzgün deyil.')
        if not isinstance(item.get('pipeline_ids', []), list) or any(not positive_id(pid) for pid in item.get('pipeline_ids', [])):
            raise ValueError('Əməkdaşın vərəqləri düzgün deyil.')
        if item.get('kommo_user_id') and not positive_id(item['kommo_user_id']):
            raise ValueError('Kommo istifadəçi ID-si düzgün deyil.')
        for flag in ('creation_requires_admin', 'completion_requires_admin', 'deal_completion_requires_admin', 'ai_enabled'):
            if flag in item and not isinstance(item[flag], bool):
                raise ValueError('Əməkdaş qaydaları düzgün deyil.')
        if 'notifications' in item:
            validate_notification_preferences(item['notifications'])
        if 'access_scopes' in item:
            from tenant_scoped_access import validate_access_scopes
            validate_access_scopes(item['access_scopes'])
        if 'native_crm_actions' in item:
            from tenant_member_access import validate_native_crm_actions
            validate_native_crm_actions(item['native_crm_actions'])
    for key in ('task_approval', 'deal_completion', 'modules', 'notifications'):
        if any(not isinstance(v, bool) for v in ((policies or {}).get(key) or {}).values()):
            raise ValueError('Qayda üçün aktiv/deaktiv seçin.')
    validate_hot_order_settings((policies or {}).get('hot_orders') or {}, (policies or {}).get('members') or {})
    from tenant_finance_policy import validate_finance_settings
    validate_finance_settings((policies or {}).get('finance',{}))
