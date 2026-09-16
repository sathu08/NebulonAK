"""nak.agents.Polaris.planning -- conditional pre-execution planning.

All decision-side logic (routing + planning gate) lives here with
Polaris.

TERMINAL LAYER ONLY. Nothing here touches nak/harness: the runtime keeps
owning decide/execute/verify/recover. This module only decides WHETHER a
planning sub-step runs before delegating, runs it, and seeds the result into
the turn state (state.plan) — which the runtime then feeds to the worker
agent as context automatically, and which surfaces in the terminal plan block.

Modes (explicit constructor arg wins, else env NAK_CHAT_PLANNING, else "auto"):
    auto    plan only on complexity signals below (no LLM call to decide this;
            trivial turns stay byte-identical to unplanned ones)
    always  plan every non-empty turn (Kepler must be resolvable,
            otherwise the step is skipped silently)
    never   skip entirely (today's behaviour)

Auto signals (cheap regexes over the raw request — deliberately sensitive:
a wasted plan step costs calls, a missed destructive op costs more):
    - destructive verbs (delete/remove/drop/...) — verifier can't un-delete
    - complexity verbs (build/implement/migrate/refactor/...)
    - multi-step markers ("and then", "step 1", "phase 2", "then", ...)
    - long requests (> 40 words)

R15 project memory is intentionally NOT touched here.
"""

from __future__ import annotations

import logging
import os
import re
from typing import List, Optional

logger = logging.getLogger("nak.agents.Polaris.planning")

PLANNING_MODES = ("auto", "always", "never")

_PLANNER_AGENT = "Kepler"
_MAX_PLAN_STEPS = 8

_COMPLEXITY_WORDS = (
    "build", "implement", "migrate", "refactor", "redesign", "scaffold",
    "integrate", "deploy", "overhaul", "architect",
)
_DESTRUCTIVE_WORDS = (
    "delete", "remove", "drop", "destroy", "wipe", "uninstall", "purge", "erase",
)
_MULTISTEP_PATTERNS = (
    "and then",
    "after that",
    "followed by",
    r"\bstep\s*\d",
    r"\bphase\s*\d",
    r"\bthen\b",
)
_LONG_WORDS = 40

_PLAN_PROMPT = (
    "Create a short step-by-step plan to accomplish the request below. "
    "Do NOT execute anything, output ONLY the plan as a short numbered list "
    f"(max {_MAX_PLAN_STEPS} steps).\nRequest: "
)


def planning_mode(explicit: Optional[str] = None) -> str:
    """Resolve the planning mode. Explicit arg wins, else env, else auto."""
    if explicit is not None and str(explicit).strip().lower() in PLANNING_MODES:
        return str(explicit).strip().lower()
    env = (os.environ.get("NAK_CHAT_PLANNING") or "").strip().lower()
    if env in PLANNING_MODES:
        return env
    return "auto"


def _has_word(text_low: str, word: str) -> bool:
    return re.search(r"\b" + re.escape(word) + r"\b", text_low) is not None


def needs_planning(text: str, mode: str) -> bool:
    """True if a planning sub-step should run. Pure, never raises."""
    try:
        mode = (mode or "auto").strip().lower()
        if mode not in PLANNING_MODES:
            mode = "auto"
        if not text or not text.strip():
            return False
        if mode == "never":
            return False
        if mode == "always":
            return True
        low = text.strip().lower()
        if any(_has_word(low, w) for w in _DESTRUCTIVE_WORDS):
            return True
        if any(_has_word(low, w) for w in _COMPLEXITY_WORDS):
            return True
        if any(re.search(p, low) for p in _MULTISTEP_PATTERNS):
            return True
        if len(text.split()) > _LONG_WORDS:
            return True
        return False
    except Exception:  # noqa: BLE001
        return False


def parse_plan_steps(answer: str) -> List[str]:
    """Numbered/bulleted plan text -> clean step list (capped, never raises)."""
    try:
        steps: List[str] = []
        for line in (answer or "").splitlines():
            cleaned = re.sub(r"^[\s>•\-\*\+]*(\d+[.)]\s*)?", "", line).strip()
            if len(cleaned) > 1:
                steps.append(cleaned)
            if len(steps) >= _MAX_PLAN_STEPS:
                break
        return steps
    except Exception:  # noqa: BLE001
        return []


async def make_plan(runtime, text: str, state) -> List[str]:
    """Run the planner agent scoped to the turn workspace. Never raises.

    Returns step list (possibly empty). Empty means: planner unresolvable,
    planner errored, or no parseable steps — the turn then proceeds unplanned.
    """
    try:
        if not runtime.agents.is_resolvable(_PLANNER_AGENT):
            logger.debug("planner %r not installed, skipping plan step", _PLANNER_AGENT)
            return []
        res = await runtime.agents.run_text(
            _PLANNER_AGENT,
            _PLAN_PROMPT + text.strip(),
            session_id=getattr(state, "session_id", None) or runtime.session_id,
            state=state,
        )
        return parse_plan_steps(str(res.get("answer") or ""))
    except Exception as exc:  # noqa: BLE001
        logger.debug("plan step failed, proceeding unplanned: %s", exc)
        return []


__all__ = [
    "PLANNING_MODES",
    "planning_mode",
    "needs_planning",
    "parse_plan_steps",
    "make_plan",
]
