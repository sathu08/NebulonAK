"""nak.plugins.tool.policy -- approval policies for tool use + agent creation.

Source of truth: nebulonak.cfg [policy] (env NAK_POLICY_* wins), normalized
by nak.utils.config to ask | allow_once | allow_always | no.

    tool_use:     ask (every call) | allow_once (once per agent run) |
                  allow_always (never ask) | no (tools disabled)
    create_agent: ask (every creation) | allow_once (once per session) |
                  allow_always (auto-create) | no (proposals only)

Terminal prompts accept y / yes / 1. Non-interactive input (EOF) denies
unless allow_always. Nothing here logs or returns secrets (tools take no keys).
"""
from __future__ import annotations

import json
from typing import Any, Dict

MODES = ("ask", "allow_once", "allow_always", "no")

_tool_once_ok = False
_create_once_ok = False


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
    preview = ""
    if arguments:
        try:
            preview = " " + json.dumps(arguments, ensure_ascii=False)[:200]
        except Exception:
            preview = ""
    ok = _prompt(f"Allow tool {name!r}{preview}? [y/N or 1/2]: ")
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
    preview = ""
    if arguments:
        try:
            preview = " " + json.dumps(arguments, ensure_ascii=False)[:200]
        except Exception:
            preview = ""
    ok = _prompt(f"Allow tool {name!r}{preview}? [y/N or 1/2]: ")
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
]
