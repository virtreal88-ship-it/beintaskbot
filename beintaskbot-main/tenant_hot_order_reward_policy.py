"""Fixed per-service AZN rewards; absent/zero means no automatic accrual."""
from decimal import Decimal, InvalidOperation


def reward_amount(value: object = '0.00') -> int:
    if not isinstance(value, str):
        raise ValueError('Xidmət haqqını mətn kimi yazın, məsələn 25.00.')
    try:
        amount = Decimal(value.strip())
        if not amount.is_finite() or amount < 0 or amount > Decimal('999999999.99') or amount != amount.quantize(Decimal('0.01')):
            raise ValueError()
        return int(amount * 100)
    except (InvalidOperation, ValueError, OverflowError):
        raise ValueError('Xidmət haqqı sıfır və ya müsbət AZN məbləği olmalıdır (iki qəpik rəqəmi).') from None


def service_reward(settings: dict, service_id: str) -> int:
    service = next((row for row in settings.get('services', []) if row['id'] == service_id), {})
    return reward_amount(service.get('reward_amount', '0.00'))
