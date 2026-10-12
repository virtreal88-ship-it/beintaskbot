"""Provider-neutral CRM boundary used by tenant integrations.

External IDs stay strings because Kommo uses integers while BEIN ERP uses
UUIDs.  Provider clients own wire formats; SaaS policy must not depend on them.
"""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CrmProviderCapabilities:
    deals: bool = True
    tasks: bool = True
    pipelines: bool = True
    stages: bool = True
    customers: bool = True
    notes: bool = False
    chats: bool = False
    webhooks: bool = False


class CrmProviderError(RuntimeError):
    """Safe provider failure that never contains credentials or response bodies."""

    def __init__(self, message: str, *, status: int = 502, code: str = "", request_id: str = ""):
        super().__init__(str(message or "CRM provider error")[:300])
        self.status = int(status)
        self.code = str(code or "")[:100]
        self.request_id = str(request_id or "")[:100]


class TenantCrmProvider(Protocol):
    name: str
    capabilities: CrmProviderCapabilities

    async def request(self, method: str, path: str, *, params: dict | None = None,
                      json_body: dict | None = None, idempotency_key: str = "") -> dict:
        """Execute one tenant-scoped provider request."""
