"""Peer-worker dispatch — run named workers under ONE shared session key.

There is no sub-worker hierarchy: every dispatched entry is a peer worker.
Team membership and planner policy are resolved by ``team.py`` before this
module runs; dispatch only sees the allowed names. ``run_craft`` plans, then
dispatches each ``[worker=name]`` in its own thread, each built with its own
brain. All turns merge into the SAME session doc, so workers share memory.

BOUNDARY (strict): this module never imports nak_host.brain / nak_host.decision /
nak_host.harness. Workers arrive INJECTED via ``factory`` (callable name ->
worker) or a prebuilt ``workers`` mapping. nak_host/wiring.py owns building
them.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

import re

_ASSIGN_RE = re.compile(r"\[\s*worker\s*=\s*([^\]\s]+)\s*\]", re.IGNORECASE)


def _resolve(name: str, workers: Optional[Dict[str, Any]],
             factory: Optional[Callable[[str], Any]]) -> Optional[Any]:
    """Worker for ``name`` from a prebuilt mapping first, else the factory."""
    if isinstance(workers, dict) and name in workers:
        return workers[name]
    if callable(factory):
        return factory(name)
    return None


def _assignment_text(answer: str, name: str, goal: str) -> str:
    """Directive for ``name``: the plan line(s) it is tagged on, tag removed.

    Falls back to the original goal when the name is not on any single line
    (the shared session context still carries the full plan to the worker).
    """
    hits: List[str] = []
    for ln in str(answer or "").splitlines():
        if any(m.group(1).strip() == name for m in _ASSIGN_RE.finditer(ln)):
            hits.append(_ASSIGN_RE.sub("", ln).strip())
    text = " ".join(h for h in hits if h)
    return text or goal


def run_workers(answer: str, assigned: Optional[List[str]] = None, *,
                goal: str = "",
                factory: Optional[Callable[[str], Any]] = None,
                workers: Optional[Dict[str, Any]] = None,
                session_id: Optional[str] = None,
                approver: Any = None,
                max_workers: Optional[int] = None,
                exclude: Optional[str] = None,
                parallel: bool = True) -> Dict[str, Any]:
    """Run each assigned worker as a peer, in parallel threads by default.

    Every thread builds its OWN worker via ``factory(name)`` (so state and
    brain are not shared) and chats its directive with the SAME
    ``session_id``; GenericWorker's session merge is lock-protected.
    ``max_workers`` caps the thread pool (it does not drop workers);
    ``parallel=False``, ``max_workers=1`` or a single name runs sequentially.
    Unknown/broken workers are reported in ``runs`` and skipped, never fatal.
    ``exclude`` is the planner (it already produced the plan turn).
    Returns {"workers", "runs", "skipped", "session_id", "parallel"}.
    """
    names: List[str] = []
    for n in (assigned or []):
        s = str(n or "").strip()
        if s and s not in names:
            names.append(s)
    skipped: List[str] = []
    me = str(exclude or "").strip()
    todo: List[str] = []
    for n in names:
        if n == me:
            skipped.append(n)  # planner already spoke in this session
        else:
            todo.append(n)
    directives = {n: _assignment_text(answer, n, goal) for n in todo}
    out: Dict[str, Any] = {"workers": todo, "runs": [], "skipped": skipped,
                           "session_id": session_id, "parallel": bool(parallel)}
    if not todo:
        return out

    def _run(name: str) -> Dict[str, Any]:
        try:
            w = _resolve(name, workers, factory)
        except Exception as exc:  # factory crash = one worker failed
            return {"worker": name, "ok": False, "reason": str(exc)[:150]}
        if w is None:
            return {"worker": name, "ok": False, "reason": "unknown worker"}
        try:
            r = w.chat(directives[name], approve=approver,
                       session_id=session_id)
        except Exception as exc:  # one worker never kills the craft
            return {"worker": name, "ok": False, "reason": str(exc)[:150]}
        return {"worker": name, "ok": True,
                "answer": str((r or {}).get("answer", "")),
                "memory": (r or {}).get("memory", {})}

    try:
        cap = max(1, int(max_workers)) if max_workers is not None else None
    except (TypeError, ValueError):
        cap = None
    if not parallel or cap == 1 or len(todo) == 1:
        out["runs"] = [_run(n) for n in todo]
        return out
    with ThreadPoolExecutor(max_workers=cap) as pool:
        futures = [pool.submit(_run, n) for n in todo]
        out["runs"] = [f.result() for f in futures]
    return out
