"""Verify a task: one test/review step via run_chain + VERIFIED transition.

A thin wrapper, not a new engine: verification is a single-step chain run by
a tester/reviewer worker, and success moves the parent RUNNING -> VERIFIED.
Anything else leaves the parent state untouched for the fix loop or report.
"""

from __future__ import annotations

from typing import Any

from nak.application.run_chain import run_chain
from nak.domain.state import TaskState
from nak.domain.task import Task, TaskRun


def verify_task(task: Task, run: TaskRun, *, store: Any,
                tester: str = "ezhilan", kind: str = "test",
                **chain_kwargs: Any) -> dict[str, Any]:
    """Run one verification step. Returns {verified, result, run, chain}."""
    worker = str(tester or "").strip()
    if not worker:
        raise ValueError("verify_task needs a tester worker name")
    mode = str(kind or "test").strip().lower()
    criteria = "\n".join(f"- {a}" for a in (task.acceptance or ())) or "- (none stated)"
    if mode == "review":
        goal = ("Review this work and report findings by severity with file "
                "evidence. Do not modify anything.\n"
                f"Goal: {task.goal}\nAcceptance:\n{criteria}")
    elif mode == "test":
        goal = ("Verify this work against the acceptance criteria. Run the "
                "tests and report exact pass/fail evidence. Do not modify "
                "anything except test scaffolding the task requires.\n"
                f"Goal: {task.goal}\nAcceptance:\n{criteria}")
    else:
        raise ValueError(f"unknown verify kind {kind!r} (want test|review)")
    chain = run_chain(task, run, [{"worker": worker, "kind": mode,
                                   "goal": goal}],
                      store=store, **chain_kwargs)
    verified = bool(chain.get("ok")) and bool(chain.get("results"))
    if verified:
        fresh = chain["run"]
        new_run = store.transition(task.id, fresh.version, TaskState.VERIFIED,
                                   {"type": "task.verified", "by": worker,
                                    "kind": mode})
        return {"verified": True, "result": chain["results"][-1]["result"],
                "run": new_run, "chain": chain}
    return {"verified": False,
            "result": chain["results"][-1]["result"] if chain.get("results") else None,
            "run": chain["run"], "chain": chain}
