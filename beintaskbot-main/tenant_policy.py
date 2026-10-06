"""Company workflow decisions, independent of Telegram names and integrations."""
from dataclasses import dataclass


ROLE_PERMISSIONS = {
    'owner': ['deals', 'tasks', 'customers', 'employees', 'integrations', 'finance', 'settings', 'hot_orders', 'linear'],
    'admin': ['deals', 'tasks', 'customers', 'employees', 'integrations', 'finance', 'settings', 'hot_orders', 'linear'],
    'manager': ['deals', 'tasks', 'customers'],
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

    def allows(self, module: str) -> bool:
        if self.profile.get('active') is False:
            return False
        if self.profile.get('role') == 'owner':
            return True
        if self.profile.get('role') == 'master' and module != 'hot_orders':
            return False
        if self.profile.get('role') == 'worker' and module in {'deals', 'customers'}:
            return False
        modules = self.policies.get('modules', self.profile.get('modules') or {})
        if module not in {'settings', 'employees', 'integrations'} and not modules.get(module, False):
            return False
        permissions = set(self.profile.get('permissions') or [])
        return module in permissions

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

    def can_access_deal(self, deal: dict | None) -> bool:
        if not deal or str(deal.get('tenant_id')) != str(self.profile.get('tenant_id')):
            return False
        if not (self.allows('deals') or self.allows('customers')):
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
        scope = self.pipeline_scope()
        if self.profile.get('role') == 'manager':
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
            return {'action': 'move_deal' if has_deal else 'create_deal',
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
        return {'modules': {key: self.allows(key) for key in ROLE_PERMISSIONS['owner']},
                'pipeline_scope': self.pipeline_scope(), 'task_scope': self.task_scope(),
                'task_completion_requires_admin': self.requires_task_approval(creator_id=0, executor_id=0, completion=True),
                'deal_completion_requires_admin': self.requires_deal_approval()}


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
            raise ValueError('Kommo vərəq ID-si düzgün deyil.')
        if 'active' in item and not isinstance(item['active'], bool):
            raise ValueError('Vərəqin aktivliyi düzgün deyil.')
        if not isinstance(item.get('settings', {}), dict):
            raise ValueError('Vərəq ayarları düzgün deyil.')
    for item in stages or []:
        if not isinstance(item, dict) or not positive_id(item.get('pipeline_id')) or not positive_id(item.get('stage_id')):
            raise ValueError('Kommo mərhələ ID-si düzgün deyil.')
        if item.get('stage_type', 'open') not in {'open', 'won', 'lost'}:
            raise ValueError('Mərhələ növü düzgün deyil.')
        if not isinstance(item.get('settings', {}), dict):
            raise ValueError('Mərhələ ayarları düzgün deyil.')
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
        for flag in ('creation_requires_admin', 'completion_requires_admin', 'deal_completion_requires_admin'):
            if flag in item and not isinstance(item[flag], bool):
                raise ValueError('Əməkdaş qaydaları düzgün deyil.')
    for key in ('task_approval', 'deal_completion', 'modules', 'notifications'):
        if any(not isinstance(v, bool) for v in ((policies or {}).get(key) or {}).values()):
            raise ValueError('Qayda üçün aktiv/deaktiv seçin.')
