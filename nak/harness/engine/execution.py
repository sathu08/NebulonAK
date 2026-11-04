"""Tool Execution — moved out of engine.py.

Lazy tool resolution (importlib only at run time) + run/suggest_and_run.
Takes an inspect callable (the harness boundary check) so this module
never imports permission logic itself.
"""

from __future__ import annotations

import importlib
import json
import time

from typing import Any, Callable, Dict, List, Optional


def resolve_tool(tool: str, overrides: Dict[str, Callable],
                 cache: Dict[str, Callable],
                 specs: Dict[str, Dict[str, Any]]) -> Callable[[Dict[str, Any]], str]:
    if tool in overrides:
        return overrides[tool]
    if tool in cache:
        return cache[tool]
    spec = specs.get(tool)
    if spec is None:
        raise KeyError(tool)
    try:
        module = importlib.import_module(str(spec["module"]))
        fn = getattr(module, str(spec["function"]))
    except (ImportError, AttributeError) as exc:
        raise ImportError(
            f"tool {tool!r} location {spec.get('module')}."
            f"{spec.get('function')} unloadable: {exc}") from exc
    if not callable(fn):
        raise TypeError(f"tool {tool!r} at {spec.get('module')}.{spec.get('function')} is not callable")
    cache[tool] = fn
    return fn


def run_tool(name: str, args: Optional[Dict[str, Any]],
             specs: Dict[str, Dict[str, Any]],
             overrides: Dict[str, Callable],
             cache: Dict[str, Callable],
             session_dir: str,
             inspect_fn: Callable[..., Dict[str, Any]],
             counters: Dict[str, Any],
             allow: Optional[List[str]] = None,
             worker: Optional[str] = None,
             approver: Optional[Callable[..., bool]] = None) -> str:
    """Boundary inspect -> run. Returns a JSON STRING, never raises on tool failure."""
    tool = str(name or "").strip()
    given = dict(args or {})
    plan = inspect_fn(tool, given, allow=allow, worker=worker, approver=approver)
    if not plan.get("ok"):
        out: Dict[str, Any] = {"ok": False, "error": plan.get("error", "inspection failed"),
                               "tool": plan.get("tool", tool)}
        if plan.get("need_approval"):
            out["need_approval"] = True
        if plan.get("worker"):
            out["worker"] = plan["worker"]
        return json.dumps(out)
    spec = specs.get(tool) or {}
    sandboxed = (bool(spec.get("sandbox")) or "path" in given
                 or "path" in (spec.get("args") or {}))
    if sandboxed and not str(given.get("root", "")).strip():
        given = {**given, "root": session_dir}
    try:
        fn = resolve_tool(tool, overrides, cache, specs)
    except (KeyError, ImportError, TypeError, ValueError) as exc:
        msg = (f"unknown tool {tool!r}." if isinstance(exc, KeyError)
               else str(exc)[:300])
        return json.dumps({"ok": False, "error": msg})
    counters["calls"] = int(counters.get("calls", 0)) + 1
    start = time.monotonic()
    try:
        result = fn(given)
    except Exception as exc:  # noqa: BLE001 — tool crash becomes error JSON
        return json.dumps({"ok": False, "error": str(exc)[:300]})
    finally:
        counters["exec_seconds"] = float(counters.get("exec_seconds", 0.0)) + time.monotonic() - start
    return str(result)


def suggest_and_run(suggestion: Dict[str, Any],
                    specs: Dict[str, Dict[str, Any]],
                    overrides: Dict[str, Callable],
                    cache: Dict[str, Callable],
                    session_dir: str,
                    inspect_fn: Callable[..., Dict[str, Any]],
                    counters: Dict[str, Any],
                    allow: Optional[List[str]] = None,
                    worker: Optional[str] = None,
                    approver: Optional[Callable[..., bool]] = None) -> str:
    """Run a Brain-shaped suggestion: {"tool": ..., "arguments": {...}}."""
    if not isinstance(suggestion, dict):
        return json.dumps({"ok": False, "error": "suggestion must be a dict"})
    tool = str(suggestion.get("tool", "")).strip()
    targs = suggestion.get("arguments") or {}
    if not isinstance(targs, dict):
        return json.dumps({"ok": False, "error": "arguments must be an object"})
    return run_tool(tool, targs, specs, overrides, cache, session_dir,
                    inspect_fn, counters, allow=allow, worker=worker, approver=approver)
