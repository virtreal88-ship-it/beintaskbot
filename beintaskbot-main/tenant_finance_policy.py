"""Money parsing and tenant authority; no provider or DB dependencies."""
from decimal import Decimal,InvalidOperation
import uuid
from tenant_policy import TenantPolicy,positive_id


class TenantFinanceError(ValueError):
    def __init__(self,message: str,status: int=400):
        super().__init__(message)
        self.status=status


def finance_policy(profile: dict, *, write: bool=False) -> TenantPolicy:
    policy=TenantPolicy(profile)
    if profile.get('tenant_status','active') not in {'active','onboarding','ready_for_integration'}:
        raise TenantFinanceError('Şirkət kabineti aktiv deyil.',403)
    if not profile.get('tenant_id') or not positive_id(profile.get('telegram_id')) or profile.get('active') is not True or not policy.allows('finance'):
        raise TenantFinanceError('Maliyyə üçün icazəniz yoxdur.',403)
    if write and not policy.privileged:
        raise TenantFinanceError('Əməliyyat üçün administrator icazəsi lazımdır.',403)
    return policy


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
