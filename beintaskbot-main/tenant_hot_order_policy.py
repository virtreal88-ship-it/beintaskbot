"""Tenant hot-order decisions; no legacy storage or transport dependencies."""
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tenant_policy import TenantPolicy

ROLES = {'owner', 'admin', 'manager', 'worker', 'master'}


def validate_hot_order_settings(company: dict, members: dict) -> None:
    for key in ('create_roles', 'claim_roles'):
        value = company.get(key)
        if key in company and (not isinstance(value, list) or
                                  any(not isinstance(role, str) or role not in ROLES for role in value)):
            raise ValueError('İsti sifariş rolları düzgün deyil.')
    services = company.get('services', [])
    if not isinstance(services, list) or len(services) > 100:
        raise ValueError('Xidmət siyahısı düzgün deyil (maksimum 100).')
    seen = set()
    for service in services:
        if not isinstance(service, dict):
            raise ValueError('Xidmət ayarları düzgün deyil.')
        key, name = service.get('id'), service.get('name')
        if (not isinstance(key, str) or not key.strip() or key != key.strip() or len(key) > 80 or
                key in seen or not isinstance(name, str) or not name.strip() or len(name) > 120 or
                not isinstance(service.get('active', True), bool)):
            raise ValueError('Xidmət ID-si, adı və aktivliyi düzgün deyil.')
        seen.add(key)
    for settings in members.values():
        for key in ('hot_order_create', 'hot_order_claim'):
            if key in settings and not isinstance(settings[key], bool):
                raise ValueError('İsti sifariş hüququ üçün aktiv/deaktiv seçin.')
        skills = settings.get('hot_order_services', [])
        if (not isinstance(skills, list) or len(skills) > 100 or
                any(not isinstance(skill, str) or not skill.strip() or skill != skill.strip() or len(skill) > 80 for skill in skills)):
            raise ValueError('Əməkdaşın xidmətləri düzgün deyil.')
        if 'services' in company and any(skill not in seen for skill in skills):
            raise ValueError('Əməkdaş üçün şirkətin xidmətini seçin.')


@dataclass(frozen=True)
class HotOrderPolicy:
    policy: 'TenantPolicy'

    @property
    def settings(self) -> dict:
        return self.policy.policies.get('hot_orders') or {}

    def services(self) -> list[dict]:
        if not self.policy.allows('hot_orders'):
            return []
        return [dict(service) for service in self.settings.get('services', []) if service.get('active', True)]

    def allowed(self, action: str) -> bool:
        if action not in {'create', 'claim'} or not self.policy.allows('hot_orders'):
            return False
        default_roles = ['owner', 'admin'] if action == 'create' else ['master']
        default = self.policy.profile.get('role') in self.settings.get(action + '_roles', default_roles)
        return self.policy.member_settings.get('hot_order_' + action, default) is True

    def matches_service(self, service_id: str) -> bool:
        active = {item['id'] for item in self.services()}
        return service_id in active and service_id in (self.policy.member_settings.get('hot_order_services') or [])

    def owns_tenant(self, order: dict) -> bool:
        tenant = self.policy.profile.get('tenant_id')
        return bool(tenant and str(tenant) == str(order.get('tenant_id')) and self.policy.allows('hot_orders'))

    def can_claim(self, order: dict) -> bool:
        return (self.owns_tenant(order) and self.allowed('claim') and order.get('status') == 'open'
                and self.matches_service(str(order.get('service_id') or '')))

    def can_edit(self, order: dict) -> bool:
        return (self.owns_tenant(order) and order.get('status') == 'open' and
                (self.policy.privileged or str(order.get('created_by')) == str(self.policy.profile.get('telegram_id'))))

    def can_view(self, order: dict) -> bool:
        if not self.owns_tenant(order):
            return False
        user = str(self.policy.profile.get('telegram_id'))
        return (self.policy.privileged or str(order.get('claimed_by')) == user or
                str(order.get('created_by')) == user or self.can_claim(order))

    def capabilities(self) -> dict:
        return {'settings_only': False, 'api_available': True, 'can_create': self.allowed('create'), 'can_claim': self.allowed('claim'),
                'services': self.services(),
                'assigned_services': [row['id'] for row in self.services() if self.matches_service(row['id'])]}
