"""run_task tests (fake brain/executor, memory store)."""

from __future__ import annotations

import pytest

from nak_host.adapters.store_memory import InMemoryTaskStore
from nak_host.application import ApprovalLedger, create_task, run_task
from nak_host.domain import ExecutionContext, ResultStatus, TaskState, Verdict

from test_loop import FakeExecutor, ScriptBrain, _Cancel


def _assigned(store, **overrides):
    values = {"goal": "do it", "workspace": "/tmp/ws", "max_steps": 4,
              "timeout": 60}
    values.update(overrides)
    task, run = create_task(values.pop("goal"), store=store, **values)
    run = store.transition(task.id, run.version, TaskState.ASSIGNED, {})
    return task, run


def _ctx(worker="arivu"):
    ledger = ApprovalLedger()
    return ExecutionContext(
        task_id="t1", worker_id=worker, workspace="/tmp/ws",
        allowed_tools=frozenset({"file", "terminal"}), attempt=1,
        approval=None, policy=object(), authority=ledger,
        cancel=_Cancel())


def _run(task, run, store, brain, executor, **kwargs):
    return run_task(task, run, store=store, brain=brain, executor=executor,
                    **kwargs)


def test_happy_path_result_and_evidence():
    store = InMemoryTaskStore()
    task, run = _assigned(store)
    block = ('```json\n{"tool": "file", "arguments": {"action": "write", '
             '"path": "a.py"}}\n```')
    out = _run(task, run, store, brain=ScriptBrain([block, "done"]),
               executor=FakeExecutor(), worker_name="arivu")
    assert out["result"].status == ResultStatus.OK
    assert out["result"].files_changed == ("a.py",)
    assert out["run"].state == TaskState.RUNNING
    assert out["run"].result.status == ResultStatus.OK
    assert out["loop"]["status"] == "done"


def test_status_mapping():
    store = InMemoryTaskStore()
    block = '```json\n{"tool": "file", "arguments": {}}\n```'
    # Needs approval (no auto budget).
    task, run = _assigned(store)
    out = _run(task, run, store, brain=ScriptBrain([block] * 4),
               executor=FakeExecutor(verdicts={"file": Verdict.ASK}),
               worker_name="arivu", auto_steps=0)
    assert out["result"].status == ResultStatus.NEEDS_APPROVAL
    assert out["result"].needs_approval is True
    # Failed loop.
    task, run = _assigned(store)

    class Broken:
        def chat(self, *a, **k):
            raise RuntimeError("down")

    out = _run(task, run, store, brain=Broken(),
               executor=FakeExecutor(), worker_name="arivu")
    assert out["result"].status == ResultStatus.FAILED
    assert out["result"].blockers == ("loop did not complete",)


def test_terminal_commands_captured():
    store = InMemoryTaskStore()
    task, run = _assigned(store)
    block = ('```json\n{"tool": "terminal", "arguments": '
             '{"action": "exec", "command": "pytest -q"}}\n```')
    out = _run(task, run, store, brain=ScriptBrain([block, "green"]),
               executor=FakeExecutor(), worker_name="arivu")
    assert out["result"].commands_run == ("pytest -q",)


def test_requires_assigned_run():
    store = InMemoryTaskStore()
    task, run = create_task("do it", store=store, workspace="/tmp/ws")
    with pytest.raises(ValueError):
        _run(task, run, store, brain=ScriptBrain(["done"]),
             executor=FakeExecutor())
