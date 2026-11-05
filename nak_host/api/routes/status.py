"""GET /status — is the server (and everything it needs) running?

Read-only. Every check is timed and failure-isolated: one down
dependency never breaks the whole response.
"""

from __future__ import annotations

import socket
import time

from typing import Any

from fastapi import APIRouter

router = APIRouter()


def _timed(label: str, fn) -> dict[str, Any]:
    """Run one check: {label, ok, ms, detail} — never raises."""
    start = time.monotonic()
    try:
        detail = fn()
        ok = True
        if isinstance(detail, dict) and "ok" in detail and not detail["ok"]:
            ok = False
    except Exception as exc:  # noqa: BLE001 — checks must never break status
        ok, detail = False, str(exc)[:300]
    return {"label": label, "ok": bool(ok),
            "ms": int((time.monotonic() - start) * 1000), "detail": detail}


def _self_check() -> dict[str, Any]:
    import os

    from nak_host.utils.settings import load_settings

    settings = load_settings()
    pid_file = settings.pid_file()
    pid: Any = None
    try:
        with open(pid_file, encoding="utf-8") as fh:
            pid = int(fh.read().strip().split()[0])
    except (OSError, ValueError):
        pid = None
    alive = False
    if pid is not None:
        try:
            os.kill(pid, 0)
            alive = True
        except (OSError, ValueError):
            alive = False
    try:
        from nak_host import __version__ as version
    except Exception:
        version = "0.1.0"
    return {"pid": pid, "pid_alive": alive,
            "host": settings.serve_host, "port": int(settings.serve_port),
            "version": version}


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=3):
            return True
    except OSError:
        return False


def _mind_live() -> dict[str, Any]:
    from nak_host.brain import Brain

    body = Brain().health_live()
    return {"ok": True, "response": str(body)[:200]}


def _mind_ready() -> dict[str, Any]:
    from nak_host.brain import Brain

    body = Brain().require_mind()
    llm: Any = None
    try:
        llm = Brain().llm_status()
    except Exception:
        llm = None
    provider = (llm or {}).get("provider", "") if isinstance(llm, dict) else ""
    model = (llm or {}).get("model", "") if isinstance(llm, dict) else ""
    return {"ok": True, "ready": str(body)[:200],
            "provider": str(provider), "model": str(model)}


def _ndb_reachable() -> dict[str, Any]:
    from nak_host.utils.settings import load_settings

    settings = load_settings()
    host = settings.ndb_base_url.split("://", 1)[-1].split("/")[0]
    name, _, port = host.partition(":")
    if _port_open(name or "localhost", int(port or 6969)):
        return {"ok": True, "base_url": settings.ndb_base_url}
    return {"ok": False, "base_url": settings.ndb_base_url}


def _laya_selftest() -> dict[str, Any]:
    from nak_host.api.laya_backend import run_laya

    out = run_laya("status probe", {
        "ok": {"type": "choice", "instructions": "pick one",
               "criteria": {"a": "first", "b": "second"}},
    })
    return {"ok": True, "keys": sorted(out)[:8]}


def _store_probe() -> dict[str, Any]:
    from nak_host.wiring import make_prod_store

    store = make_prod_store()
    pending = store.list_pending()
    return {"ok": True, "backend": type(store).__name__,
            "pending": len(pending)}


@router.get("", include_in_schema=True)
@router.get("/", include_in_schema=False)
def status() -> dict[str, Any]:
    """One status doc for the whole stack. Never raises."""
    from nak_host.utils.settings import load_settings

    settings = load_settings()
    checks = [
        _timed("server", _self_check),
        _timed("nebulonmind_live", _mind_live),
        _timed("nebulonmind_ready", _mind_ready),
        _timed("nebulondb", _ndb_reachable),
        _timed("laya", _laya_selftest),
        _timed("taskstore", _store_probe),
    ]
    # The server itself is up iff this code ran; dependencies may be down.
    ok = all(c["ok"] for c in checks if c["label"] not in ("taskstore",))
    try:
        laya_serving = _port_open(settings.serve_host, int(settings.serve_port))
    except (TypeError, ValueError):
        laya_serving = True  # this response proves the port is open
    return {"ok": bool(ok), "serving": bool(laya_serving),
            "host": settings.serve_host, "port": int(settings.serve_port),
            "checks": checks}
