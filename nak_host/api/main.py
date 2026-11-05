"""
NAK Main Application
==========================================================

This module handles the initialization of the NebulonAK API.
It provides endpoints for laya decisions.

Mirrors NebulonDB's `ndb_host/main.py`.
"""

# === Import routers from API layer ===

from contextlib import asynccontextmanager
from urllib.parse import urlparse

from nak.api import create_app
from nak.utils.settings import DEFAULT_DECISION_ENDPOINT

from nak.api.router.laya import router as laya_router


@asynccontextmanager
async def _lifespan(_app):
    """Warm the laya weights at boot so the FIRST real score is fast.

    Without this, first inference (weights load) exceeds the client's
    decision timeout and every cold boot falls back with a notice.
    Runs off-thread (inference blocks); failure only warns — the server
    still starts cold and the first request pays the load instead.
    """
    import asyncio

    try:
        from nak.api.laya_backend import run_laya

        def _warm() -> None:
            run_laya("warmup", {
                "ok": {"type": "choice", "instructions": "warmup",
                       "criteria": {"a": "warmup a", "b": "warmup b"}},
            })

        await asyncio.to_thread(_warm)
        print("[laya] warmup complete — first score will be fast", flush=True)
    except Exception as exc:  # noqa: BLE001 — cold start is degraded, not fatal
        print(f"[laya] warmup skipped ({str(exc)[:150]}); "
              f"first request pays the model load", flush=True)
    yield


# Endpoint (host/port/prefix) comes from settings.py — single source of truth.
# e.g. DEFAULT_DECISION_ENDPOINT="http://localhost:8000/api/NebulonAgentKit"
#   -> _API_PREFIX="/api/NebulonAgentKit", laya mounted at
#      "/api/NebulonAgentKit/laya" to match workers.json decision.endpoint.
_endpoint = urlparse(DEFAULT_DECISION_ENDPOINT)
_API_PREFIX = (_endpoint.path or "").rstrip("/") 
_SERVE_HOST = _endpoint.hostname
_SERVE_PORT = _endpoint.port

app = create_app()

app.include_router(laya_router, prefix=f"{_API_PREFIX}/laya", tags=["Laya"])
app.router.lifespan_context = _lifespan


def main() -> None:
    """Serve the API. Run: python nak/api/main.py."""
    import os

    import uvicorn

    host = os.environ.get("NAK_SERVE_HOST", "").strip() or _SERVE_HOST
    try:
        port = int(os.environ.get("NAK_SERVE_PORT", "").strip() or _SERVE_PORT)
    except ValueError:
        port = _SERVE_PORT
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
