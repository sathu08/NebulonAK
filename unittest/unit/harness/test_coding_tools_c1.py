"""C1: coding toolset visible, runnable, and safely bounded."""
from nak_host.application.loop import allowed_tool_schemas
from nak_host.harness.craft.craft import load_craft
from nak_host.harness.engine import ExecutionHarness
from nak_host.utils.constant import FALLBACK_TOOLS, TERMINAL_ALLOW_BINARIES
from nak_host.wiring import _tools_loader, make_registry


def test_search_visible_to_coders():
    reg = make_registry()
    for name in ("arivu", "vizhi", "viyan"):
        names = [t["name"] for t in allowed_tool_schemas(name, reg)]
        assert "search" in names
        assert "file" in names
        assert "terminal" in names
    assessor = [t["name"] for t in allowed_tool_schemas("nunnan", reg)]
    assert "search" not in assessor


def test_search_tool_runs(tmp_workspace):
    h = ExecutionHarness(worker="arivu", user="t",
                         workspace_root=tmp_workspace,
                         approver=lambda *a, **k: True)
    h.run("file", {"action": "write", "path": "grep_me.txt",
                   "content": "needle haystack", "mode": "create"})
    import json
    out = json.loads(h.run("search", {"action": "grep", "pattern": "needle",
                                     "path": "."}))
    assert out.get("ok") is True
    assert out.get("count", 0) >= 1


def test_terminal_allows_pytest_ruff_refuses_commit(tmp_workspace):
    h = ExecutionHarness(worker="arivu", user="t",
                         workspace_root=tmp_workspace)
    assert "pytest" in TERMINAL_ALLOW_BINARIES
    assert "ruff" in TERMINAL_ALLOW_BINARIES
    assert h.inspect("terminal", {"action": "exec",
                                  "command": "pytest -q"})["ok"] is True
    assert h.inspect("terminal", {"action": "exec",
                                  "command": "ruff check ."})["ok"] is True
    import json as _json
    denied = _json.loads(h.run("terminal", {"action": "exec",
                                            "command": "git commit -m x"}))
    assert denied.get("ok") is False
    assert h.inspect("terminal", {"action": "exec",
                                  "command": "git diff --stat"})["ok"] is True


def test_build_craft_has_search_and_steps():
    build = load_craft("build")
    assert "search" in build["tools"]
    assert int(build.get("max_steps") or 0) >= 12
    test = load_craft("test")
    assert int(test.get("max_steps") or 0) >= 12


def test_tools_loader_fallback_is_coding_set():
    names = _tools_loader(registry="/no/such/tools.json")()
    assert names == ["file", "directory", "search", "terminal", "observe"]
    assert FALLBACK_TOOLS == ["file", "directory", "search", "terminal",
                              "observe"]
