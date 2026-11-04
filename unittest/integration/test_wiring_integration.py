"""WIRING integration — worker + harness + craft + permission together.

End-to-end evaluation of how the created workers behave on
tools, permissions and crafts (offline FakeBrain, tmp workspace).
"""
import json
import pytest


def _j(s):
    return json.loads(s)


def test_workers_have_no_direct_harness_access():
    """Harness stays in the composition root; workers get session id only."""
    import inspect

    from nak.worker import GenericWorker
    from nak.wiring import make_worker

    assert "harness" not in inspect.signature(GenericWorker.__init__).parameters
    assert "harness" not in inspect.signature(make_worker).parameters
    assert not hasattr(GenericWorker("vizhi"), "harness")


def test_make_worker_injects_decider_and_policy(worker_vizhi):
    assert worker_vizhi._decider is not None
    assert worker_vizhi._policy_evaluator is not None
    assert worker_vizhi.brain is not None


def test_make_worker_bad_crafts_shape_raises():
    from nak.wiring import make_worker
    with pytest.raises(ValueError, match="'crafts' must be a list"):
        make_worker("vizhi", crafts="plan")


def test_make_harness_lists_tools(harness_vizhi):
    assert "file" in harness_vizhi.list()
    assert "observe" in harness_vizhi.list()


def test_run_craft_merges_into_harness_session_doc(tmp_workspace):
    """Harness session key is unknown to Mind -> AK owns the transcript and
    merges shared turns into the doc named by harness.session_id."""
    from nak.wiring import make_worker, make_harness, run_craft
    from conftest import FakeBrain

    brain = FakeBrain(answer="1. read notes\n2. summarize")
    w = make_worker("aayan", brain=brain)
    h = make_harness(worker="aayan", workspace_root=tmp_workspace,
                     approver=lambda info: True)
    res = run_craft("summarize notes", w, h, approver=lambda info: True)
    assert res["planned"] is True
    assert h.session_id in [d["id"] for d in brain.saved]


def test_run_craft_auto_approved_small(tmp_workspace):
    """Small plan (<= auto_steps) with approval=small runs ahead — aayan
    crafts=[plan] derives approval=small, so a 2-step plan auto-approves."""
    from nak.wiring import make_worker, make_harness, run_craft
    from conftest import FakeBrain

    brain = FakeBrain(answer="1. read notes\n2. summarize")
    w = make_worker("aayan", brain=brain)
    h = make_harness(worker="aayan", workspace_root=tmp_workspace,
                     approver=lambda info: True)
    res = run_craft("summarize notes", w, h, approver=lambda info: True)
    assert res["planned"] is True
    assert res["needs_approval"] is False
    assert res["approved"] is True


def test_run_craft_build_craft_auto_approved(tmp_workspace):
    """arivu developer crafts=[plan, build] (both small) auto-approves small builds."""
    from nak.wiring import make_worker, make_harness, run_craft
    from conftest import FakeBrain

    brain = FakeBrain(answer="1. read app.py\n2. fix bug")
    w = make_worker("arivu", brain=brain)
    assert w.can("build") is True
    h = make_harness(worker="arivu", workspace_root=tmp_workspace,
                     approver=lambda info: True)
    res = run_craft("fix login bug", w, h, approver=lambda info: True)
    assert res["planned"] is True
    assert res["needs_approval"] is False
    assert res["approved"] is True


def test_run_craft_pending_without_approver(worker_vizhi, tmp_workspace):
    from nak.wiring import make_harness, run_craft
    worker_vizhi.brain.answer = "1. step one\n2. step two\n3. step three\n4. step four"
    h = make_harness(worker="vizhi", workspace_root=tmp_workspace)
    res = run_craft("big task", worker_vizhi, h, approver=None)
    # vizhi default approval=all -> needs approval, stays pending
    assert res["planned"] is True
    assert res["needs_approval"] is True
    assert res["approved"] is False


def test_run_craft_no_steps(worker_vizhi, tmp_workspace):
    from nak.wiring import make_harness, run_craft
    worker_vizhi.brain.answer = "hello, just chatting, no plan"
    h = make_harness(worker="vizhi", workspace_root=tmp_workspace)
    res = run_craft("chat", worker_vizhi, h)
    assert res["planned"] is False
    assert "no numbered steps" in res["reason"]


def test_run_craft_blocked_by_policy(worker_vizhi, tmp_workspace):
    from nak.wiring import make_harness, run_craft
    worker_vizhi._policy_evaluator = lambda t, p: {"action": "clarify", "allowed": False,
                                                   "reasons": ["vague"], "policy": p}
    worker_vizhi._decider = lambda *a, **k: {"ok": True, "decision": {}}
    h = make_harness(worker="vizhi", workspace_root=tmp_workspace)
    res = run_craft("vague goal", worker_vizhi, h)
    assert res["planned"] is False
    assert "clarify" in res["reason"]


def test_end_to_end_worker_tool_permission_craft(tmp_workspace):
    """vizhi: chat (plan) -> harness file write needs approval -> craft expand."""
    from nak.wiring import make_worker, make_harness
    from nak.harness.craft import craft as Craft
    from conftest import FakeBrain

    brain = FakeBrain(answer="Plan:\n1. read input\n2. write output [worker=vizhi]")
    w = make_worker("vizhi", brain=brain)
    h = make_harness(worker="vizhi", workspace_root=tmp_workspace,
                     approver=lambda *a, **k: True)

    # 1. worker chats (crafts enforced: plan pin max 3 steps, 2-step answer survives)
    resp = w.chat("prepare output")
    steps = w.extract_plan(resp["answer"])
    assert len(steps) >= 1

    # 2. permission: write needs ask -> approver True lets inspect pass
    assert h.inspect("file", {"action": "write", "path": "out.txt",
                              "content": "data"})["ok"] is True
    # run it for real
    out = _j(h.run("file", {"action": "write", "path": "out.txt", "content": "data"}))
    assert out["ok"] is True

    # 3. craft: plan + build crafts expand with required inputs
    craft = Craft.load_craft("plan")
    expanded = Craft.expand_craft("plan", {"goal": "prepare output", "context": "none"})
    assert len(expanded) == len(craft["steps"])
    build = Craft.load_craft("build")
    bexpanded = Craft.expand_craft("build", {"goal": "fix bug",
                                             "plan": "1. edit login.py"})
    assert len(bexpanded) == len(build["steps"])

    # 4. aayan (researcher) CANNOT do the same write — boundary holds;
    #    arivu (developer) CAN with approval (ask)
    ha = make_harness(worker="aayan", workspace_root=tmp_workspace)
    denied = ha.inspect("file", {"action": "write", "path": "out.txt", "content": "x"})
    assert denied["ok"] is False
    hd = make_harness(worker="arivu", workspace_root=tmp_workspace,
                      approver=lambda *a, **k: True)
    assert hd.inspect("file", {"action": "write", "path": "out.txt",
                               "content": "x"})["ok"] is True


def test_decide_helper_offline_llm_false():
    from nak.wiring import decide
    res = decide("read notes.txt", llm=False)
    assert isinstance(res, dict)
    assert "ok" in res
