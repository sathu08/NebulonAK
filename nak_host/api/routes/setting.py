"""GET /settings/* — config and manifests + file editor (same router, no extra file).

Read-only part keeps secrets redacted. Editor part (below) reads/writes
only the allowlisted cfg + json files, with backup + atomic write.
"""

from __future__ import annotations

import configparser
import json

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

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
    if len(text) >= 32 and " " not in text and key and "/" not in text and "\\" not in text:
        # bearer-looking token outside a secret-named key: still hide it.
        # paths contain slashes -> never redacted (they are not secrets).
        return "***"
    return value


def _public_settings() -> dict[str, Any]:
    from nak_host.utils.settings import load_settings

    s = load_settings()
    ws = s.workspace_path() or str(Path(s.home_path()) / "workspace")
    return {
        "paths": {
            "home": s.home_path(),
            "web": s.web_dir(),
            "logs": s.logs_dir(),
            "pid_file": s.pid_file(),
            "runs": s.runs_dir(),
            "workspace": ws,
        },
        "server": {
            "host": s.serve_host,
            "port": int(s.serve_port),
            "workers": int(s.serve_workers),
            "timeout": int(s.serve_timeout),
            "keep_alive": int(s.serve_keep_alive),
            "graceful_timeout": int(s.serve_graceful_timeout),
            "log_level": s.serve_log_level,
        },
        "nebulonmind": {
            "base_url": s.base_url,
            "user": s.user,
            "auth_token": s.auth_token,
            "timeout": float(s.timeout),
        },
        "worker": {"workers_file": s.workers_path(), "language": s.worker_lang},
        "harness": {
            "workspace_root": s.workspace_path(),
            "user": s.harness_user,
            "registry": s.registry_path(),
            "permissions": s.permissions_path(),
            "crafts": s.crafts_path(),
            "crafts_prompt": s.crafts_prompt_path(),
        },
        "pipeline": {"directory": s.pipeline_dir_path()},
        "decision": {
            "endpoint": s.decision_endpoint,
            "timeout": float(s.decision_timeout),
            "auth_token": s.decision_auth_token,
        },
        "nebulondb": {
            "base_url": s.ndb_base_url,
            "user": s.ndb_user,
            "corpus": s.ndb_corpus,
            "timeout": float(s.ndb_timeout),
            "resolve_from_mind": bool(s.resolve_from_mind),
        },
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
    return {
        "ok": True,
        "workers": [str(e.get("name", "")) for e in reg.workers()],
        "teams": [str(t.get("name", "")) for t in reg.teams()],
        "crafts": [str(c.get("name", "")) for c in reg.crafts()],
        "tools": [str(t.get("name", "")) for t in reg.tools()],
        "craft_gaps": gaps,
    }


# --- File editor (cfg + json, allowlisted only) ------------------------- #


def _editable_files() -> dict[str, dict[str, str]]:
    """id -> {label, path, kind}. Single source for list/get/save."""
    from nak_host.utils.settings import load_settings

    s = load_settings()
    workers = Path(s.workers_path())
    manifests = workers.parent
    cfg = s.cfg_path or (Path(s.home_path()) / "nebulonak.cfg")
    return {
        "nebulonak": {"label": "nebulonak.cfg (server)", "path": str(cfg), "kind": "ini"},
        "workers": {"label": "workers.json", "path": str(workers), "kind": "json"},
        "teams": {"label": "teams.json", "path": str(manifests / "teams.json"), "kind": "json"},
        "tools": {"label": "tools.json", "path": str(Path(s.registry_path())), "kind": "json"},
        "permissions": {"label": "permissions.json", "path": str(Path(s.permissions_path())), "kind": "json"},
        "crafts": {"label": "crafts.json", "path": str(Path(s.crafts_path())), "kind": "json"},
    }


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _validate_text(kind: str, fid: str, text: str) -> list[str]:
    if kind == "ini":
        parser = configparser.ConfigParser()
        try:
            parser.read_string(text or "")
        except Exception as exc:
            return [f"ini parse error: {exc!s}"[:300]]
        return []
    try:
        raw = json.loads(text or "")
    except Exception as exc:
        return [f"json parse error: {exc!s}"[:300]]
    if fid in ("workers", "tools", "permissions", "crafts", "teams"):
        if not isinstance(raw, (list, dict)):
            return [f"{fid} must be a JSON list or object"]
        items = (
            raw
            if isinstance(raw, list)
            else raw.get("workers", raw.get("tools", raw.get("teams", raw.get("crafts", raw))))
        )
        if isinstance(items, list):
            for i, e in enumerate(items[:50]):
                if not isinstance(e, dict) or not str(e.get("name", "")).strip():
                    return [f"{fid}[{i}] entries need a non-empty name"]
    return []


@router.get("/files")
def list_files() -> dict[str, Any]:
    """Editable cfg + json files (allowlisted only)."""
    out: list[dict[str, Any]] = []
    for fid, meta in _editable_files().items():
        p = Path(meta["path"])
        out.append(
            {
                "id": fid,
                "label": meta["label"],
                "kind": meta["kind"],
                "path": str(p),
                "exists": p.is_file(),
                "size": p.stat().st_size if p.is_file() else 0,
            }
        )
    return {"ok": True, "files": out}


@router.get("/files/{fid}")
def get_file(fid: str) -> dict[str, Any]:
    """Raw text of one allowlisted file."""
    meta = _editable_files().get(str(fid or "").strip())
    if meta is None:
        raise HTTPException(status_code=404, detail=f"unknown file {fid!r}")
    p = Path(meta["path"])
    if not p.is_file():
        raise HTTPException(status_code=404, detail=f"file missing: {p}")
    try:
        return {"ok": True, "id": fid, "kind": meta["kind"], "path": str(p), "content": _read_text(p)}
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"read failed: {str(exc)[:200]}") from exc


