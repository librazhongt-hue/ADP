"""Best-effort JSON-safe serialization shared by the SQL adapters
(``adp.memory.adapters.sql``, ``adp.observability.adapters.sql``,
``adp.core.adapters.sql``).

This is intentionally *not* a general object <-> Python-type round-trip
codec. ``to_json_safe`` turns dataclasses / enums / datetimes / nested
containers into plain JSON-serializable structures for storage and audit.
``from_json_safe`` is the identity function: decoded values come back as
plain dicts/lists/primitives, not reconstructed dataclass instances. Callers
that need typed objects back (e.g. re-hydrating a ``Decision``) must do that
themselves from the dict form -- see docs/02-LoopEngine设计.md §8 (A1) for
why the platform does not attempt full type-preserving persistence.
"""

from __future__ import annotations

import dataclasses
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any


def to_json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_json_safe(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(k): to_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_json_safe(v) for v in value]
    # Last resort: something we don't know how to serialize losslessly.
    return repr(value)


def from_json_safe(value: Any) -> Any:
    """Identity by design -- see module docstring."""

    return value


def ensure_utc(value: datetime | None) -> datetime | None:
    """SQLite (via aiosqlite) does not round-trip ``tzinfo`` through a
    ``DateTime(timezone=True)`` column. The SQL adapters only ever write UTC
    timestamps, so a naive value read back is re-tagged as UTC rather than
    left ambiguous.
    """

    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
