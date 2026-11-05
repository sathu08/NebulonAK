"""Plan a task with an injected planner + harness.

Duck-typed dependencies (no concrete imports, same boundary rule as
``nak.worker.engine.dispatch``):

* ``planner`` — ``.chat(text, session_id)`` reply plus ``.extract_plan()``,
  ``.extract_assigned()`` and ``.needs_plan_approval()`` (GenericWorker
  provides all of these);
* ``harness`` — ``.session_id``, ``.start_plan()`` and ``.approve_plan()``;
* ``route_steps`` — optional ``(answer, candidates) ->
  (new_answer, items, meta)`` closure for decision routing (wiring supplies
  one over ``_apply_decisions``; ``None`` disables routing).

Returns a legacy-shaped outcome dict (``planned``/``approved``/``answer``/
``triage``/``steps``/``path``/``assigned_workers``/``decision``/
``session_id``/``reason``) plus the current ``run`` for the next stage.
"""

from __future__ import annotations

from typing import Any
from collections.abc import Callable

from nak.domain.state import TaskState
from nak.domain.task import Task, TaskRun


def plan_task(task: Task, run: TaskRun, *, store: Any, planner: Any,
              harness: Any, approver: Any = None,
              team_spec: dict[str, Any] | None = None,
              candidates: list[str] | None = None,
              known_workers: list[str] | None = None,
              route_steps: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Run the planner, persist the plan, and gate approval."""
    sid = getattr(harness, "session_id", None)
    goal = task.goal
    run = store.transition(task.id, run.version, TaskState.PLANNED,
                           {"type": "task.planned", "goal": goal})
    try:
        resp = planner.chat(goal, session_id=sid)
    except Exception as exc:
        run = store.transition(task.id, run.version, TaskState.FAILED,
                               {"type": "task.failed",
                                "reason": f"worker chat failed: {str(exc)[:150]}"})
        return {"planned": False, "approved": False, "answer": "",
                "reason": f"worker chat failed: {str(exc)[:150]}",
                "session_id": sid, "run": run}
    answer = str((resp or {}).get("answer", ""))
    triage = (resp or {}).get("triage") if isinstance(resp, dict) else None
    pol = (triage or {}).get("policy", {}) if isinstance(triage, dict) else {}
    if str(pol.get("action", "chat")).strip().lower() in ("clarify", "escalate"):
        return {"planned": False, "approved": False, "answer": answer,
                "triage": triage, "session_id": sid, "run": run,
                "reason": f"policy {pol.get('action')} — no plan started"}
    steps = planner.extract_plan(answer)
    if not steps:
        return {"planned": False, "approved": False, "answer": answer,
                "triage": triage, "reason": "no numbered steps in reply",
                "session_id": sid, "run": run}
    decision_meta = None
    if callable(route_steps):
        try:
            answer, _items, decision_meta = route_steps(answer, list(candidates or []))
            steps = planner.extract_plan(answer)
        except Exception as exc:
            decision_meta = {"scope": "", "mode": "", "status": "error",
                             "items": [], "blocked": [],
                             "reason": f"decision stage failed: {str(exc)[:150]}"}
        if not steps:
            return {"planned": False, "approved": False, "answer": answer,
                    "triage": triage, "reason": "no numbered steps in reply",
                    "session_id": sid, "decision": decision_meta, "run": run}
    assigned = planner.extract_assigned(answer, known_workers or None)
    need = planner.needs_plan_approval(len(steps))
    if decision_meta and (decision_meta.get("blocked")
                          or decision_meta.get("status") == "error"):
        need = True  # unrouted/failed decision always needs a human look
    try:
        info = harness.start_plan(goal, steps, assigned_workers=assigned,
                                  needs_approval=need,
                                  decision_evidence=decision_meta)
    except Exception as exc:
        run = store.transition(task.id, run.version, TaskState.FAILED,
                               {"type": "task.failed",
                                "reason": f"plan file failed: {str(exc)[:150]}"})
        return {"planned": False, "approved": False, "answer": answer,
                "triage": triage, "session_id": sid, "run": run,
                "reason": f"plan file failed: {str(exc)[:150]}"}
    approved = False
    if not need:
        approved = True  # small plan: file already says auto-approved
    elif callable(approver):
        try:
            approved = bool(approver(info))
        except Exception:
            approved = False
        try:
            harness.approve_plan(approved, by="approver", note="via run_craft")
        except Exception:
            pass
    if approved:
        run = store.transition(task.id, run.version, TaskState.APPROVED,
                               {"type": "task.approved", "path": info.get("path"),
                                "steps": len(steps)})
    else:
        run = store.transition(task.id, run.version, TaskState.NEEDS_APPROVAL,
                               {"type": "task.needs_approval",
                                "path": info.get("path"), "steps": len(steps)})
    return {"planned": True, "path": info.get("path"), "steps": steps,
            "needs_approval": need, "approved": approved,
            "assigned_workers": assigned, "answer": answer, "triage": triage,
            "session_id": sid, "decision": decision_meta, "run": run}
