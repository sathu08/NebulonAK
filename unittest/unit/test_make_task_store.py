"""make_task_store: backends, explicit overrides, resolver wiring (offline)."""

from __future__ import annotations

import pytest

from nak.adapters.store_memory import InMemoryTaskStore
from nak.adapters.store_nebulondb import NebulonDBTaskStore
from nak.wiring import make_task_store


def test_memory_backend_and_unknown():
    assert isinstance(make_task_store("memory"), InMemoryTaskStore)
    assert isinstance(make_task_store(), InMemoryTaskStore)
    with pytest.raises(ValueError):
        make_task_store("bogus")


def test_explicit_values_skip_resolution():
    store = make_task_store("nebulondb", base_url="http://db:6969/api/NebulonDB",
                            user="admin", password="pw",
                            resolve_from_mind=False)
    assert isinstance(store, NebulonDBTaskStore)
    assert store._resolver is None
    assert store._explicit == {"base_url": True, "user": True,
                               "password": True}


def test_resolver_attached_by_default():
    store = make_task_store("nebulondb", base_url="http://db:6969/api/NebulonDB",
                            user="admin", password="pw")
    assert isinstance(store, NebulonDBTaskStore)
    assert store._resolver is not None
