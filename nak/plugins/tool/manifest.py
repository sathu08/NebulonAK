"""nak.plugins.tool.manifest -- tools.json source agent + harness read.

`tools.json` is GENERATED (never hand-edited). Source of truth is the
`*_tools.py` modules in this folder:

    memory_tools.NEBULONAK_TOOLS + file_tool.FILE_TOOLS/FS_TOOLS
    + exec_tools.EXEC_TOOLS + agent_tools.AGENT_TOOLS/PLAN_TOOLS
    + any custom <name>_tools.py exposing *_TOOLS lists (auto-loaded)

Each entry: {name, group, module, scope, policy, spec}.
scope: file -> _root=workspace | exec -> _cwd=workspace | none.
policy: default ask (mirrors nak.plugins.tool.policy).

Usage:
    from nak.plugins.tool.manifest import load_manifest, refresh_manifest
    specs = [t["spec"] for t in load_manifest()["tools"]]  # LLM tools=
    refresh_manifest()  # after create-tool / upload-tool
"""

from __future__ import annotations

import hashlib
import importlib
import json
import pkgutil
from pathlib import Path
from typing import Any, Dict, List

MANIFEST_NAME = "tools.json"

# group -> (module, scope)
_CORE_GROUPS = {
    "NEBULONAK_TOOLS": ("memory_tools", "none"),
    "FILE_TOOLS": ("file_tool", "file"),
    "FS_TOOLS": ("file_tool", "file"),
    "EXEC_TOOLS": ("exec_tools", "exec"),
    "WEB_TOOLS": ("web_tool", "file"),
    "AGENT_TOOLS": ("agent_tools", "none"),
    "PLAN_TOOLS": ("agent_tools", "none"),
}

_SKIP_MODULES = {"manifest", "policy", "workspace", "terminal_tool"}


def _manifest_path() -> Path:
    return Path(__file__).resolve().parent / MANIFEST_NAME


def _spec_name(spec: Dict[str, Any]) -> str:
    try:
        return str(spec.get("function", {}).get("name", "?"))
    except Exception:
        return "?"


def _iter_custom_tool_lists() -> List[Dict[str, Any]]:
    """Import every <name>_tools.py (except core/skip) and collect *_TOOLS lists.

    Convention for a new tool file (auto-loaded, no __init__ edit):
        MY_TOOLS = [{type:function,...}, ...]
        def execute_my_tool(name, arguments, ...): ...
    Optional module-level TOOL_SCOPES = {"my_fn": "file"} (default file).
    """
    out: List[Dict[str, Any]] = []
    pkg_name = __name__.rsplit(".", 1)[0]
    try:
        pkg = importlib.import_module(pkg_name)
    except ModuleNotFoundError:
        return out
    for mod in pkgutil.iter_modules(getattr(pkg, "__path__", [])):
        mname = mod.name
        if not mname.endswith("_tools") or mname in _SKIP_MODULES:
            continue
        # skip core modules already covered (they also end with _tools)
        if mname in ("memory_tools", "file_tool", "exec_tools", "agent_tools", "web_tool"):
            continue
        full = f"{pkg_name}.{mname}"
        try:
            import sys
            module = sys.modules.get(full) or importlib.import_module(full)
        except Exception:
            continue
        scopes = getattr(module, "TOOL_SCOPES", {}) or {}
        for attr in dir(module):
            if not attr.endswith("_TOOLS") or attr.startswith("_"):
                continue
            try:
                specs = getattr(module, attr)
            except Exception:
                continue
            if not isinstance(specs, list):
                continue
            for spec in specs:
                tname = _spec_name(spec)
                if not tname or tname == "?":
                    continue
                scope = str(scopes.get(tname, "file"))
                if scope not in ("file", "exec", "none"):
                    scope = "file"
                out.append({"name": tname, "group": attr, "module": mname,
                            "scope": scope, "policy": "ask", "spec": spec})
    return out


