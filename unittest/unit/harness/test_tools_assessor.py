"""Assessor tools — assertion / trace / scorecard / report (sandboxed)."""
import importlib
import json

from pathlib import Path

from nak.harness.engine.registry import load_registry
from nak.harness.tools import report as R
from nak.harness.tools.assertion import assertion
from nak.harness.tools.report import report
from nak.harness.tools.scorecard import scorecard
from nak.harness.tools.trace import trace

NAMES = ("assertion", "trace", "scorecard", "report")


def _j(fn, args):
    return json.loads(fn(args))


def test_assessor_tools_registered_and_importable():
    specs = {e["name"]: e for e in load_registry()}
    for name in NAMES:
        assert name in specs
        module = importlib.import_module(specs[name]["module"])
        assert callable(getattr(module, specs[name]["function"]))


def test_assertion_all_pass():
    out = _j(assertion, {"checks": [
        {"id": "eq", "op": "eq", "expected": 1, "actual": 1},
        {"id": "has", "op": "contains", "expected": "ok", "actual": "all ok"},
        {"id": "gte", "op": "gte", "expected": 2, "actual": 3},
        {"id": "rx", "op": "regex", "expected": r"^a\d+$", "actual": "a12"},
        {"id": "in", "op": "in", "actual": "b", "expected": ["a", "b"]},
    ]})
    assert out["ok"] is True and out["all_passed"] is True
    assert out["passed"] == 5 and out["failed"] == 0


def test_assertion_reports_failures_without_raising():
    out = _j(assertion, {"checks": [
        {"id": "bad", "op": "eq", "expected": 1, "actual": 2},
        {"id": "op", "op": "wat", "expected": 1, "actual": 1},
        {"id": "rx", "op": "regex", "expected": "(", "actual": "x"},
    ]})
    assert out["ok"] is True and out["all_passed"] is False
    assert out["failed"] == 3
    assert all("error" in r or r["passed"] is False for r in out["results"])


def test_assertion_suite_file_and_sandbox(tmp_path):
    suite = tmp_path / "suite.json"
    suite.write_text(json.dumps([{"op": "eq", "expected": 1, "actual": 1}]),
                     encoding="utf-8")
    out = _j(assertion, {"suite_path": "suite.json", "root": str(tmp_path)})
    assert out["ok"] is True and out["all_passed"] is True
    denied = _j(assertion, {"suite_path": "../suite.json", "root": str(tmp_path)})
    assert denied["ok"] is False


def test_assertion_rejects_empty_checks():
    assert _j(assertion, {})["ok"] is False
    assert _j(assertion, {"checks": []})["ok"] is False


