"""SIMPLE permission tests — verdicts, rule resolution, inspect allow."""
from nak.harness.engine.permission import (
    verdict, load_permissions, rule_for,
)
from nak.harness.engine import ExecutionHarness


def test_verdict_normalization():
    assert verdict("allow") == "allow"
    assert verdict("ask") == "ask"
    assert verdict("deny") == "deny"
    assert verdict("ALLOW") == "allow"
    assert verdict("") == "deny"
    assert verdict(None) == "deny"
    assert verdict("whatever") == "deny"


def test_load_permissions_has_roles_and_workers():
    perms = load_permissions()
    assert "vizhi" in perms["workers"]
    assert "aayan" in perms["workers"]
    assert "arivu" in perms["workers"]
    assert "project_manager" in perms["roles"]
    assert "researcher" in perms["roles"]
    assert "developer" in perms["roles"]


def test_rule_for_vizhi_merges_role():
    perms = load_permissions()
    entry, who = rule_for(perms, "vizhi", "vizhi", "project_manager")
    assert who == "vizhi"
    assert entry is not None
    assert entry["tools"]["file"] == "allow"


def test_rule_for_unknown_worker_is_none():
    perms = load_permissions()
    entry, who = rule_for(perms, "ghost", "ghost", None)
    assert entry is None


def test_rule_for_default_unbound():
    perms = load_permissions()
    entry, who = rule_for(perms, None, None, None)
    assert who == "default"
    assert entry is not None


def test_inspect_allows_vizhi_read(tmp_workspace):
    h = ExecutionHarness(worker="vizhi", user="t", workspace_root=tmp_workspace)
    assert h.inspect("file", {"action": "read", "path": "x.md"})["ok"] is True
    assert h.inspect("observe", {"action": "capture", "path": "x.md"})["ok"] is True


def test_inspect_allows_arivu_read_but_tool_level(tmp_workspace):
    h = ExecutionHarness(worker="aayan", user="t", workspace_root=tmp_workspace)
    assert h.inspect("file", {"action": "read", "path": "x.md"})["ok"] is True
    assert h.inspect("file", {"action": "list", "path": "."})["ok"] is True


def test_inspect_allows_arivu_developer_read(tmp_workspace):
    h = ExecutionHarness(worker="arivu", user="t", workspace_root=tmp_workspace)
    assert h.inspect("file", {"action": "read", "path": "x.md"})["ok"] is True
    assert h.inspect("terminal", {"action": "exec", "command": "echo hi"})["ok"] is True


def test_nunnan_role_merge_grants_assessor_tools():
    perms = load_permissions()
    entry, who = rule_for(perms, "nunnan", "nunnan", "performance_evaluator")
    assert who == "nunnan" and entry is not None
    for tool in ("assertion", "trace", "scorecard"):
        assert entry["tools"][tool] == "allow"
    assert entry["tools"]["report"] == "allow"
    assert entry["capabilities"]["report.write"] == "ask"
    assert "output/**" in entry["resources"]["files"]["write"]


def test_nunnan_inspect_allows_checks_and_gates_report(tmp_workspace):
    h = ExecutionHarness(worker="nunnan", user="t", workspace_root=tmp_workspace)
    assert h.inspect("assertion", {"action": "check", "checks": [
        {"op": "eq", "expected": 1, "actual": 1}]})["ok"] is True
    assert h.inspect("scorecard", {"action": "score", "inputs": {"x": 1},
                                   "rules": [{"metric": "x", "min": 0,
                                              "max": 1}]})["ok"] is True
    assert h.inspect("trace", {"action": "trace", "path": "x.md"})["ok"] is True
    pending = h.inspect("report", {"action": "write", "sections": {"a": 1},
                                   "path": "output/report.md"})
    assert pending["ok"] is False and pending.get("need_approval") is True


def test_other_workers_cannot_use_assessor_tools(tmp_workspace):
    h = ExecutionHarness(worker="vizhi", user="t", workspace_root=tmp_workspace)
    for tool, args in (("assertion", {"action": "check", "checks": [
            {"op": "eq", "expected": 1, "actual": 1}]}),
            ("trace", {"action": "trace", "path": "x.md"}),
            ("scorecard", {"action": "score", "inputs": {},
            "rules": [{"metric": "x", "min": 0, "max": 1}]}),
            ("report", {"action": "write", "sections": {"a": 1}})):
        out = h.inspect(tool, args)
        assert out["ok"] is False and out.get("need_approval") is not True
