"""Sequential worker chains with handoff + bounded fix loop.

A chain runs steps in order on child tasks (one Task/TaskRun per step, linked
via ``inputs.from_task``). Each step's Result feeds the next step; a failing
``test`` step inserts a bounded repair step handled by the last ``write``
worker (or ``fix_with``). Review steps never auto-fix.

``step_runner(child_task, child_run, step) -> {result, run}`` is injected
(wiring closes over run_task with per-worker brains; tests use fakes), so
this module never builds workers, brains, harnesses or executors.
"""

from __future__ import annotations

from typing import Any

from nak.application.create_task import create_task
from nak.domain.result import ResultStatus
from nak.domain.state import TaskState
from nak.domain.task import Task, TaskRun

_FIX_KINDS = frozenset({"test"})


def run_chain(task: Task, run: TaskRun, steps: Any, *, store: Any,
              step_runner: Any, max_rounds: int = 3) -> dict[str, Any]:
    """Run steps sequentially. Returns {ok, results, rounds, run, reason}."""
    queue = [_check_step(s, i) for i, s in enumerate(list(steps or []))]
    if not queue:
        raise ValueError("run_chain needs at least one step")
    if run.state == TaskState.ASSIGNED:
        run = store.transition(task.id, run.version, TaskState.RUNNING,
                               {"type": "task.chain_started",
                                "steps": len(queue)})
    elif run.state != TaskState.RUNNING:
        raise ValueError(f"run_chain needs an ASSIGNED/RUNNING run, got {run.state}")
    else:
        store.append_event(task.id, {"type": "task.chain_started",
                                     "steps": len(queue)})
    results: list[dict[str, Any]] = []
    rounds = 0
    last_write = ""
    last_write_goal = ""
    child_no = 0
    index = 0
    while index < len(queue):
        step = queue[index]
        index += 1
        if step["kind"] == "write":
            last_write = step["worker"]
            last_write_goal = step["goal"]
        child, child_run = create_task(
            step["goal"], store=store,
            idempotency_key=f"{task.id}:step:{child_no}",
            inputs={"from_task": task.id, "step": step["kind"],
                    "worker": step["worker"]},
            workspace=task.workspace, max_steps=task.max_steps,
            timeout=task.timeout, allowed_tools=set(task.allowed_tools))
        child_no += 1
        child_run = store.transition(child.id, child_run.version,
                                     TaskState.ASSIGNED,
                                     {"type": "task.step_assigned",
                                      "worker": step["worker"],
                                      "kind": step["kind"]})
        try:
            out = step_runner(child, child_run, dict(step))
        except Exception as exc:
            return {"ok": False, "results": results, "rounds": rounds,
                    "run": run,
                    "reason": f"step runner failed: {exc}"}
        result = out.get("result")
        results.append({"worker": step["worker"], "kind": step["kind"],
                        "goal": step["goal"], "result": result,
                        "run": out.get("run")})
        store.append_event(task.id, {"type": "task.step_finished",
                                     "worker": step["worker"],
                                     "kind": step["kind"],
                                     "status": str(getattr(result, "status", "?"))})
        if (step["kind"] in _FIX_KINDS and result is not None
                and result.status != ResultStatus.OK and rounds < max_rounds):
            rounds += 1
            # The failure will be re-verified: it no longer counts against
            # the chain outcome (kept in history for audit).
            results[-1]["superseded"] = True
            fixer = str(step.get("fix_with") or "").strip() or last_write \
                or queue[0]["worker"]
            evidence = (getattr(result, "summary", "") + " "
                        + " ".join(getattr(result, "blockers", []) or ())).strip()
            origin = last_write_goal or step["goal"]
            queue.insert(index, {
                "worker": fixer, "kind": "write",
                "goal": (f"Fix these failures (round {rounds}/{max_rounds}), "
                         f"repair in place, do not start over:\n{evidence}\n\n"
                         f"Original task:\n{origin}"),
            })
            # Re-queue the failed step so the fix is actually re-verified.
            queue.insert(index + 1, dict(step))
    ok = all(r["result"] is not None and r["result"].status == ResultStatus.OK
             for r in results if not r.get("superseded"))
    return {"ok": ok, "results": results, "rounds": rounds, "run": run,
            "reason": "" if ok else "chain finished with failures"}


def _check_step(step: Any, index: int) -> dict[str, Any]:
    if not isinstance(step, dict):
        raise ValueError(f"step {index} must be a mapping")
    worker = str(step.get("worker", "") or "").strip()
    goal = str(step.get("goal", "") or "").strip()
    if not worker or not goal:
        raise ValueError(f"step {index} needs 'worker' and 'goal'")
    clean = dict(step)
    clean["worker"] = worker
    clean["goal"] = goal
    clean["kind"] = str(step.get("kind", "") or "write").strip().lower() or "write"
    return clean
