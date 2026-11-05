"""Permission — worker <-> harness access boundary.

permissions.json (fail-closed): which worker may use which tools.
Pure helpers only: no engine imports, no LLM, no tools.

Ownership (see ExecutionHarness tree):
  Permission (here): verdicts, normalization, load, deny, rule_for.
  Capability (capability.py): CAP_MAP + required_caps.
  Sandbox (sandbox.py): glob_hit + check_resources + session helpers.
  Limits (limits.py): DEFAULT_LIMITS + norm_limits + check_limits.
"""

from __future__ import annotations

import json
import os

from pathlib import Path
from typing import Any, Dict, Optional

from nak_host.utils.constant import (
    DEFAULT_PERMISSIONS_JSON as DEFAULT_PERMISSIONS_FILE,
    DEFAULT_WORKERS_JSON as DEFAULT_WORKERS_FILE,
)


def verdict(value: Any) -> str:
    """Normalize one verdict: allow/ask stay, anything else (incl. missing) is deny."""
    v = str(value or "").strip().lower()
    return v if v in ("allow", "ask") else "deny"


def norm_verdict_map(value: Any) -> Dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k).strip(): verdict(v) for k, v in value.items() if str(k).strip()}


def strip_dot(p: str) -> str:
    p = str(p or "").strip()
    while p.startswith("./"):
        p = p[2:]
    return p


def norm_resources(value: Any) -> Dict[str, Any]:
    from typing import List
    files = value.get("files") if isinstance(value, dict) else None
    files = files if isinstance(files, dict) else {}

    def _globs(k: str) -> List[str]:
        v = files.get(k)
        if not isinstance(v, (list, tuple)):
            return []
        return [strip_dot(x) for x in (str(i).strip() for i in v) if strip_dot(x)]

    return {"files": {"read": _globs("read"), "write": _globs("write"), "deny": _globs("deny")}}


def norm_worker_entry(value: Any) -> Dict[str, Any]:
    from .limits import norm_limits
    e = value if isinstance(value, dict) else {}
    return {"enabled": bool(e.get("enabled", True)),
            "tools": norm_verdict_map(e.get("tools")),
            "capabilities": norm_verdict_map(e.get("capabilities")),
            "resources": norm_resources(e.get("resources")),
            "limits": norm_limits(e.get("limits"))}


def deny_all_permissions() -> Dict[str, Any]:
    """Fail-closed builtin: no workers, no roles, every default denies."""
    return {"defaults": {"unknown_worker": "deny", "unknown_tool": "deny",
                         "missing_permission": "deny", "error": "deny"},
            "workers": {}, "roles": {}}


def load_permissions(permissions: Any = None) -> Dict[str, Any]:
    """Load the worker<->harness boundary (path or dict). Fail-closed.

    None = NAK_PERMISSIONS env > permissions.json next to this file.
    Missing/unreadable/bad content = deny-all (see deny_all_permissions).
    Keeps BOTH `workers` (per-name) and `roles` (per-identity.role) blocks,
    each normalized via norm_worker_entry. Never raises.
    """
    raw: Any = None
    if isinstance(permissions, dict):
        raw = permissions
    else:
        cand = str(permissions or "").strip() or os.environ.get("NAK_PERMISSIONS", "").strip()
        path = Path(cand).expanduser() if cand else DEFAULT_PERMISSIONS_FILE
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return deny_all_permissions()
    if not isinstance(raw, dict):
        return deny_all_permissions()
    workers = raw.get("workers") if isinstance(raw.get("workers"), dict) else {}
    roles = raw.get("roles") if isinstance(raw.get("roles"), dict) else {}
    return {"defaults": {"unknown_worker": "deny", "unknown_tool": "deny",
                         "missing_permission": "deny", "error": "deny"},
            "workers": {str(k).strip(): norm_worker_entry(v)
                        for k, v in workers.items() if str(k).strip()},
            "roles": {str(k).strip(): norm_worker_entry(v)
                      for k, v in roles.items() if str(k).strip()}}


