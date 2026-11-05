"""Registry adapter tests: delegation, validation, cache (offline)."""

from __future__ import annotations

import json

from pathlib import Path

import pytest

from nak_host.adapters.registry_json import JsonRegistry

MANIFESTS = Path(__file__).resolve().parents[3] / "nak_host" / "manifests"


def _registry(**overrides):
    values = {
        "workers_file": MANIFESTS / "workers.json",
        "tools_file": MANIFESTS / "tools.json",
        "permissions_file": MANIFESTS / "permissions.json",
    }
    values.update(overrides)
    return JsonRegistry(**values)


def test_workers_teams_match_direct_loaders():
    from nak_host.worker.engine.registry import list_workers

    reg = _registry()
    assert [e["name"] for e in reg.workers()] == [e["name"] for e in list_workers()]
    assert "core" in [t["name"] for t in reg.teams()]
    assert reg.team("core")["leader"] == "vizhi"
    with pytest.raises(ValueError):
        reg.team("ghost-team")


def test_crafts_tools_permissions_smoke():
    reg = _registry()
    crafts = reg.crafts()
    assert crafts and all(c["name"] and c["description"] for c in crafts)
    tools = reg.tools()
    assert "file" in [t["name"] for t in tools]
    perms = reg.permissions()
    assert isinstance(perms.get("workers"), dict) and "vizhi" in perms["workers"]


def test_bad_worker_entry_is_fail_loud(tmp_path):
    # The underlying loader already fails loud with file context; the
    # adapter preserves that error unchanged (parity, no conversion).
    bad = tmp_path / "workers.json"
    bad.write_text(json.dumps([{"type": "coder"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="name"):
        _registry(workers_file=bad).workers()


def _minimal_entry(name):
    return {"name": name, "type": "coder",
            "identity": {"role": "developer", "personality": [],
                         "tone": "x", "formality": "professional",
                         "humor": "none", "gender": "unspecified",
                         "values": [], "do_not": [], "language": "en"},
            "memory": {"enabled": True}}


def test_missing_file_keeps_loader_error(tmp_path):
    missing = tmp_path / "no-such-workers.json"
    with pytest.raises((OSError, ValueError)):
        _registry(workers_file=missing).workers()


def test_cache_returns_copies_and_refreshes(tmp_path):
    workers = tmp_path / "workers.json"
    workers.write_text(json.dumps([_minimal_entry("solo")]), encoding="utf-8")
    reg = _registry(workers_file=workers)
    first = reg.workers()
    assert [e["name"] for e in first] == ["solo"]
    first.append({"name": "mutant"})
    assert [e["name"] for e in reg.workers()] == ["solo"]
    workers.write_text(json.dumps([_minimal_entry("duo")]), encoding="utf-8")
    assert [e["name"] for e in reg.workers()] == ["duo"]
    JsonRegistry.refresh()


def test_wiring_loaders_delegate():
    from nak_host.wiring import _tools_loader, _workers_loader

    names = [e["name"] for e in _workers_loader()()]
    assert "vizhi" in names and "arivu" in names
    tools = _tools_loader()()
    assert "file" in tools
    assert _tools_loader(None, "/no/such/tools.json")() == [
        "file", "directory", "search", "terminal", "observe"]
