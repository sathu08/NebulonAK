"""nak.harness.project_memory -- R15 persistent project memory.

Turn-end one-liners to Mind (request + files + outcome, namespaced per
harness session) + bounded recall into follow-up context.

Design (from new_plan.md R15):
    - store: after each agent-executed turn, best-effort
      brain.remember("[project <key>] ...", category="harness_project").
      Never fails the turn (Mind down -> skip silently).
    - recall: on follow-up turns, best-effort brain.search() biased with the
      session key, filtered to hits that mention the key (no cross-session
      leaks), truncated to a bounded block. Empty -> "" (single-turn prompts
      stay byte-identical).
    - kill-switch: [harness] project_memory (env NAK_PROJECT_MEMORY wins).
      Disabled -> store and recall are both no-ops.

Namespace: session_id or "default", sanitised to [A-Za-z0-9_-] (max 64).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("nak.harness.project_memory")

CATEGORY = "harness_project"
MEMORY_TYPE = "episodic"
MAX_RECALL_CHARS = 1200
MAX_RECALL_HITS = 3


def session_key(session_id: Optional[str]) -> str:
    """Namespace for one harness session (safe for Mind text + queries)."""
    raw = (session_id or "default").strip() or "default"
    key = re.sub(r"[^A-Za-z0-9_-]", "_", raw)[:64]
    return key or "default"


def is_enabled() -> bool:
    """Kill-switch: env NAK_PROJECT_MEMORY > cfg [harness] project_memory > True."""
    from nak.utils.config import get_project_memory

    try:
        return bool(get_project_memory())
    except Exception:  # noqa: BLE001
        return True


def build_memory_text(
    request: str,
    *,
    session_id: Optional[str] = None,
    files: Optional[List[str]] = None,
    agent_name: str = "",
    status: str = "",
    answer: str = "",
) -> str:
    """One-line turn summary, namespaced so recall can filter per session."""
    key = session_key(session_id)
    req = " ".join(str(request or "").split())[:150]
    out = " ".join(str(answer or "").split())[:200]
    parts = [f"[project {key}] {req}"]
    if files:
        shown = ", ".join(files[:8])
        parts.append(f"files: {shown}"[:200])
    tail = f"{agent_name}({status}): {out}" if agent_name else out
    if tail.strip():
        parts.append(tail.strip()[:260])
    return " | ".join(p for p in parts if p)[:600]


def store_turn(brain: Any, state: Any, agent_name: str, answer: str) -> bool:
    """Best-effort persist of one turn. Returns True if stored."""
    if not is_enabled():
        return False
    try:
        text = build_memory_text(
            getattr(state, "request", ""),
            session_id=getattr(state, "session_id", None),
            files=_state_files(state),
            agent_name=agent_name,
            status=getattr(state, "status", ""),
            answer=answer,
        )
        if not text.strip():
            return False
        brain.remember(text, category=CATEGORY, memory_type=MEMORY_TYPE)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("project memory store skipped: %s", exc)
        return False


def recall_for_turn(brain: Any, state: Any, request: str) -> str:
    """Best-effort bounded recall block for follow-up context ("" = none)."""
    if not is_enabled():
        return ""
    try:
        key = session_key(getattr(state, "session_id", None))
        query = f"project {key} {(' '.join(str(request or '').split())[:150])}"
        hits = brain.search(query, top_k=MAX_RECALL_HITS) or []
        ours = [h for h in hits if key in _hit_text(h)]
        if not ours:
            return ""
        lines = []
        for h in ours[:MAX_RECALL_HITS]:
            txt = " ".join(_hit_text(h).split())[:400]
            if txt:
                lines.append(f"- {txt}")
        if not lines:
            return ""
        block = "Project memory:\n" + "\n".join(lines)
        return block[:MAX_RECALL_CHARS]
    except Exception as exc:  # noqa: BLE001
        logger.debug("project memory recall skipped: %s", exc)
        return ""


def _state_files(state: Any) -> List[str]:
    ws = getattr(state, "workspace", None)
    if not ws:
        return []
    try:
        from nak.plugins.tool.file_tool import list_files

        data = list_files(".", root=ws, max_results=12) or {}
        return [e for e in data.get("entries", []) if "__pycache__" not in e][:12]
    except Exception:  # noqa: BLE001
        return []


def _hit_text(hit: Any) -> str:
    if isinstance(hit, str):
        return hit
    if not isinstance(hit, dict):
        return str(hit)[:500]
    for k in ("text", "content", "summary"):
        v = hit.get(k)
        if isinstance(v, str) and v.strip():
            return v
        if isinstance(v, dict):
            t = v.get("text") or v.get("content") or ""
            if isinstance(t, str) and t.strip():
                return t
    mem = hit.get("memory")
    if isinstance(mem, dict):
        return _hit_text(mem)
    try:
        import json as _json

        return _json.dumps(hit, ensure_ascii=False, default=str)[:500]
    except Exception:  # noqa: BLE001
        return str(hit)[:500]


__all__ = [
    "CATEGORY",
    "MEMORY_TYPE",
    "session_key",
    "is_enabled",
    "build_memory_text",
    "store_turn",
    "recall_for_turn",
]
