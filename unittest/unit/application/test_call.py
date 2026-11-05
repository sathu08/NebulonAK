"""call_worker tests (fully injected fakes; depth-1 by construction)."""

from __future__ import annotations

import pytest

from nak_host.adapters.store_memory import InMemoryTaskStore
from nak_host.application import call_worker
from nak_host.domain import ResultStatus, TaskState

from test_loop import FakeExecutor, ScriptBrain


class _Worker:
    def __init__(self, name, brain):
        self.name = name
        self.brain = brain
        self.builds = []

    def prompt(self):
        return f"# {self.name}\nyou build things."


class _Harness:
    def __init__(self, worker=None, **kwargs):
        self.worker = worker
        self.session_dir = "/tmp/ws"


def _factories(brain, worker=None, harness=None, executor=None, memory=None,
               registry=None):
    made = {}

    def brain_factory():
        made["brain"] = brain
        return brain

    def worker_factory(name, new_brain):
        made["worker"] = _Worker(name, new_brain)
        return worker if worker is not None else made["worker"]

    def harness_factory(worker=None, **kwargs):
        made["harness"] = harness or _Harness(worker=worker)
        return made["harness"]

    def executor_factory(built):
        made["executor"] = executor or FakeExecutor()
        return made["executor"]

    def memory_factory(new_brain):
        made["memory"] = memory
        return memory

    return made, {"worker_factory": worker_factory,
                  "brain_factory": brain_factory,
                  "harness_factory": harness_factory,
                  "executor_factory": executor_factory,
                  "memory_factory": memory_factory,
                  "registry": registry}


def test_named_call_builds_and_runs():
    store = InMemoryTaskStore()
    made, factories = _factories(ScriptBrain(["done"]))
    out = call_worker("arivu", "write it", store=store, workspace="/tmp/ws",
                      **factories)
    assert out["result"].status == ResultStatus.OK
    assert out["result"].worker == "arivu"
    assert out["run"].state == TaskState.RUNNING
    assert made["worker"].name == "arivu"
    assert made["worker"].brain is made["brain"]
    # Depth-1: the returned graph carries instances, never factories.
    assert "worker_factory" not in out and "brain_factory" not in out


def test_rejects_empty_name_or_goal():
    store = InMemoryTaskStore()
    _, factories = _factories(ScriptBrain(["done"]))
    with pytest.raises(ValueError):
        call_worker("", "goal", store=store, **factories)
    with pytest.raises(ValueError):
        call_worker("arivu", "  ", store=store, **factories)


def test_tools_come_from_registry():
    class Reg:
        def tools(self):
            return [{"name": "file", "description": "f"}]

        def permissions(self):
            return {"workers": {"arivu": {"tools": {"file": "allow"}}}}

    store = InMemoryTaskStore()
    made, factories = _factories(ScriptBrain(["done"]), registry=Reg())
    out = call_worker("arivu", "write it", store=store, workspace="/tmp/ws",
                      **factories)
    assert out["result"].status == ResultStatus.OK
