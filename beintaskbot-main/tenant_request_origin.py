"""Shared transport guard, independent of sessions and business authorization."""
from collections.abc import Mapping
from typing import Protocol

from tenant_linear_policy import TenantLinearError


class JsonPostRequest(Protocol):
    method: str
    content_type: str
    headers: Mapping[str, str]


def require_json_post(
    request: JsonPostRequest,
    canonical_origin: str,
    *,
    message: str = 'Sorğunun mənbəyi düzgün deyil.',
) -> None:
    """Keep the existing exact-origin contract; do not accept host suffixes.

    Invoke before reading JSON or performing any side effect. This is not
    membership, identity or resource authorization; those remain with callers.
    """
    if (request.method != 'POST'
            or request.content_type != 'application/json'
            or request.headers.get('Origin', '').rstrip('/') != canonical_origin.rstrip('/')):
        raise TenantLinearError(message, 403)
