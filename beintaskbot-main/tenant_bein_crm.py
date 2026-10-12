"""BEIN ERP CRM integration-token transport.

The transport follows https://docs.beinsystems.az/docs/api/: branch scope is
query-based for reads and body-based for writes; every integration-token
mutation requires a stable Idempotency-Key supplied by the calling command.
"""
import asyncio
from dataclasses import dataclass, field
from urllib.parse import urlparse

import requests

from tenant_crm_provider import CrmProviderCapabilities, CrmProviderError


RESOURCE_PATHS = {
    "deals": "crm/leads",
    "tasks": "crm/tasks",
    "pipelines": "crm/pipelines",
    "stages": "crm/stages",
    "customers": "partners",
    "sources": "crm/sources",
    "lost_reasons": "crm/lost-reasons",
}


def normalize_base_url(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    parsed = urlparse(raw)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise CrmProviderError("BEIN ERP API ünvanı düzgün deyil.", status=400)
    return raw if parsed.path.rstrip("/").endswith("/api/v1") else raw + "/api/v1"


@dataclass(frozen=True)
class BeinCrm:
    base_url: str
    integration_token: str = field(repr=False)
    branch_id: str = ""
    timeout: int = 20
    name: str = "beinsystems"
    capabilities = CrmProviderCapabilities(notes=False, chats=False, webhooks=False)

    def __post_init__(self):
        object.__setattr__(self, "base_url", normalize_base_url(self.base_url))
        token = str(self.integration_token or "").strip()
        if not token.startswith("bei_int_") or len(token) < 24 or any(char.isspace() for char in token):
            raise CrmProviderError("BEIN ERP integration token düzgün deyil.", status=400)
        object.__setattr__(self, "integration_token", token)
        object.__setattr__(self, "branch_id", str(self.branch_id or "").strip())

    async def request(self, method: str, path: str, *, params: dict | None = None,
                      json_body: dict | None = None, idempotency_key: str = "") -> dict:
        verb = str(method or "GET").upper()
        if verb not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"}:
            raise CrmProviderError("BEIN ERP sorğu metodu dəstəklənmir.", status=400)
        relative = str(path or "").strip().lstrip("/")
        if not relative or ".." in relative.split("/") or "://" in relative:
            raise CrmProviderError("BEIN ERP resurs yolu düzgün deyil.", status=400)
        query = dict(params or {})
        body = dict(json_body or {})
        headers = {"Authorization": f"Bearer {self.integration_token}", "Accept": "application/json", "X-Locale": "az"}
        if verb in {"GET", "HEAD"}:
            if self.branch_id and "filter[branch_id]" not in query:
                query["filter[branch_id]"] = self.branch_id
        else:
            key = str(idempotency_key or "").strip()
            if not 8 <= len(key) <= 200:
                raise CrmProviderError("Dəyişiklik üçün etibarlı idempotency açarı tələb olunur.", status=400)
            headers["Idempotency-Key"] = key
            if self.branch_id and "branch_id" not in body:
                body["branch_id"] = self.branch_id

        def send() -> dict:
            try:
                response = requests.request(
                    verb, f"{self.base_url}/{relative}", params=query or None,
                    json=body if verb not in {"GET", "HEAD"} else None,
                    headers=headers, timeout=max(3, min(int(self.timeout), 60)),
                )
            except requests.RequestException as exc:
                raise CrmProviderError("BEIN ERP xidməti ilə əlaqə qurulmadı.", status=503) from exc
            try:
                payload = response.json() if response.content else {}
            except ValueError:
                payload = {}
            if response.status_code >= 400:
                safe = payload if isinstance(payload, dict) else {}
                raise CrmProviderError(
                    str(safe.get("message") or "BEIN ERP sorğusu yerinə yetirilmədi."),
                    status=response.status_code, code=str(safe.get("code") or ""),
                    request_id=str(safe.get("request_id") or response.headers.get("X-Request-Id") or ""),
                )
            if not isinstance(payload, dict) or payload.get("status") not in {None, "success"}:
                raise CrmProviderError("BEIN ERP cavabı düzgün deyil.")
            return payload

        return await asyncio.to_thread(send)

    async def list_resource(self, resource: str, *, page: int = 1, filters: dict | None = None) -> dict:
        path = RESOURCE_PATHS.get(str(resource or ""))
        if not path:
            raise CrmProviderError("CRM resursu dəstəklənmir.", status=400)
        return await self.request("GET", path, params={"page": max(1, int(page)), **(filters or {})})

    async def create(self, resource: str, values: dict, *, idempotency_key: str) -> dict:
        path = RESOURCE_PATHS.get(str(resource or ""))
        if not path:
            raise CrmProviderError("CRM resursu dəstəklənmir.", status=400)
        return await self.request("POST", path, json_body=values, idempotency_key=idempotency_key)

    async def update(self, resource: str, external_id: str, values: dict, *, idempotency_key: str) -> dict:
        path = RESOURCE_PATHS.get(str(resource or ""))
        identity = str(external_id or "").strip()
        if not path or not identity or "/" in identity:
            raise CrmProviderError("CRM resurs identifikatoru düzgün deyil.", status=400)
        return await self.request("PATCH", f"{path}/{identity}", json_body=values, idempotency_key=idempotency_key)