def test_trace_matches_lines(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("start\nok line\nERROR boom\n", encoding="utf-8")
    out = _j(trace, {"path": "run.log", "pattern": "ERROR",
                     "root": str(tmp_path)})
    assert out["ok"] is True and out["matched"] == 1
    assert out["matches"][0]["line"] == 3
    assert "boom" in out["matches"][0]["text"]


def test_trace_tail_without_pattern(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("a\nb\nc\n", encoding="utf-8")
    out = _j(trace, {"path": "run.log", "tail": 2, "root": str(tmp_path)})
    assert out["ok"] is True and out["mode"] == "tail"
    assert [m["text"] for m in out["matches"]] == ["b", "c"]


def test_trace_bad_pattern_and_secret_denied(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("x\n", encoding="utf-8")
    (tmp_path / ".env").write_text("KEY=1\n", encoding="utf-8")
    assert _j(trace, {"path": "run.log", "pattern": "(",
                      "root": str(tmp_path)})["ok"] is False
    assert _j(trace, {"path": ".env", "root": str(tmp_path)})["ok"] is False


def test_scorecard_weighted_and_dotted_metrics():
    out = _j(scorecard, {
        "inputs": {"a": 0.5, "b": {"c": 3}},
        "rules": [
            {"id": "a", "metric": "a", "weight": 1, "min": 0, "max": 1},
            {"id": "c", "metric": "b.c", "weight": 3, "min": 1, "max": 3},
        ],
    })
    assert out["ok"] is True
    assert out["score"] == 0.875
    assert [r["normalized"] for r in out["breakdown"]] == [0.5, 1.0]


def test_scorecard_inverted_rule():
    out = _j(scorecard, {
        "inputs": {"risk": 0.8},
        "rules": [{"metric": "risk", "min": 0, "max": 1,
                   "higher_is_better": False}],
    })
    assert out["ok"] is True and out["score"] == 0.2


def test_scorecard_validation():
    assert _j(scorecard, {"inputs": {}, "rules": []})["ok"] is False
    assert _j(scorecard, {"inputs": {}, "rules": [{"metric": "x"}]})["ok"] is False
    assert _j(scorecard, {
        "inputs": {"x": 1},
        "rules": [{"metric": "x", "min": 2, "max": 1}],
    })["ok"] is False


def test_report_markdown_and_json(tmp_path):
    md = _j(report, {"title": "Inspect", "sections": {
        "Summary": "ok", "Checks": ["a", "b"]},
        "path": "output/reports/r.md", "root": str(tmp_path)})
    assert md["ok"] is True
    text = (tmp_path / "output" / "reports" / "r.md").read_text(encoding="utf-8")
    assert "# Inspect" in text and "## Summary" in text and "- a" in text
    js = _j(report, {"format": "json", "title": "Inspect",
                     "sections": {"score": 0.5},
                     "path": "output/reports/r.json", "root": str(tmp_path)})
    assert js["ok"] is True
    data = json.loads((tmp_path / "output" / "reports" / "r.json").read_text(encoding="utf-8"))
    assert data == {"title": "Inspect", "sections": {"score": 0.5}}


def test_report_overwrite_and_path_rules(tmp_path):
    args = {"sections": {"a": 1}, "path": "out/r.md", "root": str(tmp_path)}
    assert _j(report, args)["ok"] is True
    assert _j(report, args)["ok"] is False
    assert _j(report, {**args, "overwrite": True})["ok"] is True
    assert _j(report, {**args, "path": "out/r.txt"})["ok"] is False
    assert _j(report, {**args, "path": ".env"})["ok"] is False


def test_report_default_path_and_size_cap(tmp_path, monkeypatch):
    out = _j(report, {"sections": {"a": 1}, "root": str(tmp_path)})
    assert out["ok"] is True and out["relative"].startswith("output/reports/")
    monkeypatch.setattr(R, "MAX_BYTES", 8)
    big = _j(report, {"sections": {"a": "x" * 50}, "root": str(tmp_path)})
    assert big["ok"] is False


def _specs():
    return {e["name"]: e for e in load_registry()}


def _allow(*_a, **_k):
    return {"ok": True}


def test_run_tool_injects_session_root_for_suite(tmp_path):
    from nak.harness.engine.execution import run_tool

    session = tmp_path / "session"
    session.mkdir()
    (session / "suite.json").write_text(
        json.dumps([{"op": "eq", "expected": 1, "actual": 1}]), encoding="utf-8")
    counters = {}
    out = json.loads(run_tool("assertion", {"suite_path": "suite.json"},
                              _specs(), {}, {}, str(session), _allow, counters))
    assert out["ok"] is True and out["all_passed"] is True
    assert counters["calls"] == 1


def test_run_tool_report_stays_inside_session(tmp_path):
    from nak.harness.engine.execution import run_tool

    session = tmp_path / "session"
    session.mkdir()
    out = json.loads(run_tool("report", {"sections": {"a": 1}},
                              _specs(), {}, {}, str(session), _allow, {}))
    assert out["ok"] is True
    assert (session / "output" / "reports").is_dir()
    assert str(session.resolve()) in str(Path(out["path"]).resolve())


def test_new_tools_denied_without_permission(harness_vizhi):
    out = json.loads(harness_vizhi.run(
        "assertion", {"checks": [{"op": "eq", "expected": 1, "actual": 1}]}))
    assert out["ok"] is False


FORBIDDEN_PREFIXES = (
    "nak.worker", "nak.brain", "nak.decision", "nak.wiring",
    "nak.harness.craft", "nak.harness.engine.engine",
    "nak.harness.engine.execution", "nak.harness.engine.permission",
)
ALLOWED_NAK_IMPORTS = {"nak.utils.constant", "nak.harness.engine.sandbox"}


def test_tool_modules_respect_dependency_boundary():
    import ast

    for name in NAMES:
        module = importlib.import_module(f"nak.harness.tools.{name}")
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and not node.level:
                found = node.module or ""
            elif isinstance(node, ast.Import):
                found = ",".join(alias.name for alias in node.names)
            else:
                continue
            for mod in found.split(","):
                assert not mod.startswith(FORBIDDEN_PREFIXES), f"{name} imports {mod}"
                if mod.startswith("nak."):
                    assert mod in ALLOWED_NAK_IMPORTS, f"{name} imports {mod}"
