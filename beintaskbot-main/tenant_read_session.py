"""HTTP identity guard only; resource authorization stays in each store."""
from collections.abc import Callable, Mapping
from typing import Protocol

from tenant_linear_policy import TenantLinearError


class ReadRequest(Protocol):
    query: Mapping[str, str]


def require_read_session(
    request: ReadRequest,
    member_from_request: Callable[[ReadRequest], Mapping[str, object] | None],
) -> Mapping[str, object]:
    """Compare exact query identity without normalizing user-controlled values.

    This does not certify current membership or access to a deal. Callers must
    still run their database/resource checks, including post-provider checks.
    """
    session = member_from_request(request)
    if not session:
        raise TenantLinearError('İcazə yoxdur.', 403)
    expected = (request.query.get('expected_tenant_id'),
                request.query.get('expected_user_id'))
    actual = (str(session['tenant_id']), str(session['telegram_id']))
    if expected != actual:
        raise TenantLinearError('Kabinet dəyişib. Yeniləyin.', 409)
    return session
