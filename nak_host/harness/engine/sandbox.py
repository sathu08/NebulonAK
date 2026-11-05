"""Sandbox + Session — single source of truth for path safety and sessions.

Used by BOTH the file tool and the harness inspect() so neither
duplicates the policy. Session lives HERE (no separate session.py):
session dirs are <workspace>/<user>_<UTC timestamp>__<hex>.

No imports from tools/ here (same package only).
"""

from __future__ import annotations

import os
import re
import secrets

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from nak.utils.constant import (
    SANDBOX_DENIED_NAMES as DENIED_NAMES,
    SANDBOX_DENIED_SUFFIXES as DENIED_SUFFIXES,
    WORKSPACE_HISTORY_FILENAME as HISTORY_FILENAME,
    WORKSPACE_HISTORY_KEEP as HISTORY_KEEP,
)

_USER_SAFE = re.compile(r"[^A-Za-z0-9_-]+")

_DENIED_DIRS_CACHE: Any = "unloaded"  # set[str] | None (None = config missing) once loaded


def _tools_registry_path() -> Path:
    """tools.json path: NAK_REGISTRY env > nak/manifests/tools.json."""
    cand = (os.environ.get("NAK_REGISTRY", "") or "").strip()
    if cand:
        return Path(cand).expanduser()
    return Path(__file__).resolve().parents[2] / "manifests" / "tools.json"