def collect_entries() -> List[Dict[str, Any]]:
    """Live discovery: core groups + custom files. Dedupes (custom wins)."""
    from . import agent_tools, exec_tools, file_tool, memory_tools, web_tool

    entries: List[Dict[str, Any]] = []
    core_map = {
        "NEBULONAK_TOOLS": getattr(memory_tools, "NEBULONAK_TOOLS", []),
        "FILE_TOOLS": getattr(file_tool, "FILE_TOOLS", []),
        "FS_TOOLS": getattr(file_tool, "FS_TOOLS", []),
        "EXEC_TOOLS": getattr(exec_tools, "EXEC_TOOLS", []),
        "WEB_TOOLS": getattr(web_tool, "WEB_TOOLS", []),
        "AGENT_TOOLS": getattr(agent_tools, "AGENT_TOOLS", []),
        "PLAN_TOOLS": getattr(agent_tools, "PLAN_TOOLS", []),
    }
    for group, specs in core_map.items():
        module, scope = _CORE_GROUPS.get(group, ("?", "none"))
        for spec in specs or []:
            entries.append({"name": _spec_name(spec), "group": group,
                            "module": module, "scope": scope,
                            "policy": "ask", "spec": spec})
    entries.extend(_iter_custom_tool_lists())
    # dedupe by name (last wins = custom overrides core on collision)
    seen: Dict[str, Dict[str, Any]] = {}
    for e in entries:
        seen[e["name"]] = e
    return [seen[k] for k in sorted(seen)]


