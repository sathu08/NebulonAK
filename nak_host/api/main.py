"""
NAK Server Application
==========================================================

Hosts the NebulonAK API (laya decisions + console dashboard).
Mirrors NebulonDB's `ndb_host/main.py` composition pattern.

Run it with:  python nebulonak.py start
Foreground:   python nebulonak.py start --foreground
Direct:       python -m nak_host.api.main  (reads the same Settings)
"""

# === Import routers from API layer ===

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from nak_host.api import create_app
from nak_host.api.router.laya import router as laya_router
from nak_host.api.routes import dashboard as dashboard_routes
from nak_host.api.routes import pipeline as pipeline_routes
from nak_host.api.routes import settings_view as settings_routes
from nak_host.api.routes import status as status_routes
from nak_host.utils.settings import load_settings


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Warm the laya weights at boot so the FIRST real score is fast.

    Without this, first inference (weights load) exceeds the client's
    decision timeout and every cold boot falls back with a notice.
    Runs off-thread (inference blocks); failure only warns — the server
    still starts cold and the first request pays the load instead.
    """
    import asyncio

    try:
        from nak_host.api.laya_backend import run_laya

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


class NoCacheConsoleMiddleware(BaseHTTPMiddleware):
    """Prevent browsers from serving stale HTML/JS/CSS for the web console."""

    _NO_CACHE_PATHS = ("/assets", "/api/NebulonAK/dashboard",
                       "/dashboard", "/pipeline", "/settings", "/")

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/assets") or path.startswith("/api/NebulonAK") \
                or path in self._NO_CACHE_PATHS:
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response


def _api_prefix() -> str:
    """Decision API prefix from Settings (matches workers.json endpoints)."""
    from urllib.parse import urlparse

    try:
        return (urlparse(load_settings().decision_endpoint).path or "").rstrip("/") \
            or "/api/NebulonAgentKit"
    except Exception:
        return "/api/NebulonAgentKit"


def create_server_app() -> FastAPI:
    """Full server app: laya decisions + console dashboard + static web."""
    from pathlib import Path

    app = create_app()
    # Laya stays on the decision prefix (workers.json endpoints point here).
    prefix = _api_prefix()
    app.include_router(laya_router, prefix=f"{prefix}/laya", tags=["Laya"])
    # Console is fixed at /api/NebulonAK/* regardless of the decision prefix.
    console = "/api/NebulonAK"
    app.include_router(status_routes.router, prefix=f"{console}/status", tags=["Status"])
    app.include_router(pipeline_routes.router, prefix=f"{console}/pipeline", tags=["Pipeline"])
    app.include_router(settings_routes.router, prefix=f"{console}/settings", tags=["Settings"])
    app.include_router(dashboard_routes.router, prefix=f"{console}/dashboard", tags=["Console"])
    app.router.lifespan_context = _lifespan
    app.add_middleware(NoCacheConsoleMiddleware)
    try:
        web_dir = Path(load_settings().web_dir())
        assets = web_dir / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")
    except Exception:
        pass
    return app


app = create_server_app()


def main() -> None:
    """Serve the API. Run: python nebulonak.py start (or this module direct)."""
    import uvicorn

    settings = load_settings()
    uvicorn.run(app, host=settings.serve_host, port=int(settings.serve_port),
                log_level=settings.serve_log_level or "info")


if __name__ == "__main__":
    main()
