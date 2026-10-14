"""Stable provider-neutral identities for tenant CRM snapshots.

Kommo identifiers are integers while other CRM systems can use UUIDs.  This
module deliberately keeps every external identifier as text and contains no
transport or database code.
"""
import re


_PROVIDER = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_ENTITY_TYPE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def provider_name(value: object) -> str:
    provider = str(value or "").strip().casefold()
    if not _PROVIDER.fullmatch(provider):
        raise ValueError("CRM provider adı düzgün deyil")
    return provider


def entity_type(value: object) -> str:
    kind = str(value or "").strip().casefold()
    if not _ENTITY_TYPE.fullmatch(kind):
        raise ValueError("CRM entity tipi düzgün deyil")
    return kind


def external_id(value: object) -> str:
    identity = str(value or "").strip()
    if not identity or len(identity) > 180 or "\x00" in identity:
        raise ValueError("CRM xarici identifikatoru düzgün deyil")
    return identity


def crm_identity(provider: object, kind: object, value: object) -> tuple[str, str, str]:
    """Return a validated (provider, entity_type, external_id) tuple."""
    return provider_name(provider), entity_type(kind), external_id(value)