@router.post("/files/{fid}/validate")
def validate_file(fid: str, body: dict[str, Any]) -> dict[str, Any]:
    """Validate edited text without writing."""
    meta = _editable_files().get(str(fid or "").strip())
    if meta is None:
        raise HTTPException(status_code=404, detail=f"unknown file {fid!r}")
    text = str((body or {}).get("content", ""))
    errors = _validate_text(meta["kind"], fid, text)
    return {"ok": not errors, "errors": errors}


@router.post("/files/{fid}")
def save_file(fid: str, body: dict[str, Any]) -> dict[str, Any]:
    """Validate then backup + atomic write one allowlisted file."""
    from datetime import datetime, timezone

    meta = _editable_files().get(str(fid or "").strip())
    if meta is None:
        raise HTTPException(status_code=404, detail=f"unknown file {fid!r}")
    text = str((body or {}).get("content", ""))
    errors = _validate_text(meta["kind"], fid, text)
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors)[:300])
    p = Path(meta["path"])
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.is_file():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            backup = p.parent / (p.name + f".bak.{stamp}")
            backup.write_text(_read_text(p), encoding="utf-8")
        tmp = p.parent / (p.name + ".tmp")
        tmp.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
        tmp.replace(p)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"save failed: {str(exc)[:200]}") from exc
    return {"ok": True, "id": fid, "path": str(p), "size": len(text)}


# --- Inline section edit (Settings page pencil icon) -------------------- #
# View section -> (cfg section, {view key: cfg key}). store_backend is env-only: read-only.

_SECTION_MAP: dict[str, tuple[str, dict[str, str]]] = {
    "paths": ("paths", {"home": "nebulonak_home"}),
    "server": (
        "server",
        {
            "host": "host",
            "port": "port",
            "workers": "workers",
            "timeout": "timeout",
            "keep_alive": "keep_alive",
            "graceful_timeout": "graceful_timeout",
            "log_level": "log_level",
        },
    ),
    "nebulonmind": (
        "nebulonmind",
        {"base_url": "base_url", "user": "user", "auth_token": "auth_token", "timeout": "timeout"},
    ),
    "worker": ("worker", {"workers_file": "workers_file", "language": "language"}),
    "harness": (
        "harness",
        {
            "workspace_root": "workspace_root",
            "user": "user",
            "registry": "registry",
            "permissions": "permissions",
            "crafts": "crafts",
            "crafts_prompt": "crafts_prompt",
        },
    ),
    "pipeline": ("pipeline", {"directory": "directory"}),
    "decision": ("decision", {"endpoint": "endpoint", "timeout": "timeout", "auth_token": "auth_token"}),
    "nebulondb": (
        "nebulondb",
        {
            "base_url": "base_url",
            "user": "user",
            "corpus": "corpus",
            "timeout": "timeout",
            "resolve_from_mind": "resolve_from_mind",
        },
    ),
}