def resolve_workers_file(workers_file: Any = None) -> Path:
    """workers.json path: explicit arg > NAK_WORKERS_FILE env > built-in default."""
    cand = str(workers_file or "").strip() or os.environ.get("NAK_WORKERS_FILE", "").strip()
    return Path(cand).expanduser() if cand else DEFAULT_WORKERS_FILE


def load_worker_names(workers_file: Any = None) -> list:
    """Worker names in workers.json (raw read only). Empty list when unreadable. Never raises."""
    path = resolve_workers_file(workers_file)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    return sorted({str(e.get("name", "")).strip() for e in raw
                   if isinstance(e, dict) and str(e.get("name", "")).strip()})


def assert_worker_known(workers_file: Any = None, worker_name: Optional[str] = None) -> str:
    """Fail-loud check: worker name must exist in workers.json.

    Unbound (empty name) passes (returns ""). Unknown/unreadable ->
    ValueError listing known names + the file used. Used by
    ExecutionHarness.__init__/bind so a typo fails at start, not at
    first tool call (inspect stays fail-closed deny for per-call names).
    """
    name = str(worker_name or "").strip()
    if not name:
        return ""
    path = resolve_workers_file(workers_file)
    names = load_worker_names(path)
    if name not in names:
        hint = f" Have: {names}" if names else f" (workers file unreadable/empty: {path})"
        raise ValueError(f"unknown worker {name!r}.{hint}")
    return name


def load_worker_role(workers_file: Any = None, worker_name: Optional[str] = None) -> Optional[str]:
    """Map a worker name -> identity.role via workers.json (raw read only).

    None = NAK_WORKERS_FILE env > nak_host/manifests/workers.json. Returns the
    role string, or None when unbound/unknown/unreadable. Never raises.
    """
    name = str(worker_name or "").strip()
    if not name:
        return None
    path = resolve_workers_file(workers_file)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, list):
        return None
    for e in raw:
        if isinstance(e, dict) and str(e.get("name", "")).strip() == name:
            ident = e.get("identity")
            role = (ident.get("role") if isinstance(ident, dict) else None)
            role = str(role or "").strip()
            return role or None
    return None


