"""nak.service.suggest -- pure offline name/capability fallbacks.

Moved out of pipeline/chatagent/__main__.py so both the headless service
and the thin CLI share one copy. No Brain, no I/O, no prompts — pure
functions the drag-drop canvas can also call for node defaults.
"""
from __future__ import annotations

import re
from typing import List, Tuple

from nak.utils.agent_registry import validate_agent_name

_NAME_KEYWORDS = (
    ("plan", "Kepler"), ("excel", "ExcelAgent"), ("pdf", "PdfReaderAgent"),
    ("email", "EmailAgent"), ("research", "ResearchAgent"), ("code", "Apollo"),
    ("api", "Apollo"), ("test", "Pulsar"),
)
_CAP_KEYWORDS = (
    ("plan", ["planning", "roadmap", "design"]),
    ("excel", ["excel", "duplicate-detection"]),
    ("pdf", ["pdf", "extract"]),
    ("research", ["research", "compare"]),
    ("code", ["coding", "review"]), ("api", ["api", "backend"]),
)
_NAME_STOP = {"a", "an", "the", "that", "this", "agent", "create", "make", "new",
              "my", "i", "want", "need", "needs", "type", "of", "for", "to",
              "please", "me", "us", "our", "with", "and"}


def _kw_hit(low: str, kw: str) -> bool:
    """Word-prefix match: 'planning' hits 'plan', but 'explain' does NOT."""
    return re.search(rf"\b{re.escape(kw)}\w*\b", low) is not None


def suggest_agent_name(desc: str) -> str:
    """Suggest a reusable agent name for a free-text description."""
    low = (desc or "").lower()
    for kw, name in _NAME_KEYWORDS:
        if _kw_hit(low, kw):
            return name
    words = [w for w in re.findall(r"[A-Za-z]+", desc) if w.lower() not in _NAME_STOP][:2]
    base = "".join(w[:1].upper() + w[1:] for w in words) or "Custom"
    if not base.endswith("Agent"):
        base += "Agent"
    try:
        return validate_agent_name(base)
    except ValueError:
        return "CustomAgent"


def capability_options(desc: str) -> List[Tuple[str, List[str]]]:
    """3 numbered capability choices. Last is always custom."""
    low = (desc or "").lower()
    for kw, caps in _CAP_KEYWORDS:
        if _kw_hit(low, kw):
            return [(f"{', '.join(caps)} (Recommended)", caps),
                    (caps[0], [caps[0]]),
                    ("custom (type your own)", [])]
    return [("general (Recommended)", ["general"]),
            ("chat, remember, recall", ["chat", "remember", "recall"]),
            ("custom (type your own)", [])]


__all__ = ["suggest_agent_name", "capability_options"]
