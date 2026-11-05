"""GET /, /pipeline, /settings — the web console pages (static HTML).

Mirrors NebulonDB's dashboard router: serve files from the configured
web dir, 404 loudly when the install is incomplete.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

router = APIRouter()


def _page(name: str) -> Path:
    from nak_host.utils.settings import load_settings

    page = Path(load_settings().web_dir()) / name
    if not page.is_file():
        raise HTTPException(status_code=404,
                            detail=f"console page {name} missing "
                                   f"(expected at {page})")
    return page


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def console_root():
    """Console landing page (status)."""
    return FileResponse(_page("index.html"))


@router.get("/index", response_class=HTMLResponse, include_in_schema=False)
@router.get("/index.html", response_class=HTMLResponse, include_in_schema=False)
async def console_index():
    """Alias of the landing page."""
    return FileResponse(_page("index.html"))


@router.get("/pipeline", response_class=HTMLResponse, include_in_schema=False)
@router.get("/pipeline.html", response_class=HTMLResponse, include_in_schema=False)
async def console_pipeline():
    """Pipeline page (tasks, vetting runs, sessions)."""
    return FileResponse(_page("pipeline.html"))


@router.get("/settings", response_class=HTMLResponse, include_in_schema=False)
@router.get("/settings.html", response_class=HTMLResponse, include_in_schema=False)
async def console_settings():
    """Settings page (config, manifests, workspace)."""
    return FileResponse(_page("settings.html"))


@router.get("/go-pipeline", include_in_schema=False)
async def go_pipeline():
    """Old bookmark redirect (kept stable for shared links)."""
    return RedirectResponse(url="pipeline", status_code=307)
