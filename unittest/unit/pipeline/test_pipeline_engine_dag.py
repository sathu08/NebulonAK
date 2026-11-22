"""flow.engine DAG: validation rules + offline branch execution."""

from __future__ import annotations

import json

import pytest

from nak_host.flow import store as _store
from nak_host.flow.engine.chain import order_chain, validate_definition
from nak_host.flow.engine.runner import run_definition


def _node(nid, ntype, label="", config=None, x=0, y=0):
    return {"id": nid, "type": ntype, "label": label or nid, "x": x, "y": y, "config": config or {}}


def _edge(a, b, label=""):
    e = {"from_id": a, "to_id": b}
    if label:
        e["label"] = label
    return e


def _linear():
    return {
        "name": "t",
        "slug": "t",
        "nodes": [
            _node("n1", "prompt", config={"text": "hi"}),
            _node("n2", "tool", config={"tool": "file", "args": {"action": "read", "path": "x"}}),
        ],
        "edges": [_edge("n1", "n2")],
    }


def _branched():
    return {
        "name": "b",
        "slug": "b",
        "nodes": [
            _node("n1", "prompt", config={"text": "hi"}),
            _node("n2", "condition", config={"contains": "MAGIC"}),
            _node("n3", "tool", config={"tool": "file", "args": {"action": "read", "path": "a"}}),
            _node("n4", "tool", config={"tool": "file", "args": {"action": "read", "path": "b"}}),
        ],
        "edges": [_edge("n1", "n2"), _edge("n2", "n3", "true"), _edge("n2", "n4", "false")],
    }


def test_builtin_vetting_template_validates():
    t = _store.builtin_vetting_template()
    res = validate_definition(t)
    assert res["ok"], res["errors"]
    assert res["ordered"] == ["n1", "n2", "n3"]


def test_linear_ok():
    res = validate_definition(_linear())
    assert res["ok"], res["errors"]
    assert res["ordered"] == ["n1", "n2"]


def test_branched_true_first_order():
    res = validate_definition(_branched())
    assert res["ok"], res["errors"]
    assert res["ordered"][0] == "n1" and res["ordered"][1] == "n2"
    assert set(res["ordered"][2:]) == {"n3", "n4"}
    assert res["ordered"].index("n3") < res["ordered"].index("n4")  # true first


def test_cycle_rejected():
    d = _linear()
    d["edges"].append(_edge("n2", "n1"))
    res = validate_definition(d)
    assert not res["ok"]
    assert any("cycle" in e or ">1 incoming" in e for e in res["errors"])


def test_two_starts_rejected():
    d = {
        "name": "t",
        "slug": "t",
        "nodes": [_node("n1", "prompt", config={"text": "a"}), _node("n2", "prompt", config={"text": "b"})],
        "edges": [],
    }
    res = validate_definition(d)
    assert not res["ok"]
    assert any("1 start" in e for e in res["errors"])


def test_condition_needs_both_branches():
    d = _branched()
    d["edges"] = [e for e in d["edges"] if e.get("label") != "false"]
    res = validate_definition(d)
    assert not res["ok"]
    assert any("false" in e for e in res["errors"])


def test_join_rejected():
    d = {
        "name": "t",
        "slug": "t",
        "nodes": [
            _node("n1", "prompt", config={"text": "a"}),
            _node("n2", "prompt", config={"text": "b"}),
            _node("n3", "tool", config={"tool": "file", "args": {"action": "read", "path": "x"}}),
        ],
        "edges": [_edge("n1", "n3"), _edge("n2", "n3")],
    }
    res = validate_definition(d)
    assert not res["ok"]
    assert any("incoming" in e for e in res["errors"])


def test_per_type_config_required():
    for ntype, cfg in [
        ("prompt", {}),
        ("tool", {}),
        ("craft", {}),
        ("agent", {"goal": "g"}),
        ("agent", {"worker": "w"}),
        ("verify", {}),
        ("condition", {}),
    ]:
        d = {"name": "t", "slug": "t", "nodes": [_node("n1", ntype, config=cfg)], "edges": []}
        assert not validate_definition(d)["ok"], ntype
    d = {
        "name": "t",
        "slug": "t",
        "nodes": [
            _node("n1", "agent", config={"worker": "w", "goal": "g"}),
            _node("n2", "verify", config={"expect": "ok"}),
        ],
        "edges": [_edge("n1", "n2")],
    }
    assert validate_definition(d)["ok"]


