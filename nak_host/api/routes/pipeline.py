"""GET /pipeline/* — what the pipelines did/are doing. Read-only.

* /tasks          pending TaskStore runs (+ single-task detail)
* /tasks/{task_id}  one task + run snapshot (404 when unknown)
* /vetting         newest-first vetting run summaries on disk
* /sessions       tail of the shared session history
"""

from __future__ import annotations

import json

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


def _store() -> Any:
    from nak_host.wiring import make_task_store

    import os

    backend = os.environ.get("NAK_STORE", "").strip().lower()
    if backend in ("nebulondb", "ndb", "db"):
        from nak_host.wiring import make_prod_store

        return make_prod_store(), "nebulondb"
    return make_task_store("memory"), "memory"


@router.get("/tasks")
def tasks(limit: int = 20) -> dict[str, Any]:
    """Pending runs. Memory backend reports itself (nothing durable there)."""
    try:
        store, backend = _store()
        pending = store.list_pending()
    except Exception as exc:  # noqa: BLE001 — dashboard must not 500
        return {"ok": False, "backend": "unknown", "pending": [],
                "notice": f"store unreachable: {str(exc)[:200]}"}
    out: list[dict[str, Any]] = []
    for run in list(pending or [])[:max(1, min(100, int(limit or 20)))]:
        out.append({"task_id": str(getattr(run, "task_id", "")),
                    "state": str(getattr(getattr(run, "state", ""), "value", run.state)),
                    "worker": str(getattr(run, "assigned_worker", "") or ""),
                    "attempt": int(getattr(run, "attempt", 1) or 1)})
    notice = ""
    if backend == "memory":
        notice = "memory backend: pending list is process-local and not durable"
    return {"ok": True, "backend": backend, "pending": out, "notice": notice}


@router.get("/tasks/{task_id}")
def task_detail(task_id: str) -> dict[str, Any]:
    """One task + its current run snapshot (404 when unknown)."""
    try:
        store, backend = _store()
        found = store.get_task(str(task_id))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503,
                            detail=f"store unreachable: {str(exc)[:200]}") from exc
    if found is None:
        raise HTTPException(status_code=404, detail=f"unknown task {task_id!r}")
    task, run = found
    return {"ok": True, "backend": backend,
            "task": {"id": task.id, "goal": task.goal,
                     "workspace": task.workspace,
                     "max_steps": int(task.max_steps),
                     "acceptance": list(task.acceptance or ()),
                     "allowed_tools": sorted(task.allowed_tools or ())},
            "run": {"state": str(run.state.value),
                    "version": int(run.version),
                    "worker": str(run.assigned_worker or ""),
                    "attempt": int(run.attempt),
                    "block_reason": (None if run.block_reason is None else {
                        "code": run.block_reason.code,
                        "detail": run.block_reason.detail,
                        "resumable": bool(run.block_reason.resumable)}),
                    "result": (None if run.result is None else {
                        "status": str(run.result.status.value),
                        "summary": run.result.summary,
                        "files_changed": list(run.result.files_changed or ()),
                        "commands_run": list(run.result.commands_run or ())})}}


def _runs_dir() -> Path:
    from nak_host.utils.settings import load_settings

    return Path(load_settings().runs_dir())


@router.get("/vetting")
def vetting(limit: int = 10) -> dict[str, Any]:
    """Newest-first vetting run summaries straight from disk."""
    base = _runs_dir()
    runs: list[dict[str, Any]] = []
    if base.is_dir():
        for stamp in sorted((d for d in base.iterdir() if d.is_dir()),
                            key=lambda d: d.name, reverse=True):
            summary = stamp / "summary.json"
            if not summary.is_file():
                # report_card runs keep the summary one level deeper
                nested = stamp / "report_card" / "summary.json"
                summary = nested if nested.is_file() else summary
            entry: dict[str, Any] = {"stamp": stamp.name, "path": str(stamp),
                                     "summary": bool(summary.is_file())}
            if summary.is_file():
                try:
                    raw = json.loads(summary.read_text(encoding="utf-8"))
                    entry["report"] = raw if isinstance(raw, dict) else {"raw": raw}
                except (OSError, ValueError) as exc:
                    entry["report_error"] = str(exc)[:200]
            runs.append(entry)
            if len(runs) >= max(1, min(50, int(limit or 10))):
                break
    return {"ok": True, "runs_dir": str(base), "runs": runs}


@router.get("/sessions")
def sessions(limit: int = 20) -> dict[str, Any]:
    """Tail of workspace_history.json (who ran what, when). No transcripts."""
    from nak_host.utils.settings import load_settings

    settings = load_settings()
    ws = settings.workspace_path() or str(Path(settings.home_path()) / "workspace")
    history = Path(ws) / "workspace_history.json"
    entries: list[dict[str, Any]] = []
    if history.is_file():
        try:
            raw = json.loads(history.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                entries = raw[-max(1, min(100, int(limit or 20))):]
        except (OSError, ValueError) as exc:
            return {"ok": False, "workspace": ws,
                    "notice": f"history unreadable: {str(exc)[:200]}"}
    return {"ok": True, "workspace": ws, "sessions": entries}
