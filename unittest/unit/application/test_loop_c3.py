"""C3: autonomy max budget, auto-grant, coding defaults."""
import inspect
import json

from nak_host.adapters.store_memory import InMemoryTaskStore
from nak_host.application import create_task
from nak_host.application.loop import run_loop
from nak_host.domain import TaskState
from nak_host.worker.engine.autonomy import _autonomy_from_crafts

from test_loop import FakeExecutor, ScriptBrain, _ctx, _task
from nak_host.domain import Verdict


def _autonomy_case(tmp_path, crafts):
    wf = tmp_path / "workers.json"
    wf.write_text(json.dumps([{"name": "t1", "type": "coder",
                               "crafts": [c["mode"] for c in crafts]}]),
                  encoding="utf-8")
    (tmp_path / "crafts.json").write_text(json.dumps({"crafts": crafts}),
                                          encoding="utf-8")
    return _autonomy_from_crafts([c["mode"] for c in crafts], str(wf), "t1")


def _pin(mode, steps, auto=2, approval="small"):
    return {"mode": mode, "version": "1.0.0", "description": "d",
            "tools": ["file"], "max_steps": steps, "approval": approval,
            "auto_steps": auto}


def test_autonomy_uses_max_for_coders(tmp_path):
    a = _autonomy_case(tmp_path, [_pin("plan", 3), _pin("build", 12)])
    assert a["max_steps"] == 12
    assert a["auto_steps"] == 2
    solo = _autonomy_case(tmp_path, [_pin("plan", 3)])
    assert solo["max_steps"] == 3


def test_auto_grant_first_n_asks():
    # Task-scoped grant: one auto approval covers the whole task.
    from nak_host.application.loop import MAX_CALLS_FALLBACK
    assert MAX_CALLS_FALLBACK == 3
    store = InMemoryTaskStore()
    task = _task(store, max_steps=4)
    block = ('```json\n{"tool": "file", "arguments": {"action": "write"}}\n```')
    events = []
    out = run_loop(task, None, store=store,
                   brain=ScriptBrain([block] * 4),
                   executor=FakeExecutor(verdicts={"file": Verdict.ASK}),
                   ctx=_ctx(), approver=None, auto_steps=2,
                   on_event=events.append)
    assert out["status"] == "max_steps"
    assert len([c for c in out["tool_calls"] if c["ok"]]) == 4
    assert events  # auto grants still emit
    # No auto budget -> needs approval without an approver.
    task2 = _task(store, max_steps=4)
    out2 = run_loop(task2, None, store=store,
                    brain=ScriptBrain([block] * 4),
                    executor=FakeExecutor(verdicts={"file": Verdict.ASK}),
                    ctx=_ctx(), approver=None, auto_steps=0)
    assert out2["status"] == "needs_approval"
    assert len([c for c in out2["tool_calls"] if c["ok"]]) == 0


def test_auto_steps_exhausted_calls_approver():
    store = InMemoryTaskStore()
    task = _task(store, max_steps=4)
    block = ('```json\n{"tool": "file", "arguments": {"action": "write"}}\n```')
    seen = []

    def approver(info):
        seen.append(info)
        return True

    out = run_loop(task, None, store=store,
                   brain=ScriptBrain([block] * 4),
                   executor=FakeExecutor(verdicts={"file": Verdict.ASK}),
                   ctx=_ctx(), approver=approver, auto_steps=0)
    assert out["status"] == "max_steps"
    assert len(seen) >= 1
    assert len([c for c in out["tool_calls"] if c["ok"]]) == 4


def test_call_defaults_are_coding():
    from nak_host.wiring import call_worker as wired
    from nak_host.application.call import call_worker as app_call
    assert inspect.signature(wired).parameters["max_steps"].default == 12
    assert inspect.signature(wired).parameters["timeout"].default == 600
    assert inspect.signature(app_call).parameters["max_steps"].default == 12
    assert inspect.signature(app_call).parameters["timeout"].default == 600
    assert "auto_steps" in inspect.signature(app_call).parameters
    assert "auto_steps" in inspect.signature(wired).parameters