def test_unknown_type_rejected():
    d = {"name": "t", "slug": "t", "nodes": [_node("n1", "nope", config={})], "edges": []}
    assert not validate_definition(d)["ok"]


def test_order_chain_raises():
    with pytest.raises(ValueError):
        order_chain({"name": "t", "nodes": [], "edges": []})


class _FakeHarness:
    session_id = "s"
    workspace_root = "/tmp"

    def __init__(self, content="the answer contains MAGIC"):
        self.content = content

    def run(self, name, args=None, **_kw):
        return json.dumps({"ok": True, "content": self.content})


class _FakeWorker:
    def __init__(self, answer="has MAGIC inside"):
        self._answer = answer

    def chat(self, text, session_id=None):
        return self._answer


def _patch_wiring(monkeypatch, answer="has MAGIC inside"):
    import nak_host.wiring as wiring

    monkeypatch.setattr(wiring, "make_harness", lambda worker=None, **_k: _FakeHarness(answer))
    monkeypatch.setattr(wiring, "make_worker", lambda name, brain=None, **_k: _FakeWorker(answer))
    monkeypatch.setattr(wiring, "make_brain", lambda **_k: object())
    monkeypatch.setattr(
        wiring, "run_craft", lambda *a, **_k: {"planned": True, "path": "p", "steps": ["s1"], "answer": "a"}
    )
    monkeypatch.setattr(
        wiring,
        "call_worker",
        lambda name, goal, **_k: {"task": None, "result": None, "loop": [], "answer": f"{name}:{goal}"},
    )


def test_run_takes_true_branch(monkeypatch):
    _patch_wiring(monkeypatch, answer="has MAGIC inside")
    res = run_definition(_branched(), worker="viyan", auto_approve=True)
    assert res["ok"], res
    by_id = {s["id"]: s for s in res["steps"]}
    assert by_id["n3"]["ok"] is True and "skipped" not in by_id["n3"]
    assert by_id["n4"].get("skipped") is True
    assert by_id["n2"]["branch"] == "true"


def test_run_takes_false_branch(monkeypatch):
    _patch_wiring(monkeypatch, answer="nothing relevant")
    res = run_definition(_branched(), worker="viyan", auto_approve=True)
    assert res["ok"], res
    by_id = {s["id"]: s for s in res["steps"]}
    assert by_id["n4"]["ok"] is True and "skipped" not in by_id["n4"]
    assert by_id["n3"].get("skipped") is True
    assert by_id["n2"]["branch"] == "false"


def test_verify_and_agent_steps(monkeypatch):
    _patch_wiring(monkeypatch, answer="all good")
    d = {
        "name": "t",
        "slug": "t2",
        "nodes": [
            _node("n1", "agent", config={"worker": "w", "goal": "do it"}),
            _node("n2", "verify", config={"expect": "w:do it"}),
        ],
        "edges": [_edge("n1", "n2")],
    }
    res = run_definition(d, worker="viyan", auto_approve=True)
    assert res["ok"], res
    assert res["steps"][1]["ok"] is True


def test_verify_failure_stops_run(monkeypatch):
    _patch_wiring(monkeypatch, answer="all good")
    d = {
        "name": "t",
        "slug": "t3",
        "nodes": [
            _node("n1", "prompt", config={"text": "hi"}),
            _node("n2", "verify", config={"expect": "zzz-missing"}),
            _node("n3", "tool", config={"tool": "file", "args": {"action": "read", "path": "x"}}),
        ],
        "edges": [_edge("n1", "n2"), _edge("n2", "n3")],
    }
    res = run_definition(d, worker="viyan", auto_approve=True)
    assert res["ok"] is False
    by_id = {s["id"]: s for s in res["steps"]}
    assert by_id["n2"]["ok"] is False
    assert by_id["n3"].get("skipped") is True


def test_dry_run_marks_all_dry():
    res = run_definition(_branched(), dry=True)
    assert res["ok"] is True and res["dry"] is True
    assert all(s.get("dry") for s in res["steps"])
    assert len(res["steps"]) == 4