def refresh_manifest() -> Dict[str, Any]:
    """Rebuild tools.json from live discovery. Returns {path, count, names}."""
    entries = collect_entries()
    blob = {"version": 1, "count": len(entries), "tools": entries}
    # content hash so loaders can detect drift without parsing twice
    digest = hashlib.sha256(json.dumps(
        [e["name"] for e in entries], sort_keys=True).encode()).hexdigest()[:12]
    blob["hash"] = digest
    path = _manifest_path()
    path.write_text(json.dumps(blob, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"path": str(path), "count": len(entries),
            "names": sorted(e["name"] for e in entries), "hash": digest}


def load_manifest(*, auto_refresh: bool = True) -> Dict[str, Any]:
    """Read tools.json; auto-refresh when discovery drifted (new tool file).

    Never raises on missing/corrupt JSON — falls back to live discovery.
    """
    path = _manifest_path()
    live = collect_entries()
    live_names = sorted(e["name"] for e in live)
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(blob, dict) or not isinstance(blob.get("tools"), list):
            raise ValueError("bad manifest shape")
        disk_names = sorted(
            str(t.get("name", "?")) for t in blob["tools"] if isinstance(t, dict))
        if auto_refresh and disk_names != live_names:
            info = refresh_manifest()
            blob = json.loads(path.read_text(encoding="utf-8"))
            blob["_refreshed"] = info
        return blob
    except (OSError, ValueError):
        info = refresh_manifest()
        blob = json.loads(path.read_text(encoding="utf-8"))
        blob["_refreshed"] = info
        return blob


def manifest_specs() -> List[Dict[str, Any]]:
    """LLM-ready specs: [spec, ...] for every tool in the manifest."""
    return [t["spec"] for t in load_manifest().get("tools", [])
            if isinstance(t, dict) and isinstance(t.get("spec"), dict)]


def validate_manifest() -> Dict[str, Any]:
    """Check tools.json matches live discovery + specs are JSON-serializable."""
    blob = load_manifest(auto_refresh=False)
    live_names = sorted(e["name"] for e in collect_entries())
    disk_names = sorted(
        str(t.get("name", "?")) for t in blob.get("tools", []) if isinstance(t, dict))
    issues: List[str] = []
    if live_names != disk_names:
        issues.append(f"stale manifest (disk={disk_names} live={live_names}) — run refresh_manifest()")
    for t in blob.get("tools", []):
        try:
            json.dumps(t.get("spec"))
        except TypeError:
            issues.append(f"{t.get('name')}: spec not JSON-serializable")
    return {"names": disk_names, "count": len(disk_names),
            "issues": issues, "ok": not issues}


def execute_manifest_tool(name: str, arguments: Any = None, *,
                          root=None, cwd=None) -> str | None:
    """Route a custom (non-core) tool via its manifest entry. None = not ours.

    Convention per custom file `<name>_tools.py`:
        MY_TOOLS = [...]                      # specs (auto-found)
        TOOL_SCOPES = {"my_fn": "file"}       # optional (default file)
        def execute_my_tool(tname, args, *, root=None, cwd=None): ...  # any execute_* prefix
    Never raises — errors become JSON strings.
    """
    import inspect as _inspect

    blob = load_manifest(auto_refresh=False)
    entry = next((t for t in blob.get("tools", [])
                  if isinstance(t, dict) and t.get("name") == name), None)
    if entry is None or not str(entry.get("module", "")).endswith("_tools"):
        return None
    module_name = str(entry["module"])
    if module_name in ("memory_tools", "file_tool", "exec_tools", "agent_tools"):
        return None  # core handled by execute_plugin directly
    try:
        module = importlib.import_module(f"{__name__.rsplit('.', 1)[0]}.{module_name}")
    except Exception as exc:
        return json.dumps({"error": f"custom tool module {module_name!r} failed to import: {exc}"[:300]})
    # candidate executors: execute_<group>_lower, then any execute_*
    group = str(entry.get("group", ""))
    candidates: List[str] = []
    if group:
        candidates.append(f"execute_{group.lower()}")
    candidates.extend([a for a in dir(module)
                       if a.startswith("execute_") and a not in candidates])
    if isinstance(arguments, str):
        try:
            args = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError:
            return json.dumps({"error": f"invalid JSON arguments: {arguments[:200]}"})
    else:
        args = dict(arguments or {})
    last_unknown: str | None = None
    for cname in candidates:
        fn = getattr(module, cname, None)
        if not callable(fn):
            continue
        try:
            sig = _inspect.signature(fn)
            kwargs: Dict[str, Any] = {}
            params = list(sig.parameters.values())
            # shape A: (name, arguments, *, root, cwd)
            if len(params) >= 2 and params[0].name in ("name", "tool", "tool_name"):
                kwargs = {"root": root, "cwd": cwd}
                accepted = set(sig.parameters)
                call: Dict[str, Any] = {}
                if "root" in accepted:
                    call["root"] = root
                if "cwd" in accepted:
                    call["cwd"] = cwd
                if "_root" in accepted:
                    call["_root"] = root
                if "_cwd" in accepted:
                    call["_cwd"] = cwd
                try:
                    res = fn(name, args, **call)
                except TypeError:
                    res = fn(name, args)
                text = str(res)
                if '"unknown' in text and "tool" in text and len(candidates) > 1:
                    last_unknown = text
                    continue
                return text if text.strip().startswith(("{", "[")) else json.dumps({"result": res}, ensure_ascii=False, default=str)
            # shape B: direct kwargs fn(path=..., ...) with optional _root/_cwd
            call2 = dict(args)
            accepted2 = set(sig.parameters)
            if "_root" in accepted2 and "root" not in accepted2:
                call2["_root"] = root
            if "_cwd" in accepted2 and "cwd" not in accepted2:
                call2["_cwd"] = cwd
            if "root" in accepted2 and root is not None and "root" not in call2:
                call2["root"] = root
            if "cwd" in accepted2 and cwd is not None and "cwd" not in call2:
                call2["cwd"] = cwd
            filtered = {k: v for k, v in call2.items()
                        if k in accepted2 or any(p.kind == _inspect.Parameter.VAR_KEYWORD for p in params)}
            res = fn(**filtered)
            return json.dumps(res, ensure_ascii=False, default=str)[:20000]
        except Exception as exc:
            return json.dumps({"error": str(exc)[:500]})
    if last_unknown:
        return last_unknown
    return json.dumps({"error": f"custom tool {name!r} has specs but no execute_* in {module_name}.py"})


__all__ = ["load_manifest", "refresh_manifest", "manifest_specs",
           "validate_manifest", "collect_entries", "execute_manifest_tool"]
