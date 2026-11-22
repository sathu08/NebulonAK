"""POST /pipeline_engine/* + definitions CRUD — thin API over nak_host.flow.

Boundary: this router only calls flow.store / flow.engine (chain/runner)
(which in turn calls wiring). No worker/harness/brain imports here.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get("/definitions")
def list_definitions() -> dict[str, Any]:
    """All pipelines: builtin 'vetting' + pipeline/UI/*."""
    from nak_host.flow.store import list_definitions as _list

    try:
        return {"ok": True, "definitions": _list()}
    except Exception as exc:  # noqa: BLE001 — dashboard must not 500
        return {"ok": False, "definitions": [], "notice": str(exc)[:200]}


@router.get("/location")
def location() -> dict[str, Any]:
    """Where Builder saves pipelines ([pipeline] directory in cfg)."""
    from nak_host.flow.store import custom_root

    try:
        return {"ok": True, "directory": str(custom_root())}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "directory": "", "notice": str(exc)[:200]}


@router.get("/files")
def files(prefix: str = "", limit: int = 50) -> dict[str, Any]:
    """List workspace files for the Builder file picker (safe, top-level only)."""
    from pathlib import Path

    from nak_host.utils.settings import load_settings

    try:
        ws = load_settings().workspace_path() or str(Path(load_settings().home_path()) / "workspace")
    except Exception:
        ws = ""
    base = Path(ws).expanduser() if ws else None
    items: list[dict[str, Any]] = []
    if base is not None and base.is_dir():
        pre = str(prefix or "").strip().strip("/")
        target = (base / pre) if pre else base
        try:
            resolved = target.resolve()
            base_res = base.resolve()
            if resolved == base_res or base_res in resolved.parents:
                for child in sorted(resolved.iterdir(), key=lambda d: (not d.is_dir(), d.name.lower())):
                    if child.name.startswith("."):
                        continue
                    items.append(
                        {
                            "name": child.name,
                            "path": str(child.relative_to(base_res).as_posix()),
                            "type": "dir" if child.is_dir() else "file",
                        }
                    )
                    if len(items) >= max(1, min(200, int(limit or 50))):
                        break
        except OSError:
            pass
    return {"ok": True, "workspace": str(base) if base else "", "prefix": str(prefix or ""), "files": items}


@router.get("/definitions/{slug}")
def get_definition(slug: str) -> dict[str, Any]:
    """One definition (vetting template or custom pipeline.json)."""
    from nak_host.flow.store import get_definition as _get

    data = _get(slug)
    if data is None:
        raise HTTPException(status_code=404, detail=f"unknown pipeline {slug!r}")
    return {"ok": True, "definition": data}


@router.get("/approvals")
def list_approvals() -> dict[str, Any]:
    """Pending user approvals (polled by Builder while a run waits)."""
    from nak_host.flow.engine.approvals import list_pending as _pending

    return {"ok": True, "approvals": _pending()}


@router.post("/approvals/{aid}")
def decide_approval(aid: str, body: dict[str, Any]) -> dict[str, Any]:
    """Approve/Deny a waiting run: {approved: true/false}."""
    from nak_host.flow.engine.approvals import decide as _decide

    approved = bool((body or {}).get("approved", False))
    if not _decide(aid, approved):
        raise HTTPException(status_code=404, detail=f"unknown or decided approval {aid!r}")
    return {"ok": True, "id": aid, "approved": approved}


@router.post("/definitions")
def save_definition(body: dict[str, Any]) -> dict[str, Any]:
    """Create/update pipeline/UI/<slug>/pipeline.json (single chain)."""
    from nak_host.flow.store import save_definition as _save

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be an object")
    try:
        saved = _save(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:300]) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"save failed: {str(exc)[:200]}") from exc
    return {"ok": True, "definition": saved}


@router.delete("/definitions/{slug}")
def delete_definition(slug: str) -> dict[str, Any]:
    """Delete a custom pipeline (vetting is protected)."""
    from nak_host.flow.store import delete_definition as _delete

    try:
        found = _delete(slug)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:200]) from exc
    if not found:
        raise HTTPException(status_code=404, detail=f"unknown pipeline {slug!r}")
    return {"ok": True, "deleted": slug}


@router.get("/definitions/{slug}/runs")
def list_runs(slug: str, limit: int = 50) -> dict[str, Any]:
    """Newest-first run summaries for one user pipeline (Builder history)."""
    from nak_host.flow.store import get_definition as _get
    from nak_host.flow.store import list_runs as _runs

    if _get(slug) is None:
        raise HTTPException(status_code=404, detail=f"unknown pipeline {slug!r}")
    return {"ok": True, "slug": slug, "runs": _runs(slug, limit)}


@router.post("/validate")
def validate(body: dict[str, Any]) -> dict[str, Any]:
    """Dry validation only — no disk write, no LLM/tool calls."""
    from nak_host.flow.engine.chain import validate_definition as _validate

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be an object")
    # Accept {definition:{...}} or raw definition.
    raw = body.get("definition") if isinstance(body.get("definition"), dict) else body
    return _validate(raw)


@router.post("/run")
async def run(body: dict[str, Any]) -> dict[str, Any]:
    """Run a single-chain pipeline (prompt->tool->craft) off-thread.

    Body: {slug} or {definition:{...}, worker, auto_approve}. {dry:true} only plans.
    auto_approve:true auto-oks ask-verdict tools (file.write etc).
    """
    from nak_host.flow.engine.runner import run_definition
    from nak_host.flow.store import get_definition as _get

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be an object")
    worker = str(body.get("worker") or "viyan").strip() or "viyan"
    dry = bool(body.get("dry", False))
    auto_approve = bool(body.get("auto_approve", False))
    raw: dict[str, Any] | None = None
    if isinstance(body.get("definition"), dict):
        raw = body["definition"]
        if not raw.get("worker") and worker:
            raw = {**raw, "worker": worker}
    elif body.get("slug"):
        raw = _get(str(body["slug"]))
        if raw is None:
            raise HTTPException(status_code=404, detail=f"unknown pipeline {body['slug']!r}")
    else:
        raise HTTPException(status_code=400, detail="provide {slug} or {definition}")
    try:
        result = await asyncio.to_thread(run_definition, raw, worker, dry, auto_approve)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"run failed: {str(exc)[:200]}") from exc
    return result
