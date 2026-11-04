"""run_craft decision stage — tags win, decisions fill missing, audit in plan."""
from pathlib import Path

DECISION = {"enabled": True, "scope": "per_step", "mode": "endpoint",
            "assignment": "tags_win", "parallel": True, "timeout": 5.0}


def _worker(answer, name="vizhi"):
    from conftest import FakeBrain
    from nak.wiring import make_worker

    return make_worker(name, brain=FakeBrain(answer=answer))


def _factory():
    from conftest import FakeBrain

    return lambda: FakeBrain(answer="done")


def _must_not_route(*_args, **_kwargs):
    raise AssertionError("route must not run when decision is off")


def _harness(name, tmp_workspace, approver=None):
    from nak.wiring import make_harness

    return make_harness(worker=name, workspace_root=tmp_workspace,
                        approver=approver)


def test_decision_fills_missing_and_keeps_tags(tmp_workspace):
    from nak.wiring import run_craft

    w = _worker("1. read notes\n2. summarize [worker=aayan]")
    calls = []

    def route(text, **_kwargs):
        calls.append(text)
        return {"ok": True, "worker": "arivu", "decision": {"risk": 0}}

    w.route = route
    h = _harness("vizhi", tmp_workspace, approver=lambda i: True)
    res = run_craft("summarize notes", w, h, approver=lambda i: True,
                    decision=DECISION, team=None, brain_factory=_factory())

    assert res["planned"] is True
    assert len(calls) == 1 and "read notes" in calls[0]
    assert res["assigned_workers"] == ["arivu", "aayan"]
    assert any("[worker=arivu]" in s for s in res["steps"])
    assert any("[worker=aayan]" in s for s in res["steps"])
    assert res["decision"]["items"][0]["worker"] == "arivu"
    assert res["decision"]["items"][0]["source"] == "decision"

    plan = Path(res["path"]).read_text(encoding="utf-8")
    assert "## Decision" in plan
    assert "step 1: arivu (decision)" in plan
    assert "step 2" not in plan.split("## Decision", 1)[1].split("## Approvals")[0]


def test_tags_win_needs_no_decisions_when_every_step_is_tagged(tmp_workspace):
    from nak.wiring import run_craft

    w = _worker("1. read notes [worker=aayan]\n2. summarize [worker=arivu]")

    def route(*_a, **_k):
        raise AssertionError("route must not run when tags already cover the plan")

    w.route = route
    h = _harness("vizhi", tmp_workspace, approver=lambda i: True)
    res = run_craft("summarize notes", w, h, approver=lambda i: True,
                    decision=DECISION, brain_factory=_factory())

    assert res["planned"] is True
    assert res["assigned_workers"] == ["aayan", "arivu"]
    assert "tags only" in res["decision"]["status"]


def test_failed_decision_blocks_step_and_forces_approval(tmp_workspace):
    from nak.wiring import run_craft

    w = _worker("1. read notes\n2. summarize", name="aayan")
    w.route = lambda *_a, **_k: {"ok": False, "error": "endpoint down"}
    h = _harness("aayan", tmp_workspace)
    res = run_craft("summarize notes", w, h, approver=None,
                    decision=DECISION, brain_factory=_factory())

    assert res["planned"] is True
    assert res["needs_approval"] is True
    assert res["approved"] is False
    assert res["decision"]["blocked"] == ["1", "2"]
    assert all("[decision-blocked]" in s for s in res["steps"])
    plan = Path(res["path"]).read_text(encoding="utf-8")
    assert "decision-blocked" in plan
    assert "endpoint down" in plan


def test_decision_parallel_false_is_visible_in_evidence(tmp_workspace):
    from nak.wiring import run_craft

    w = _worker("1. read notes\n2. summarize")
    w.route = lambda *_a, **_k: {"ok": True, "worker": "arivu"}
    h = _harness("vizhi", tmp_workspace, approver=lambda i: True)
    res = run_craft("summarize notes", w, h, approver=lambda i: True,
                    decision=DECISION, decision_parallel=False,
                    brain_factory=_factory())

    assert res["decision"]["parallel"] is False
    assert "serial" in res["decision"]["status"]