def _denied_dirs() -> Optional[set]:
    """Denied dir names UNIONED from every tools.json sandbox.deny_dirs.

    Normalized (trailing slashes stripped) so "secrets/" and "secrets"
    enforce identically. Cached after first load. None = registry
    unreadable/invalid -> callers must refuse (fail-closed).
    """
    global _DENIED_DIRS_CACHE
    if _DENIED_DIRS_CACHE != "unloaded":
        return _DENIED_DIRS_CACHE
    try:
        import json as _json

        raw = _json.loads(_tools_registry_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _DENIED_DIRS_CACHE = None
        return None
    if not isinstance(raw, list):
        _DENIED_DIRS_CACHE = None
        return None
    out: set = set()
    for spec in raw:
        if not isinstance(spec, dict):
            continue
        box = spec.get("sandbox")
        if not isinstance(box, dict):
            continue
        for d in (box.get("deny_dirs") or []):
            # normalize: "secrets/" and "secrets" enforce identically.
            # Nested paths (still containing "/") stay enforced via
            # check_resources globs instead — only simple names here.
            s = str(d or "").strip().strip("/").strip()
            if s and "/" not in s:
                out.add(s)
    _DENIED_DIRS_CACHE = out
    return out


def sanitize_user(user: str) -> str:
    """Keep filename-safe user text; blank -> 'user'."""
    clean = _USER_SAFE.sub("_", str(user or "").strip()).strip("_")
    return clean or "user"


def make_session_id(user: str, when: Optional[datetime] = None) -> str:
    """Session id format: <user>_<UTC timestamp>__<6 hex>  (filename-safe).

    The random suffix makes the id collision-safe: two workers starting in
    the same second (a team fan-out) still get distinct ids, so unrelated
    sessions never share (or clobber) one transcript doc. The id stays a
    valid, filename-safe chat key so a *deliberately* shared id can be
    handed to several workers as the team session key.
    """
    ts = (when or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%S")
    return f"{sanitize_user(user)}_{ts}__{secrets.token_hex(3)}"


def default_workspace() -> Path:
    """Repo workspace: NAK_WORKSPACE env, else <project>/workspace (single)."""
    env = os.environ.get("NAK_WORKSPACE", "").strip()
    if env:
        return Path(env).expanduser()
    return Path(__file__).resolve().parents[3] / "workspace"


def safe_join(root: str | Path, raw: str) -> Tuple[bool, str]:
    """Resolve raw inside root. Returns (True, resolved) or (False, reason).

    Blocks: empty path, absolute escapes, '..' escapes, symlink escapes
    (via resolve), denied names/suffixes/dirs (even inside root).
    """
    if not str(raw or "").strip():
        return False, "missing path"
    base = Path(str(root or ".")).expanduser()
    try:
        base_res = base.resolve()
    except OSError:
        return False, f"bad root: {base}"
    try:
        target = (base_res / str(raw).strip()).resolve()
    except OSError:
        return False, f"bad path: {raw}"
    try:
        target.relative_to(base_res)
    except ValueError:
        return False, "path escapes workspace root"
    name = target.name
    if name in DENIED_NAMES:
        return False, f"denied: {name}"
    if any(name.endswith(s) for s in DENIED_SUFFIXES):
        return False, f"denied: {name}"
    denied_dirs = _denied_dirs()
    if denied_dirs is None:
        return False, "deny config missing (tools.json unreadable)"
    if any(part in denied_dirs for part in target.relative_to(base_res).parts[:-1]) or (
        target.name in denied_dirs and target.is_dir()
    ):
        return False, "denied: restricted directory"
    return True, str(target)


# --- Session (lives here by design: no separate session.py) ---

def create_session_dir(workspace_root: str | Path, user: str) -> Tuple[str, str]:
    """New session dir <workspace>/<user>_<UTC timestamp>__. Returns (id, dir)."""
    who = sanitize_user(user)
    session_id = make_session_id(who)
    session_dir = str(Path(workspace_root).expanduser() / session_id)
    Path(session_dir).mkdir(parents=True, exist_ok=True)
    return session_id, session_dir


def record_session_history(workspace_root: str | Path, entry: Dict[str, Any]) -> Optional[str]:
    """Append one session record to <workspace>/workspace_history.json.

    Record: which worker started, owner (user), role, UTC time, session,
    plus assigned_workers (list of sub-worker names Vizhi assigned for
    parallel work — empty when none, reserved for future fan-out).
    The file is a JSON list (created when missing). Best-effort: never
    raises — returns the file path on success, None when skipped/failed.
    """
    import json

    def _assigned(value: Any) -> List[str]:
        if isinstance(value, (list, tuple)):
            return [str(v).strip() for v in value if str(v).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    try:
        root = Path(workspace_root).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        path = root / HISTORY_FILENAME
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            history = raw if isinstance(raw, list) else []
        except (OSError, ValueError):
            history = []
        record = {
            "time": (entry.get("time") or datetime.now(timezone.utc).isoformat()),
            "event": str(entry.get("event") or "started"),
            "session_id": str(entry.get("session_id") or ""),
            "worker": entry.get("worker"),
            "role": entry.get("role"),
            "owner": str(entry.get("owner") or entry.get("user") or "user"),
            "assigned_workers": _assigned(entry.get("assigned_workers")),
        }
        history.append(record)
        history = history[-HISTORY_KEEP:]
        path.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
        return str(path)
    except (OSError, ValueError):
        return None


def prune_sessions(workspace_root: str | Path, keep: int = 20) -> Dict[str, Any]:
    """Delete oldest session dirs beyond `keep` newest. Explicit-only (never auto).

    Sessions match `<user>_<UTC timestamp>__` dirs directly under the
    workspace root. Survivors' history records are kept; pruned sessions'
    records are dropped from workspace_history.json (best-effort).
    Returns {"pruned": [...ids], "kept": N}. Never raises.
    """
    import json
    import shutil

    try:
        keep_n = max(0, int(keep))
    except (TypeError, ValueError):
        keep_n = 20
    try:
        root = Path(workspace_root).expanduser()
        if not root.is_dir():
            return {"pruned": [], "kept": 0}
        dirs = sorted((d for d in root.iterdir()
                       if d.is_dir() and d.name.endswith("__")),
                      key=lambda d: d.name)
        doomed = dirs[:max(0, len(dirs) - keep_n)]
        pruned: List[str] = []
        for d in doomed:
            try:
                shutil.rmtree(d)
                pruned.append(d.name)
            except OSError:
                continue
        if pruned:
            try:
                path = root / HISTORY_FILENAME
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    gone = set(pruned)
                    kept = [r for r in raw
                            if str((r or {}).get("session_id", "") or "") not in gone
                            and str((r or {}).get("session", "") or "") not in gone]
                    path.write_text(json.dumps(kept, indent=2, ensure_ascii=False),
                                    encoding="utf-8")
            except (OSError, ValueError):
                pass
        return {"pruned": pruned, "kept": len(dirs) - len(pruned)}
    except Exception:
        return {"pruned": [], "kept": 0}


def resource_bases(session_dir: str | Path, workspace_root: str | Path,
                   engine_file: str | Path = __file__) -> List[Path]:
    """Bases a resolved path is tested against (session + workspace + project root)."""
    bases = []
    for b in (session_dir, workspace_root,
              Path(engine_file).resolve().parents[3]):  # nak/harness/engine -> root
        try:
            if str(b or "").strip():
                bases.append(Path(b).expanduser().resolve())
        except OSError:
            pass
    return bases


# --- Resource globs (deny wins; empty grant denies) ---

def glob_hit(pattern: str, form: str) -> bool:
    """Glob match where ** crosses separators (PurePath.match can't
    handle trailing /**). Also covers top-level names for **/x forms."""
    from fnmatch import fnmatchcase
    pat = pattern.replace("**", "*")
    pats = [pat]
    if pat.startswith("*/"):
        pats.append(pat[2:])
    if pat.endswith("/*"):
        pats.append(pat[:-2])
    return any(fnmatchcase(form, p) for p in pats)


def check_resources(resolved: str, op: str,
                    entry: Dict[str, Any],
                    bases: List[Path]) -> Optional[str]:
    """File globs for a resolved path. deny wins; empty grant = deny."""
    files = ((entry.get("resources") or {}).get("files")) or {}
    try:
        target = Path(resolved).expanduser().resolve()
    except OSError:
        return f"bad resource path: {resolved}"
    forms = [target.as_posix()]
    for base in bases or []:
        try:
            forms.append(target.relative_to(base).as_posix())
        except ValueError:
            pass
    for pat in (files.get("deny") or []):
        if any(glob_hit(pat, f) for f in forms):
            return f"resource denied: {resolved}"
    grants = files.get(op) or []
    if op == "":
        grants = (files.get("read") or []) + (files.get("write") or [])
    if not grants or not any(glob_hit(p, f) for p in grants for f in forms):
        return f"resource not granted for {op or 'access'}: {resolved}"
    return None
