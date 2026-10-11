"""Shared database and encryption primitives for tenant repositories."""
from __future__ import annotations

import json
import os

import psycopg
from psycopg.rows import dict_row
from cryptography.fernet import Fernet

from tenant_platform_schema import _ensure_schema


class TenantPlatformError(RuntimeError):
    pass


def _database_url() -> str:
    value = str(os.environ.get("DATABASE_URL") or "").strip()
    if not value:
        raise TenantPlatformError("Platforma bazası qoşulmayıb.")
    return value


def _connect():
    return psycopg.connect(_database_url(), row_factory=dict_row)


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _fernet() -> Fernet:
    """Return the mandatory at-rest encryption key for tenant secrets."""
    key = str(os.environ.get("TENANT_ENCRYPTION_KEY") or "").strip()
    if not key:
        raise TenantPlatformError("OAuth təhlükəsizlik açarı quraşdırılmayıb.")
    try:
        return Fernet(key.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise TenantPlatformError("OAuth təhlükəsizlik açarı düzgün deyil.") from exc


def _kommo_domain(value: str) -> str:
    domain = str(value or "").strip().lower().replace("https://", "").replace("http://", "").strip("/")
    if not domain.endswith(".kommo.com") or "/" in domain or " " in domain:
        raise TenantPlatformError("Kommo hesab ünvanını daxil edin. Məsələn: company.kommo.com")
    return domain
