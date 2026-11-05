"""C2: streaming events, budget resolution, diff_stat evidence."""
from nak_host.adapters.store_memory import InMemoryTaskStore
from nak_host.application import create_task, run_chain, run_task
from nak_host.application.loop import run_loop
from nak_host.domain import ResultStatus, TaskState

from test_loop import FakeExecutor, ScriptBrain, _ctx
from test_run_chain import _result


def _task(store, **overrides):
    values = {"goal": "do it", "workspace": "/tmp/ws", "max_steps": 4,
              "timeout": 60}
    values.update(overrides)
    task, _ = create_task(values.pop("goal"), store=store, **values)
    return task


def test_on_event_fires_per_call():
    store = InMemoryTaskStore()
    task = _task(store)
    block = ('```json\n{"tool": "file", "arguments": {"action": "read"}}\n```')
    events = []
    out = run_loop(task, None, store=store,
                   brain=ScriptBrain([block, "done"]),
                   executor=FakeExecutor(), ctx=_ctx(),
                   on_event=events.append)
    assert out["status"] == "done"
    tool_events = [e for e in events if e.get("tool") == "file"]
    assert len(tool_events) == 1
    assert tool_events[0]["ok"] is True
    assert tool_events[0]["verdict"] == "allow"
    assert any(e.get("type") == "loop_finished" for e in events)


def test_on_event_never_breaks_loop():
    store = InMemoryTaskStore()
    task = _task(store)
    block = ('```json\n{"tool": "file", "arguments": {"action": "read"}}\n```')

    def bad(event):
        raise RuntimeError("listener boom")

    out = run_loop(task, None, store=store,
                   brain=ScriptBrain([block, "done"]),
                   executor=FakeExecutor(), ctx=_ctx(), on_event=bad)
    assert out["status"] == "done"
    assert len(out["tool_calls"]) == 1


def test_budgets_from_inputs():
    store = InMemoryTaskStore()
    three = "\n".join(
        f'```json\n{{"tool": "file", "arguments": {{"action": "read", "path": "f{i}"}}}}\n```'
        for i in range(3))
    task = _task(store, max_steps=3, inputs={"max_calls_per_turn": 1},
                 goal="do it")
    out = run_loop(task, None, store=store,
                   brain=ScriptBrain([three, three, "done"]),
                   executor=FakeExecutor(), ctx=_ctx())
    assert len(out["tool_calls"]) == 2
    # Explicit kwarg beats inputs.
    task2 = _task(store, max_steps=3, inputs={"max_calls_per_turn": 1},
                  goal="do it")
    out2 = run_loop(task2, None, store=store,
                    brain=ScriptBrain([three, "done"]),
                    executor=FakeExecutor(), ctx=_ctx(),
                    max_calls_per_turn=3)
    assert len(out2["tool_calls"]) == 3


def test_diff_stat_in_evidence():
    from nak_host.domain.ports import ToolResult
    store = InMemoryTaskStore()
    task, run = create_task("do it", store=store, workspace="/tmp/ws",
                            max_steps=4, timeout=60)
    run = store.transition(task.id, run.version, TaskState.ASSIGNED, {})
    block = ('```json\n{"tool": "terminal", "arguments": {"action": "exec", '
             '"command": "git diff --stat"}}\n```')

    class DiffExecutor(FakeExecutor):
        def run(self, tool, args, ctx):
            return ToolResult(ok=True, data={"stdout": "diff --git a/x\n 1 file"})

    out = run_task(task, run, store=store, brain=ScriptBrain([block, "done"]),
                   executor=DiffExecutor(), worker_name="arivu")
    assert out["result"].evidence["diff_stat"] != ""
    # No-diff run keeps empty string.
    task2, run2 = create_task("do it", store=store, workspace="/tmp/ws",
                              max_steps=4, timeout=60)
    run2 = store.transition(task2.id, run2.version, TaskState.ASSIGNED, {})
    block2 = ('```json\n{"tool": "file", "arguments": {"action": "read"}}\n```')
    out2 = run_task(task2, run2, store=store,
                    brain=ScriptBrain([block2, "done"]),
                    executor=FakeExecutor(), worker_name="arivu")
    assert out2["result"].evidence["diff_stat"] == ""


def test_chain_step_events():
    store = InMemoryTaskStore()
    task, run = create_task("ship it", store=store, workspace="/tmp/ws",
                            max_steps=4, timeout=60)
    run = store.transition(task.id, run.version, TaskState.ASSIGNED, {})
    events = []

    def runner(child, child_run, step):
        return {"result": _result(ResultStatus.OK, "ok"), "run": child_run}

    out = run_chain(task, run, [
        {"worker": "arivu", "kind": "write", "goal": "write it"},
        {"worker": "ezhilan", "kind": "test", "goal": "test it"},
    ], store=store, step_runner=runner, on_event=events.append)
    assert out["ok"] is True
    kinds = [e.get("kind") for e in events
             if e.get("type") == "step_finished"]
    assert kinds == ["write", "test"]
