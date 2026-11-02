"""Optional immutable browser intent for native CRM requests."""
import re
from uuid import UUID


class CRMContextError(ValueError):
    def __init__(self, code, status=400):
        super().__init__(code)
        self.code, self.status = code, status


def require_expected_context(request, profile):
    keys = ('expected_tenant_id', 'expected_user_id')
    values = [request.query.getall(key, []) if hasattr(request.query, 'getall')
              else [request.query[key]] if key in request.query else [] for key in keys]
    if not any(values):
        return
    if any(len(items) != 1 for items in values):
        raise CRMContextError('contacts_invalid_query')
    tenant, actor = values[0][0], values[1][0]
    try:
        expected_tenant = str(UUID(str(tenant)))
        if not re.fullmatch(r'[0-9]{1,19}', str(actor)) or int(actor) <= 0:
            raise ValueError
    except (ValueError, TypeError):
        raise CRMContextError('contacts_invalid_query') from None
    if expected_tenant != str(profile['tenant_id']) or int(actor) != int(profile['telegram_id']):
        raise CRMContextError('contacts_context_changed', 409)
