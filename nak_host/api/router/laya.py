"""nak.api.router.laya — laya API endpoint for api.

Thin FastAPI layer over `..laya_backend` (which owns the `laya` import).

  POST /v1/laya (open, no auth)

Fixed routes — add new ones here in code, never at runtime.

Usage (server):
    from nak.api.router.laya import router as laya_router
    app.include_router(laya_router)

Usage (direct def call, no HTTP):
    from nak.api.router import laya
    laya.run_laya("read notes.txt", {"route": {...}})
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from ..laya_backend import predict as backend_predict

router = APIRouter(tags=["laya"])


def run_laya(state: Any, questions: Dict[str, Any]) -> Dict[str, Any]:
    """Def function: run one laya inference via the backend.

    This is the extension point — add pre/post-processing, caps,
    logging, alternate scorer here. Raises on bad scorer output
    so callers fall back deterministically.
    """
    if not isinstance(questions, dict) or not questions:
        raise ValueError("'questions' must be a non-empty object")
    result = backend_predict(state, questions)
    if not isinstance(result, dict):
        raise RuntimeError("laya scorer returned non-object")
    return result


@router.post("/", include_in_schema=True)
@router.post("", include_in_schema=False)
def laya_endpoint(body: Dict[str, Any]) -> Any:
    """Accepts both .../laya/ (canonical) and .../laya (no 307 hop)."""
    if not isinstance(body, dict) or "questions" not in body:
        raise HTTPException(
            status_code=400,
            detail="body must be an object with a 'questions' field",
        )
    try:
        result = run_laya(body.get("state", ""), body["questions"])
    except ValueError as exc:  # bad question shape — client's fault
        raise HTTPException(status_code=422, detail=str(exc)[:300])
    except Exception:  # noqa: BLE001 — never leak paths/weights text
        raise HTTPException(status_code=500, detail="inference failed")
    return JSONResponse(content=result)
