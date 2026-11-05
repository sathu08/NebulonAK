"""DIFFICULT permission tests — fail-closed, ask-gates, resources, limits."""
import json
import pytest

from nak_host.harness.engine import ExecutionHarness
from nak_host.harness.engine.permission import (
    load_permissions, deny_all_permissions, audit_tool_coverage, merge_entries,
)


def _j(s):
    return json.loads(s)


def test_unknown_worker_denied(tmp_workspace):
    h = ExecutionHarness(worker="vizhi", user="t", workspace_root=tmp_workspace)
    out = h.inspect("file", {"action": "read", "path": "x.md"}, worker="ghost-xyz")
    assert out["ok"] is False
    assert "unknown worker" in out["error"]


def test_unknown_tool_denied(harness_vizhi):
    out = harness_vizhi.inspect("ghost-tool", {"action": "x"})
    assert out["ok"] is False
    assert "unknown tool" in out["error"]


def test_arivu_terminal_denied(harness_aayan):
    out = harness_aayan.inspect("terminal", {"action": "exec", "command": "echo hi"})
    assert out["ok"] is False
    assert "denied" in out["error"]


def test_arivu_write_denied(harness_aayan):
    out = harness_aayan.inspect("file", {"action": "write", "path": "a.txt", "content": "x"})
    assert out["ok"] is False
    assert "denied" in out["error"]


def test_arivu_developer_write_is_ask(harness_arivu):
    out = harness_arivu.inspect("file", {"action": "write", "path": "a.txt", "content": "x"})
    assert out["ok"] is False
    assert out.get("need_approval") is True


def test_arivu_developer_terminal_allowed(harness_arivu):
    out = harness_arivu.inspect("terminal", {"action": "exec", "command": "echo hi"})
    assert out["ok"] is True


def test_vizhi_write_is_ask_without_approver(harness_vizhi):
    out = harness_vizhi.inspect("file", {"action": "write", "path": "a.txt", "content": "x"})
    assert out["ok"] is False
    assert out.get("need_approval") is True


def test_vizhi_write_ask_with_approver_true(tmp_workspace):
    h = ExecutionHarness(worker="vizhi", user="t", workspace_root=tmp_workspace,
                         approver=lambda *a, **k: True)
    out = h.inspect("file", {"action": "write", "path": "a.txt", "content": "x"})
    assert out["ok"] is True


def test_vizhi_write_ask_with_approver_false(tmp_workspace):
    h = ExecutionHarness(worker="vizhi", user="t", workspace_root=tmp_workspace,
                         approver=lambda *a, **k: False)
    out = h.inspect("file", {"action": "write", "path": "a.txt", "content": "x"})
    assert out["ok"] is False
    assert out.get("need_approval") is True


def test_run_ask_denies_without_approver(harness_vizhi):
    out = _j(harness_vizhi.run("file", {"action": "write", "path": "need.txt", "content": "x"}))
    assert out["ok"] is False
    assert out.get("need_approval") is True


def test_resource_deny_wins_over_grant(tmp_workspace):
    h = ExecutionHarness(worker="vizhi", user="t", workspace_root=tmp_workspace)
    # password-like name hits resources.files.deny glob **/*password*
    out = h.inspect("file", {"action": "read", "path": "my-password-note.txt"})
    assert out["ok"] is False
    assert "denied" in out["error"] or "not granted" in out["error"]


def test_per_call_allow_list_intersection(harness_vizhi):
    out = harness_vizhi.inspect("file", {"action": "read", "path": "x.md"}, allow=["observe"])
    assert out["ok"] is False
    assert "not allowed" in out["error"]


def test_missing_permissions_file_is_deny_all(tmp_workspace, tmp_path):
    missing = str(tmp_path / "no-such-perms.json")
    h = ExecutionHarness(worker="vizhi", user="t", workspace_root=tmp_workspace,
                         permissions=missing, workers_file=None)
    # constructor with unknown worker name would raise; use unbound inspect
    h2 = ExecutionHarness(user="t", workspace_root=tmp_workspace, permissions=missing)
    out = h2.inspect("file", {"action": "read", "path": "x.md"})
    assert out["ok"] is False


def test_deny_all_builtin_shape():
    d = deny_all_permissions()
    assert d["workers"] == {} and d["roles"] == {}
    assert d["defaults"]["unknown_worker"] == "deny"


def test_disabled_worker_denied(tmp_workspace):
    perms = {
        "workers": {"vizhi": {"enabled": False, "tools": {"file": "allow"},
                              "capabilities": {"file.read": "allow"}}},
        "roles": {},
    }
    h = ExecutionHarness(worker="vizhi", user="t", workspace_root=tmp_workspace,
                         permissions=perms)
    out = h.inspect("file", {"action": "read", "path": "x.md"})
    assert out["ok"] is False
    assert "disabled" in out["error"]


def test_audit_coverage_runs():
    from nak_host.harness.engine.registry import load_registry
    perms = load_permissions()
    specs = load_registry()
    gaps = audit_tool_coverage(perms, specs)
    assert isinstance(gaps, dict)


def test_merge_enabled_ands():
    base = {"enabled": True, "tools": {"file": "allow"}, "capabilities": {},
            "resources": {"files": {"read": ["a"], "write": [], "deny": []}}, "limits": {}}
    over = {"enabled": False, "tools": {}, "capabilities": {},
            "resources": {"files": {"read": [], "write": [], "deny": []}}, "limits": {}}
    assert merge_entries(base, over)["enabled"] is False


def test_bind_unknown_raises(tmp_workspace):
    h = ExecutionHarness(user="t", workspace_root=tmp_workspace)
    with pytest.raises(ValueError, match="unknown worker"):
        h.bind("ghost-xyz")


def test_vizhi_vs_arivu_write_boundary(tmp_workspace):
    """Same call: vizhi asks, aayan denies (researcher boundary); arivu asks (developer)."""
    hv = ExecutionHarness(worker="vizhi", user="t", workspace_root=tmp_workspace)
    ha = ExecutionHarness(worker="aayan", user="t", workspace_root=tmp_workspace)
    hd = ExecutionHarness(worker="arivu", user="t", workspace_root=tmp_workspace)
    args = {"action": "write", "path": "shared.txt", "content": "x"}
    rv, ra, rd = hv.inspect(**{"name": "file", "args": args}), ha.inspect("file", args), hd.inspect("file", args)
    assert rv.get("need_approval") is True  # ask
    assert ra["ok"] is False and "denied" in ra["error"]
    assert rd.get("need_approval") is True  # developer ask
