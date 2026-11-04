"""nak.plugins.tool.policy -- approval policies for tool use + agent creation.

Common switch, same for terminal and web — never edited per deploy:
  modes:  nebulonak.cfg [policy] (env NAK_POLICY_* wins), normalized
          by nak.utils.config to ask | allow_once | allow_always | no.
  channel: nebulonak.cfg [approval] channel (env NAK_APPROVAL_VIA wins):
          terminal (input() prompts) | web (browser Approvals queue) | auto.
          Exactly one channel is live per process.

    tool_use:     ask (every call) | allow_once (once per agent run) |
                  allow_always (never ask) | no (tools disabled)
    create_agent: ask (every creation) | allow_once (once per session) |
                  allow_always (auto-create) | no (proposals only)

Terminal prompts accept y / yes / 1. Non-interactive input (EOF) denies
unless allow_always. Nothing here logs or returns secrets (tools take no keys).
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

MODES = ("ask", "allow_once", "allow_always", "no")

_tool_once_ok = False
_create_once_ok = False


# -- web approvals (ask on the website, not the server terminal) ------------
# When the pipeline serves HTTP (test_dir/server.py sets NAK_APPROVAL_VIA=web,
# or stdin is not a TTY), an `ask` approval becomes a pending entry the
# browser polls (GET /api/approvals) and resolves (POST /api/approvals).
# The agent thread waits on an event; timeout denies (safe default).
# Terminal CLI behaviour (input()) is unchanged when stdin is a TTY.

_pending: Dict[str, Dict[str, Any]] = {}
_pending_lock = threading.Lock()


def _approval_via() -> str:
    """Where to ask: 'web' (browser queue) or 'terminal' (input()).

    Common switch, never edited per deploy — order:
      1. env NAK_APPROVAL_VIA (web|terminal) wins for one-off runs
      2. nebulonak.cfg [approval] channel (web|terminal) — the single live line
      3. auto: terminal when stdin is a TTY, else web
    Exactly one channel is live per process; the other path is never taken.
    """
    via = os.environ.get("NAK_APPROVAL_VIA", "").strip().lower()
    if via in ("web", "terminal"):
        return via
    try:
        from nak.utils.config import get_approval_channel

        pinned = get_approval_channel()
        if pinned in ("web", "terminal"):
            return pinned
    except Exception:
        pass
    try:
        return "terminal" if sys.stdin.isatty() else "web"
    except Exception:
        return "terminal"


def _approval_timeout() -> float:
    try:
        return max(10.0, min(float(os.environ.get("NAK_APPROVAL_TIMEOUT", 120.0)), 600.0))
    except (ValueError, TypeError):
        return 120.0


def list_pending_approvals() -> List[Dict[str, Any]]:
    """Pending web approvals for the browser to render (no internals)."""
    now = time.time()
    with _pending_lock:
        return [{"id": rid, "tool": e["tool"], "arguments": e["arguments"],
                 "waited_s": round(now - e["created"], 1)}
                for rid, e in _pending.items()]


def resolve_approval(req_id: str, allow: bool) -> bool:
    """Browser decision for a pending approval. False = unknown id."""
    with _pending_lock:
        entry = _pending.get(req_id or "")
        if entry is None:
            return False
        entry["decision"] = bool(allow)
        try:
            entry["event"].set()
        except Exception:
            pass
        return True


def _await_web_approval(name: str, arguments: Any = None) -> bool:
    """Queue an approval on the website and wait. Timeout denies."""
    try:
        args_preview: Any = json.loads(json.dumps(arguments, ensure_ascii=False, default=str))
    except Exception:
        args_preview = str(arguments)[:500]
    rid = uuid.uuid4().hex[:12]
    event = threading.Event()
    with _pending_lock:
        _pending[rid] = {"tool": name, "arguments": args_preview,
                         "created": time.time(), "event": event, "decision": None}
    try:
        decided = event.wait(timeout=_approval_timeout())
    finally:
        with _pending_lock:
            entry = _pending.pop(rid, None)
    if not decided or entry is None:
        return False
    return bool(entry.get("decision", False))


def _ask_user(name: str, arguments: Any = None) -> bool:
    """Ask once: browser queue when serving web, terminal prompt otherwise."""
    if _approval_via() == "web":
        return _await_web_approval(name, arguments)
    try:
        preview = ""
        if arguments:
            try:
                preview = " " + json.dumps(arguments, ensure_ascii=False)[:200]
            except Exception:
                preview = ""
        return input(f"Allow tool {name!r}{preview}? [y/N or 1/2]: ").strip().lower() in ("y", "yes", "1")
    except (EOFError, KeyboardInterrupt):
        return False


def get_policy() -> Dict[str, str]:
    """Current policy as {tool_use, create_agent}. Defaults ask/ask on any failure."""
    try:
        from nak.utils.config import load_config

        cfg = load_config()
        return {"tool_use": cfg.policy_tool_use, "create_agent": cfg.policy_create_agent}
    except Exception:
        return {"tool_use": "ask", "create_agent": "ask"}


def reset_tool_once() -> None:
    """Start a new approval window (call at the start of each agent run)."""
    global _tool_once_ok
    _tool_once_ok = False


def _prompt(question: str) -> bool:
    try:
        return input(question).strip().lower() in ("y", "yes", "1")
    except (EOFError, KeyboardInterrupt):
        return False


def approve_tool_use(name: str, arguments: Any = None) -> bool:
    """Gate one tool call per policy. Pure wiring over get_policy + prompt."""
    global _tool_once_ok
    mode = get_policy()["tool_use"]
    if mode == "no":
        return False
    if mode == "allow_always":
        return True
    if mode == "allow_once" and _tool_once_ok:
        return True
    ok = _ask_user(name, arguments)
    if ok and mode == "allow_once":
        _tool_once_ok = True
    return ok


def create_once_approved() -> bool:
    return _create_once_ok


def mark_create_approved() -> None:
    global _create_once_ok
    _create_once_ok = True


# -- per-state (harness) approvals ---------------------------------------
# Process-global `_tool_once_ok` breaks multi-session harnesses. The runtime
# keeps `state.permissions["tool_once_ok"]` per task; these helpers read and
# update that scope while falling back to the globals for legacy callers.


def approve_tool_use_for_state(state, name: str, arguments: Any = None) -> bool:
    """Gate one tool call for a HarnessState (per-run allow_once window)."""
    global _tool_once_ok
    mode = get_policy()["tool_use"]
    if mode == "no":
        return False
    if mode == "allow_always":
        return True
    perms = getattr(state, "permissions", None)
    once_ok = bool(perms.get("tool_once_ok")) if isinstance(perms, dict) else False
    if mode == "allow_once" and (once_ok or _tool_once_ok):
        return True
    ok = _ask_user(name, arguments)
    if ok and mode == "allow_once":
        if isinstance(perms, dict):
            perms["tool_once_ok"] = True
        _tool_once_ok = True
    return ok


__all__ = [
    "MODES",
    "get_policy",
    "reset_tool_once",
    "approve_tool_use",
    "approve_tool_use_for_state",
    "create_once_approved",
    "mark_create_approved",
    "list_pending_approvals",
    "resolve_approval",
]
