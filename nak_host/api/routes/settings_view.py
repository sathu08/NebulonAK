"""GET /settings/* — config and manifests. Read-only, secrets redacted.

Redaction is structural, not best-effort: any key whose name suggests a
secret is replaced before the response is built, and the dashboard test
asserts no secret value survives.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter()

_SECRET_HINTS = ("auth_token", "password", "secret", "api_key", "bearer")


def _redact(value: Any, key: str = "") -> Any:
    name = str(key or "").lower()
    if any(hint in name for hint in _SECRET_HINTS):
        return "***" if str(value or "") else ""
    if isinstance(value, dict):
        return {k: _redact(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v, key) for v in value]
    text = str(value or "")
    if len(text) >= 32 and " " not in text and key:
        # bearer-looking token outside a secret-named key: still hide it.
        return "***"
    return value


def _public_settings() -> dict[str, Any]:
    from nak_host.utils.settings import load_settings

    s = load_settings()
    return {
        "paths": {"home": s.home_path(), "web": s.web_dir(),
                  "logs": s.logs_dir(), "pid_file": s.pid_file(),
                  "runs": s.runs_dir(), "workspace": s.workspace_path()},
        "server": {"host": s.serve_host, "port": int(s.serve_port),
                   "workers": int(s.serve_workers),
                   "timeout": int(s.serve_timeout),
                   "keep_alive": int(s.serve_keep_alive),
                   "graceful_timeout": int(s.serve_graceful_timeout),
                   "log_level": s.serve_log_level},
        "nebulonmind": {"base_url": s.base_url, "user": s.user,
                        "auth_token": s.auth_token,
                        "timeout": float(s.timeout)},
        "worker": {"workers_file": s.workers_path(),
                   "language": s.worker_lang},
        "harness": {"workspace_root": s.workspace_path(),
                    "user": s.harness_user, "registry": s.registry_path(),
                    "permissions": s.permissions_path(),
                    "crafts": s.crafts_path(),
                    "crafts_prompt": s.crafts_prompt_path()},
        "decision": {"endpoint": s.decision_endpoint,
                     "timeout": float(s.decision_timeout),
                     "auth_token": s.decision_auth_token},
        "nebulondb": {"base_url": s.ndb_base_url, "user": s.ndb_user,
                      "corpus": s.ndb_corpus,
                      "timeout": float(s.ndb_timeout),
                      "resolve_from_mind": bool(s.resolve_from_mind)},
        "store_backend": s.store_backend,
    }


@router.get("")
@router.get("/")
def view() -> dict[str, Any]:
    """Effective config with every secret redacted."""
    return {"ok": True, "settings": _redact(_public_settings())}


@router.get("/manifests")
def manifests() -> dict[str, Any]:
    """Registry counts + fail-loud gaps (what the server actually loaded)."""
    from nak_host.adapters.registry_json import JsonRegistry

    reg = JsonRegistry()
    try:
        gaps: dict[str, Any] = {}
        from nak_host.harness.craft.craft import craft_gaps

        gaps = dict(craft_gaps() or {})
    except Exception as exc:  # noqa: BLE001 — report, don't fail
        gaps = {"error": str(exc)[:200]}
    return {"ok": True,
            "workers": [str(e.get("name", "")) for e in reg.workers()],
            "teams": [str(t.get("name", "")) for t in reg.teams()],
            "crafts": [str(c.get("name", "")) for c in reg.crafts()],
            "tools": [str(t.get("name", "")) for t in reg.tools()],
            "craft_gaps": gaps}