def test_decision_off_leaves_untagged_steps_alone(tmp_workspace):
    from nak.wiring import run_craft

    w = _worker("1. read notes\n2. summarize")
    w.route = _must_not_route
    h = _harness("vizhi", tmp_workspace, approver=lambda i: True)
    res = run_craft("summarize notes", w, h, approver=lambda i: True,
                    decision=False, brain_factory=_factory())

    assert res["planned"] is True
    assert res["decision"] is None
    assert res["assigned_workers"] == []
    assert all("[worker=" not in s for s in res["steps"])
    plan = Path(res["path"]).read_text(encoding="utf-8")
    assert "## Decision" not in plan


def test_worker_decision_disabled_skips_enabled_craft_block(tmp_workspace):
    from nak.wiring import run_craft

    w = _worker("1. read notes\n2. summarize")
    w.entry["decision"] = dict(w.entry["decision"], enabled=False)
    w.route = _must_not_route
    h = _harness("vizhi", tmp_workspace, approver=lambda i: True)
    res = run_craft("summarize notes", w, h, approver=lambda i: True,
                    craft="assign", brain_factory=_factory())

    assert res["planned"] is True
    assert res["assigned_workers"] == []
    assert "disabled" in res["decision"]["status"]


def test_decision_off_still_dispatches_existing_tags(tmp_workspace):
    from conftest import FakeBrain
    from nak.wiring import make_worker, run_craft

    w = _worker("1. research API [worker=aayan]\n2. fix login [worker=arivu]")
    w.route = _must_not_route
    h = _harness("vizhi", tmp_workspace, approver=lambda i: True)

    def factory(name):
        return make_worker(name, brain=FakeBrain(answer=f"{name} done"))

    res = run_craft("ship login", w, h, approver=lambda i: True,
                    team="core", decision=False, factory=factory)

    assert res["decision"] is None
    assert res["assigned_workers"] == ["aayan", "arivu"]
    assert set(res["workers"]) == {"aayan", "arivu"}
    assert len(res["runs"]) == 2 and all(r["ok"] for r in res["runs"])


def test_core_team_assign_craft_routes_multiple_workers_in_parallel(tmp_workspace):
    from conftest import FakeBrain
    from nak.wiring import make_worker, run_craft

    w = _worker("1. research API\n2. fix login\n3. summarize")
    seen = []

    def route(text, **kwargs):
        seen.append((text, tuple(kwargs.get("workers") or ())))
        if "fix" in text:
            return {"ok": True, "worker": "arivu"}
        return {"ok": True, "worker": "aayan"}

    w.route = route
    h = _harness("vizhi", tmp_workspace, approver=lambda i: True)

    def factory(name):
        return make_worker(name, brain=FakeBrain(answer=f"{name} done"))

    res = run_craft("ship login", w, h, approver=lambda i: True,
                    team="core", craft="assign", factory=factory)

    assert res["planned"] is True
    assert res["decision"]["parallel"] is True
    assert "parallel" in res["decision"]["status"]
    assert res["assigned_workers"] == ["aayan", "arivu"]
    assert set(res["workers"]) == {"aayan", "arivu"}
    assert len(res["runs"]) == 2 and all(r["ok"] for r in res["runs"])
    roster = {"aayan", "arivu", "ezhilan", "thiran", "viyan"}
    assert all(set(names) <= roster for _text, names in seen)
    assert all({"aayan", "arivu"} <= set(names) for _text, names in seen)


def test_decision_only_overrides_existing_tag(tmp_workspace):
    from nak.wiring import run_craft

    w = _worker("1. read notes [worker=aayan]")
    w.route = lambda *_a, **_k: {"ok": True, "worker": "arivu"}
    h = _harness("vizhi", tmp_workspace, approver=lambda i: True)
    cfg = dict(DECISION, assignment="decision_only")
    res = run_craft("summarize notes", w, h, approver=lambda i: True,
                    decision=cfg, brain_factory=_factory())

    assert res["assigned_workers"] == ["arivu"]
    assert "[worker=aayan]" not in res["steps"][0]
    assert "[worker=arivu]" in res["steps"][0]
