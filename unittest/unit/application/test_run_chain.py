"""run_chain + verify_task tests (fake step runners, memory store)."""

from __future__ import annotations

import pytest

from nak_host.adapters.store_memory import InMemoryTaskStore
from nak_host.application import create_task, run_chain
from nak_host.domain import Result, ResultStatus, TaskState


def _parent(store, **overrides):
    values = {"goal": "ship it", "workspace": "/tmp/ws", "max_steps": 4,
              "timeout": 60}
    values.update(overrides)
    task, run = create_task(values.pop("goal"), store=store, **values)
    run = store.transition(task.id, run.version, TaskState.ASSIGNED, {})
    return task, run


def _result(status=ResultStatus.OK, summary="ok"):
    return Result(task_id="c", worker="w", status=status, summary=summary,
                  files_changed=[], commands_run=[], evidence={},
                  blockers=[] if status == ResultStatus.OK else ["bad"],
                  needs_approval=False)


def _runner(results):
    """Step runner popping scripted (status, summary) pairs."""
    calls = []

    def run(child, child_run, step):
        calls.append((child.id, step["worker"], step["kind"]))
        status, summary = results.pop(0)
        result = _result(status, summary)
        return {"result": result, "run": child_run}

    run.calls = calls
    return run


def test_happy_path_handoff():
    store = InMemoryTaskStore()
    task, run = _parent(store)
    out = run_chain(task, run, [
        {"worker": "arivu", "kind": "write", "goal": "write it"},
        {"worker": "ezhilan", "kind": "test", "goal": "test it"},
    ], store=store, step_runner=_runner([(ResultStatus.OK, "wrote"),
                                         (ResultStatus.OK, "green")]))
    assert out["ok"] and out["rounds"] == 0
    assert [r["worker"] for r in out["results"]] == ["arivu", "ezhilan"]
    assert [r["kind"] for r in out["results"]] == ["write", "test"]


def test_fail_fix_pass():
    store = InMemoryTaskStore()
    task, run = _parent(store)
    runner = _runner([(ResultStatus.OK, "wrote"),
                      (ResultStatus.FAILED, "red"),
                      (ResultStatus.OK, "fixed"),
                      (ResultStatus.OK, "green")])
    out = run_chain(task, run, [
        {"worker": "arivu", "kind": "write", "goal": "write it"},
        {"worker": "ezhilan", "kind": "test", "goal": "test it"},
    ], store=store, step_runner=runner, max_rounds=3)
    assert out["ok"] and out["rounds"] == 1
    kinds = [(c[1], c[2]) for c in runner.calls]
    assert kinds == [("arivu", "write"), ("ezhilan", "test"),
                     ("arivu", "write"), ("ezhilan", "test")]
    assert [r["result"].status for r in out["results"]].count(
        ResultStatus.OK) == 3


def test_exhaustion_and_review_never_fixes():
    store = InMemoryTaskStore()
    task, run = _parent(store)
    out = run_chain(task, run, [
        {"worker": "arivu", "kind": "write", "goal": "write it"},
        {"worker": "ezhilan", "kind": "test", "goal": "test it"},
    ], store=store, step_runner=_runner([(ResultStatus.OK, "w"),
                                         (ResultStatus.FAILED, "r1"),
                                         (ResultStatus.FAILED, "r2"),
                                         (ResultStatus.FAILED, "r3")]),
        max_rounds=1)
    assert not out["ok"] and out["rounds"] == 1
    assert out["reason"] == "chain finished with failures"

    task2, run2 = _parent(store)
    out = run_chain(task2, run2, [
        {"worker": "arivu", "kind": "write", "goal": "write it"},
        {"worker": "thiran", "kind": "review", "goal": "review it"},
    ], store=store, step_runner=_runner([(ResultStatus.OK, "w"),
                                         (ResultStatus.FAILED, "findings")]))
    assert not out["ok"] and out["rounds"] == 0


def test_bad_steps_rejected():
    store = InMemoryTaskStore()
    task, run = _parent(store)
    with pytest.raises(ValueError):
        run_chain(task, run, [], store=store, step_runner=_runner([]))
    with pytest.raises(ValueError):
        run_chain(task, run, [{"worker": "", "goal": "x"}],
                  store=store, step_runner=_runner([]))


def test_verify_transitions_on_success(monkeypatch):
    import importlib

    verify_module = importlib.import_module("nak_host.application.verify_task")

    store = InMemoryTaskStore()
    task, run = _parent(store)
    run = store.transition(task.id, run.version, TaskState.RUNNING, {})
    result = _result(ResultStatus.OK, "green")

    def fake_chain(task_arg, run_arg, steps, **kwargs):
        assert steps[0]["worker"] == "ezhilan"
        assert "Acceptance" in steps[0]["goal"]
        return {"ok": True, "results": [{"result": result}], "rounds": 0,
                "run": run_arg}

    monkeypatch.setattr(verify_module, "run_chain", fake_chain)
    out = verify_module.verify_task(task, run, store=store)
    assert out["verified"] and out["result"].status == ResultStatus.OK
    assert store.get_task(task.id)[1].state == TaskState.VERIFIED


def test_verify_leaves_state_on_failure(monkeypatch):
    import importlib

    verify_module = importlib.import_module("nak_host.application.verify_task")

    store = InMemoryTaskStore()
    task, run = _parent(store)
    run = store.transition(task.id, run.version, TaskState.RUNNING, {})

    def fake_chain(task_arg, run_arg, steps, **kwargs):
        return {"ok": False, "results": [{"result": _result(ResultStatus.FAILED, "red")}],
                "rounds": 0, "run": run_arg}

    monkeypatch.setattr(verify_module, "run_chain", fake_chain)
    out = verify_module.verify_task(task, run, store=store)
    assert not out["verified"]
    assert store.get_task(task.id)[1].state == TaskState.RUNNING
    with pytest.raises(ValueError):
        verify_module.verify_task(task, run, store=store, kind="bogus")
