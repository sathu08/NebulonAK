"""Deep-freeze helper for immutable domain values."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any


def deep_freeze(value: Any) -> Any:
    """Recursively convert dict/list/set values into immutable structures.

    ``MappingProxyType`` alone only protects the outer mapping, so nested
    containers are frozen here too. Scalars pass through unchanged.
    """
    if isinstance(value, Mapping):
        return MappingProxyType({k: deep_freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(deep_freeze(v) for v in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(deep_freeze(v) for v in value)
    return value
