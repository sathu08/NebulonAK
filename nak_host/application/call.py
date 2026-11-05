"""Named worker call: one worker, one task, one loop.

Depth-1 delegation by construction: factories arrive here and are NEVER
forwarded — run_task receives built instances only, so a called worker has
no handle to summon further workers. New code should prefer chains; this is
the escape hatch for targeted calls ("arivu, write this").
"""

from __future__ import annotations

from typing import Any

from nak_host.application.create_task import create_task
from nak_host.application.loop import allowed_tool_schemas
from nak_host.application.run_task import run_task
from nak_host.domain.state import TaskState
from nak_host.utils.constant import CALL_DEFAULT_MAX_STEPS
from nak_host.utils.constant import CALL_DEFAULT_TIMEOUT


def call_worker(name: str, goal: str, *, store: Any, worker_factory: Any,
                brain_factory: Any, harness_factory: Any,
                executor_factory: Any, memory_factory: Any = None,
                registry: Any = None, approver: Any = None,
                workspace: str = "", max_steps: int = CALL_DEFAULT_MAX_STEPS,
                timeout: int = CALL_DEFAULT_TIMEOUT,
                allowed_tools: Any = frozenset(), harness_kwargs: Any = None,
                task_kwargs: Any = None, on_event: Any = None,
                max_calls_per_turn: Any = None, obs_chars: Any = None,
                history_keep: Any = None, max_stalls: Any = None,
                auto_steps: Any = None) -> dict[str, Any]:
    """Build instances via injected factories and run one task loop.

    Factory protocols (duck-typed, no imports):
    * ``worker_factory(name, brain)`` -> worker (with .prompt()/.chat())
    * ``brain_factory()`` -> brain (with .chat(text, messages))
    * ``harness_factory(worker=..., **harness_kwargs)`` -> harness
    * ``executor_factory(harness)`` -> ToolExecutorPort
    * ``memory_factory(brain)`` -> MemoryPort (optional)

    Returns {task, run, result, loop}.
    """
    worker_name = str(name or "").strip()
    if not worker_name:
        raise ValueError("call_worker needs a worker name")
    if not str(goal or "").strip():
        raise ValueError("call_worker needs a goal")
    brain = brain_factory()
    worker = worker_factory(worker_name, brain)
    harness = harness_factory(worker=worker_name, **dict(harness_kwargs or {}))
    executor = executor_factory(harness)
    memory = memory_factory(brain) if callable(memory_factory) else None
    try:
        prompt = worker.prompt()
        identity = str(prompt) if prompt else ""
    except Exception:
        identity = ""
    tools = allowed_tool_schemas(worker_name, registry) if registry is not None else []
    session_ws = (str(workspace or "").strip()
                  or str(getattr(harness, "session_dir", "") or "").strip()
                  or "default")
    inputs: dict[str, Any] = {"goal": str(goal).strip(),
                              "worker": worker_name}
    for key, val in (("max_calls_per_turn", max_calls_per_turn),
                     ("obs_chars", obs_chars), ("history_keep", history_keep),
                     ("max_stalls", max_stalls), ("auto_steps", auto_steps)):
        if val is not None:
            inputs[key] = val
    task, run = create_task(
        str(goal).strip(), store=store, workspace=session_ws,
        max_steps=int(max_steps), timeout=int(timeout),
        allowed_tools=frozenset(allowed_tools or ()),
        inputs=inputs,
        **dict(task_kwargs or {}))
    run = store.transition(task.id, run.version, TaskState.ASSIGNED,
                           {"type": "task.assigned", "worker": worker_name})
    out = run_task(task, run, store=store, brain=brain, executor=executor,
                   memory=memory, tools=tools, worker_name=worker_name,
                   identity=identity, approver=approver, on_event=on_event,
                   max_calls_per_turn=max_calls_per_turn, obs_chars=obs_chars,
                   history_keep=history_keep, max_stalls=max_stalls,
                   auto_steps=auto_steps)
    return {"task": task, "run": out["run"], "result": out["result"],
            "loop": out["loop"]}
