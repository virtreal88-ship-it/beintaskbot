"""Framework-neutral result returned by extracted legacy web actions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LegacyActionResult:
    payload: dict[str, Any]
    status: int = 200