def merge_entries(base: Optional[Dict[str, Any]],
                  overlay: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Merge a role base entry with a worker overlay entry (worker wins).

    * enabled: AND (either side disabled -> disabled).
    * tools/capabilities: per-key, overlay value wins when present.
    * resources read/write/deny: union (deduped, sorted).
    * limits: per-key, overlay wins.
    Either side None -> the other side (normalized) or None.
    """
    if base is None:
        return dict(overlay) if isinstance(overlay, dict) else None
    if overlay is None:
        return dict(base)
    tools = dict(base.get("tools") or {})
    tools.update(overlay.get("tools") or {})
    caps = dict(base.get("capabilities") or {})
    caps.update(overlay.get("capabilities") or {})
    bres = ((base.get("resources") or {}).get("files")) or {}
    ores = ((overlay.get("resources") or {}).get("files")) or {}

    def _union(*lists: Any) -> list:
        out: list = []
        for lst in lists:
            for x in (lst or []):
                if x not in out:
                    out.append(x)
        return sorted(out)

    lim = dict(base.get("limits") or {})
    lim.update(overlay.get("limits") or {})
    return {
        "enabled": bool(base.get("enabled", True)) and bool(overlay.get("enabled", True)),
        "description": str(overlay.get("description") or base.get("description") or ""),
        "tools": tools,
        "capabilities": caps,
        "resources": {"files": {
            "read": _union(bres.get("read"), ores.get("read")),
            "write": _union(bres.get("write"), ores.get("write")),
            "deny": _union(bres.get("deny"), ores.get("deny")),
        }},
        "limits": lim,
    }


def deny(tool: str, worker_label: str, reason: str,
         need_approval: bool = False) -> Dict[str, Any]:
    out: Dict[str, Any] = {"ok": False, "error": reason,
                           "tool": tool, "worker": worker_label}
    if need_approval:
        out["need_approval"] = True
    return out


def rule_for(permissions: Dict[str, Any], worker: Optional[str],
             bound_worker: Optional[str] = None,
             role: Optional[str] = None) -> tuple:
    """Effective permission entry for a worker -> (entry|None, label).

    Resolution: role base (permissions.roles[role]) merged with the
    per-worker overlay (permissions.workers[name]) via merge_entries
    (worker wins, enabled ANDs).     Unbound (no worker) falls back to the
    "default" entry (internal / harness use). A NAMED worker with
    neither a workers entry nor a role entry has no entry (None) and
    hits defaults.unknown_worker (deny) — fail-closed.
    """
    name = str(worker if worker is not None else bound_worker or "").strip()
    perms = permissions or {}
    workers = perms.get("workers") or {}
    roles = perms.get("roles") or {}
    if not name:
        entry = workers.get("default")
        return (entry, "default") if isinstance(entry, dict) else (None, "default")
    r = str(role or "").strip() or None
    base = roles.get(r) if r else None
    overlay = workers.get(name)
    if base is None and overlay is None:
        return (None, name)
    if base is None:
        entry = overlay if isinstance(overlay, dict) else None
        return (entry, name) if isinstance(entry, dict) else (None, name)
    merged = merge_entries(base if isinstance(base, dict) else None,
                           overlay if isinstance(overlay, dict) else None)
    return (merged, name) if isinstance(merged, dict) else (None, name)


def registry_coverage(specs: Any) -> tuple:
    """Tool names + action capability names declared by registry specs.

    Returns (sorted tool names, sorted dotted caps). A spec contributes
    its name plus every value in its action_capabilities map (falling
    back to its plain capabilities list when no map exists).
    """
    from .capability import CAP_MAP
    tools: set = set()
    caps: set = set()
    for spec in (specs or []):
        if not isinstance(spec, dict):
            continue
        name = str(spec.get("name", "") or "").strip()
        if name:
            tools.add(name)
        amap = spec.get("action_capabilities")
        if isinstance(amap, dict):
            for values in amap.values():
                if isinstance(values, list):
                    for c in values:
                        c = str(c or "").strip()
                        if c:
                            caps.add(c)
        else:
            for c in (spec.get("capabilities") or []):
                c = str(c or "").strip()
                if c:
                    caps.add(CAP_MAP.get(c, c if "." in c else f"cap.{c}"))
    return sorted(tools), sorted(caps)


def audit_tool_coverage(permissions: Any, specs: Any) -> Dict[str, Any]:
    """Gaps + extras per workers/roles entry vs the tool registry.

    Returns {<label>: {"tools": [...], "capabilities": [...],
    "extra_tools": [...], "extra_capabilities": [...]}} with labels like
    "workers:vizhi" / "roles:project_manager", only for entries that have
    gaps or extras ({} when fully covered with nothing extra). Read-only:
    never mutates the input. Missing entries stay fail-closed deny at
    runtime. Extras (perm keys with no registry tool/cap) are invisible
    to enforcement but reported here so ghosts get pruned.
    """
    tool_names, action_caps = registry_coverage(specs)
    known_tools = set(tool_names)
    known_caps = set(action_caps)
    gaps: Dict[str, Any] = {}
    for section in ("workers", "roles"):
        entries = (permissions or {}).get(section) or {}
        if not isinstance(entries, dict):
            continue
        for entry_name, entry in entries.items():
            if not isinstance(entry, dict):
                continue
            have_tools = entry.get("tools") or {}
            have_caps = entry.get("capabilities") or {}
            miss_tools = [t for t in tool_names if t not in have_tools]
            miss_caps = [c for c in action_caps if c not in have_caps]
            extra_tools = sorted(t for t in have_tools if t not in known_tools)
            extra_caps = sorted(c for c in have_caps if c not in known_caps)
            if miss_tools or miss_caps or extra_tools or extra_caps:
                gaps[f"{section}:{entry_name}"] = {"tools": miss_tools,
                                                  "capabilities": miss_caps,
                                                  "extra_tools": extra_tools,
                                                  "extra_capabilities": extra_caps}
    return gaps



