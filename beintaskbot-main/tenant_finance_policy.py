"""Money parsing and tenant authority; no provider or DB dependencies."""
from decimal import Decimal,InvalidOperation
import uuid
from tenant_policy import TenantPolicy,positive_id


class TenantFinanceError(ValueError):
    def __init__(self,message: str,status: int=400):
        super().__init__(message)
        self.status=status


def finance_policy(profile: dict, *, write: bool=False) -> TenantPolicy:
    from tenant_scoped_access import scoped_access_enabled
    if scoped_access_enabled() and not isinstance(profile.get('permissions'),list):
        profile={**profile,'permissions':[]}
    policy=TenantPolicy(profile)
    if profile.get('tenant_status','active') not in {'active','onboarding','ready_for_integration'}:
        raise TenantFinanceError('Şirkət kabineti aktiv deyil.',403)
    scope=policy.action_scope('finance','view')
    allowed=policy.legacy_allows('finance') if write or scope is None else scope!='none'
    if not profile.get('tenant_id') or not positive_id(profile.get('telegram_id')) or profile.get('active') is not True or not allowed:
        raise TenantFinanceError('Maliyyə üçün icazəniz yoxdur.',403)
    if write and not policy.privileged:
        raise TenantFinanceError('Əməliyyat üçün administrator icazəsi lazımdır.',403)
    return policy


def finance_scope(policy):
    scope=policy.action_scope('finance','view')
    return scope if scope is not None else 'all' if policy.privileged else 'self'


def finance_member_allowed(policy,person):
    if not person or str(person.get('tenant_id'))!=str(policy.profile.get('tenant_id')):
        return False
    if policy.has_scoped_access('finance','view'):
        return policy.allows_resource('finance','view',{**person,
            'responsible_member_id':person.get('telegram_id'),
            'responsible_department_id':person.get('department_id')})
    return policy.privileged or positive_id(person.get('telegram_id'))==positive_id(policy.profile.get('telegram_id'))


def finance_member_sql(profile,alias='m'):
    from tenant_scoped_access_store import resource_scope_sql
    policy=finance_policy(profile)
    if policy.has_scoped_access('finance','view'):
        return resource_scope_sql(profile,'finance','view',alias=alias,responsible_column='telegram_id')
    # Legacy read behavior is self for non-admins and tenant-wide for admins.
    sql=f'{alias}.tenant_id=%s::uuid';args=[str(profile['tenant_id'])]
    if not policy.privileged:
        sql+=f' AND {alias}.telegram_id=%s';args.append(int(profile['telegram_id']))
    return sql,args


def finance_capabilities(policy):
    try:
        finance_policy(policy.profile,write=True)
        can_write=True
    except TenantFinanceError:
        can_write=False
    try:
        finance_policy(policy.profile)
        scope=finance_scope(policy)
    except TenantFinanceError:
        scope='none'
    return {'can_write':can_write,'can_view_all':scope=='all',
            'can_select_members':scope in {'department','all'},'read_scope':scope}


def entry_id(value: object) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError,ValueError,AttributeError):
        raise TenantFinanceError('Əməliyyat ID-si düzgün deyil.') from None


def minor_amount(value: object) -> int:
    # JSON decimal strings avoid loss of precision through binary floating point.
    if isinstance(value,bool) or not isinstance(value,(str,int)):
        raise TenantFinanceError('Məbləği mətn kimi yazın, məsələn 100.00.')
    try:
        amount=Decimal(str(value).strip())
        if not amount.is_finite() or amount<=0 or amount>Decimal('999999999.99') or amount!=amount.quantize(Decimal('0.01')):
            raise ValueError()
        return int(amount*100)
    except (InvalidOperation,ValueError,OverflowError):
        raise TenantFinanceError('Məbləğ müsbət olmalıdır, maksimum iki qəpik rəqəmi ilə.') from None


def money_text(minor: int) -> str:
    return format(Decimal(minor)/100,'.2f')


def validate_finance_settings(value: object) -> None:
    if not isinstance(value,dict):
        raise ValueError('Maliyyə qaydaları obyekt olmalıdır.')
    if 'allow_negative_balances' in value and not isinstance(value['allow_negative_balances'],bool):
        raise ValueError('Mənfi balans qaydası aktiv/deaktiv olmalıdır.')
    if value.get('currency','AZN')!='AZN':
        raise ValueError('Bu mərhələdə valyuta AZN olmalıdır.')