_READONLY_SECTIONS = {"store_backend"}


def _update_cfg_text(text: str, cfg_section: str, cfg_key: str, value: str) -> str:
    """Set one key under [section], preserving comments/layout. Adds section/key if missing."""
    lines = text.splitlines()
    head = f"[{cfg_section}]"
    head_idx: int | None = None
    next_head_idx: int | None = None
    key_idx: int | None = None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            if head_idx is not None and next_head_idx is None and s[1:-1].strip().lower() != cfg_section.lower():
                next_head_idx = i
            if s[1:-1].strip().lower() == cfg_section.lower() and head_idx is None:
                head_idx = i
            continue
        if (
            head_idx is not None
            and next_head_idx is None
            and key_idx is None
            and s
            and not s.startswith(("#", ";"))
            and "=" in line
            and line.split("=", 1)[0].strip().lower() == cfg_key.lower()
        ):
            key_idx = i
    if key_idx is not None:
        line = lines[key_idx]
        indent = line[: len(line) - len(line.lstrip())]
        lines[key_idx] = f"{indent}{cfg_key} = {value}"
        return "\n".join(lines) + "\n"
    new_line = f"{cfg_key} = {value}"
    if head_idx is None:
        return "\n".join(lines + ([""] if lines and lines[-1].strip() else []) + [head, new_line]) + "\n"
    end = next_head_idx if next_head_idx is not None else len(lines)
    insert_at = end
    while insert_at > head_idx + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    lines.insert(insert_at, new_line)
    return "\n".join(lines) + "\n"


@router.post("/section/{section}")
def save_section(section: str, body: dict[str, Any]) -> dict[str, Any]:
    """Inline edit one Settings card: {values: {viewKey: value}}. Writes nebulonak.cfg."""
    from datetime import datetime, timezone

    sec = str(section or "").strip()
    if sec in _READONLY_SECTIONS:
        raise HTTPException(status_code=400, detail=f"section {sec!r} is read-only (env-only)")
    mapping = _SECTION_MAP.get(sec)
    if mapping is None:
        raise HTTPException(status_code=404, detail=f"unknown section {sec!r}")
    cfg_section, key_map = mapping
    values = (body or {}).get("values", {})
    if not isinstance(values, dict) or not values:
        raise HTTPException(status_code=400, detail="values must be a non-empty object")
    from nak_host.utils.settings import load_settings

    s = load_settings()
    cfg_path = s.cfg_path or (Path(s.home_path()) / "nebulonak.cfg")
    try:
        current = cfg_path.read_text(encoding="utf-8") if cfg_path.is_file() else f"[{cfg_section}]\n"
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"read failed: {str(exc)[:200]}") from exc
    updated = current
    saved: dict[str, str] = {}
    for view_key, raw in values.items():
        cfg_key = key_map.get(str(view_key))
        if cfg_key is None:
            continue
        val = str(raw if raw is not None else "").strip()
        if view_key in ("auth_token",) and val in ("", "***"):
            continue  # redacted placeholder: don't overwrite secret with stars
        updated = _update_cfg_text(updated, cfg_section, cfg_key, val)
        saved[view_key] = val
    if not saved:
        raise HTTPException(status_code=400, detail="no editable keys in values")
    try:
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        if cfg_path.is_file():
            (cfg_path.parent / (cfg_path.name + f".bak.{stamp}")).write_text(current, encoding="utf-8")
        tmp = cfg_path.parent / (cfg_path.name + ".tmp")
        tmp.write_text(updated, encoding="utf-8")
        tmp.replace(cfg_path)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"save failed: {str(exc)[:200]}") from exc
    return {"ok": True, "section": sec, "saved": saved, "path": str(cfg_path)}
