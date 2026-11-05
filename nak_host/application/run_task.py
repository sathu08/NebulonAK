"""Run one assigned task through the agent loop (single worker).

Expects the run in ASSIGNED state (callers: chains and named calls create
and assign first). Transitions to RUNNING, executes the loop, saves the
Result. Terminal states (VERIFIED/REPORTED/FAILED) belong to verify/report
paths, not raw execution — a finished run stays RUNNING with its result.
"""

from __future__ import annotations

from typing import Any

from nak_host.application.approval import ApprovalLedger
from nak_host.application.loop import allowed_tool_schemas, run_loop
from nak_host.domain.result import Result, ResultStatus
from nak_host.domain.state import TaskState
from nak_host.domain.task import Task, TaskRun

_STATUS_MAP = {
    "done": (ResultStatus.OK, False),
    "needs_approval": (ResultStatus.NEEDS_APPROVAL, True),
    "cancelled": (ResultStatus.CANCELLED, False),
}


class _NeverCancel:
    @property
    def cancelled(self) -> bool:
        return False

    def raise_if_cancelled(self) -> None:
        return None


def run_task(task: Task, run: TaskRun, *, store: Any, brain: Any,
             executor: Any, memory: Any = None, registry: Any = None,
             tools: Any = None, worker_name: str = "", identity: str = "",
             approver: Any = None, policy: Any = None, authority: Any = None,
             cancel: Any = None, session_id: Any = None,
             on_event: Any = None, max_calls_per_turn: Any = None,
             obs_chars: Any = None, history_keep: Any = None,
             max_stalls: Any = None, auto_steps: Any = None) -> dict[str, Any]:
    """Execute the loop for one worker. Returns {result, run, loop}.

    ``policy`` is reserved for future policy engines (the harness enforces
    policy today) and may be None; ``authority`` defaults to a fresh ledger
    (pass it as both authority and grants source); ``cancel`` defaults to a
    never-cancel token. ``on_event`` streams per-tool events; budget kwargs
    resolve as explicit > task.inputs > loop default.
    """
    if run.state != TaskState.ASSIGNED:
        raise ValueError(f"run_task needs an ASSIGNED run, got {run.state}")
    worker = str(worker_name or "").strip() or str(run.assigned_worker or "")
    ledger = authority if isinstance(authority, ApprovalLedger) else ApprovalLedger()
    ctx_authority = authority if authority is not None else ledger
    ctx = _build_context(task, run, worker, policy, ctx_authority,
                         cancel or _NeverCancel())
    run = store.transition(task.id, run.version, TaskState.RUNNING,
                           {"type": "task.running", "worker": worker})
    schemas = (list(tools) if tools is not None
               else allowed_tool_schemas(worker, registry))
    loop_out = run_loop(task, run, store=store, brain=brain, executor=executor,
                        ctx=ctx, ledger=ledger, memory=memory, tools=schemas,
                        identity=identity, worker_name=worker,
                        approver=approver, session_id=session_id,
                        on_event=on_event,
                        max_calls_per_turn=max_calls_per_turn,
                        obs_chars=obs_chars, history_keep=history_keep,
                        max_stalls=max_stalls, auto_steps=auto_steps)
    status, needs = _STATUS_MAP.get(loop_out["status"],
                                    (ResultStatus.FAILED, False))
    calls = loop_out.get("tool_calls", [])
    ok_calls = [c for c in calls if c.get("ok")]
    files = sorted({str(c.get("detail", "")) for c in ok_calls
                    if c.get("tool") == "file" and c.get("detail")})
    commands = [str(c.get("detail", "")) for c in calls
                if c.get("tool") == "terminal" and c.get("detail")]
    diff_stat = ""
    for c in calls:
        if c.get("tool") == "terminal" and c.get("ok") \
                and "git diff" in str(c.get("detail", "")):
            diff_stat = str(c.get("output", "") or "")[:2000]
            if diff_stat:
                break
    result = Result(
        task_id=task.id, worker=worker, status=status,
        summary=(f"loop {loop_out['status']} after {loop_out.get('steps', 0)} "
                 f"steps, {len(ok_calls)}/{len(calls)} tool calls ok"),
        files_changed=tuple(files), commands_run=tuple(commands),
        evidence={"answer": str(loop_out.get("answer", ""))[:2000],
                  "tool_calls": len(calls), "ok_calls": len(ok_calls),
                  "reason": str(loop_out.get("reason", "")),
                  "diff_stat": diff_stat},
        blockers=() if status == ResultStatus.OK else ("loop did not complete",),
        needs_approval=needs)
    store.save_result(task.id, result)
    store.append_event(task.id, {"type": "task.finished",
                                 "status": loop_out["status"]})
    _, fresh = store.get_task(task.id)
    return {"result": result, "run": fresh, "loop": loop_out}


def _build_context(task: Task, run: TaskRun, worker: str, policy: Any,
                   authority: Any, cancel: Any) -> Any:
    from nak_host.domain.ports import ExecutionContext

    return ExecutionContext(
        task_id=task.id, worker_id=worker, workspace=task.workspace,
        allowed_tools=set(task.allowed_tools), attempt=run.attempt,
        approval=None, policy=policy, authority=authority, cancel=cancel)
