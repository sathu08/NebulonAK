"""nak.harness.recovery -- bounded auto-recovery (Phase 5).

Consumes exactly what Phase 4 produces: STATUS_FAILED + structured
verification_results. Loop per turn:

    verify FAILED -> decide_strategy -> fix attempt -> re-verify -> ...

Strategies:
    RETRY     re-run the same agent, same prompt (TIMEOUT / transient errors)
    REPLAN    re-run the same agent WITH failure context (verify failures +
              log tail) so it can fix the issue (FAILURE / PARTIAL_SUCCESS)
    ESCALATE  stop retrying; report to the user (NEEDS_USER denials, unknown
              agents, or budget exhausted)

Budgets (infinite observe->recover loops are the #1 risk):
    max_retries via config (env NAK_MAX_RETRIES, else cfg [harness];
    default 2, clamped 0..5); per-state recovery_attempts counter.
    should_recover() is False when the budget is spent or the failure
    needs a human.

No dedicated RecoveryAgent: the original agent replans with failure context
(Kepler serves as fix-planner when it is the running agent). All
recovery steps are recorded in state like normal tool calls + events.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("nak.harness.recovery")

RETRY = "retry"
REPLAN = "replan"
ESCALATE = "escalate"

_DEFAULT_MAX_RETRIES = 2
_MAX_RETRIES_CAP = 5


def max_retries() -> int:
    """Auto-recovery budget via config (default 2, clamped 0..5)."""
    from nak.utils.config import get_max_retries

    return get_max_retries()


def _last_verification(state) -> Dict[str, Any]:
    results = getattr(state, "verification_results", None) or []
    return dict(results[-1]) if results else {}


def _recent_denied(state, window: int = 5) -> bool:
    calls = getattr(state, "tool_calls", None) or []
    return any(c.get("denied") for c in calls[-window:])


def _mentions_timeout(state) -> bool:
    blob = " ".join([
        str(_last_verification(state).get("failures", "")),
        *list(getattr(state, "errors", None) or [])[-3:],
    ]).lower()
    return "timeout" in blob or "timed out" in blob


def decide_strategy(state) -> Dict[str, str]:
    """Pick RETRY / REPLAN / ESCALATE for the current failure. Never raises."""
    try:
        if state.recovery_attempts >= max_retries():
            return {"strategy": ESCALATE,
                    "reason": f"retry budget spent ({state.recovery_attempts}/{max_retries()})"}
        if _recent_denied(state):
            return {"strategy": ESCALATE,
                    "reason": "tool use denied by policy/user — needs a human decision"}
        if _mentions_timeout(state):
            return {"strategy": RETRY, "reason": "timeout looks transient — retry as-is"}
        failures = _last_verification(state).get("failures", [])
        if failures:
            return {"strategy": REPLAN,
                    "reason": f"verification failed ({', '.join(map(str, failures))}) — fix with context"}
        return {"strategy": REPLAN, "reason": "agent reported failure — retry with context"}
    except Exception as exc:  # noqa: BLE001
        return {"strategy": ESCALATE, "reason": f"strategy error: {exc}"}


def build_fix_prompt(state, original_request: str) -> str:
    """Original request + failure context for the fix attempt."""
    failures = _last_verification(state).get("failures", [])
    log_tail = _read_verify_log_tail(state)
    parts = [f"Original request: {(original_request or '').strip()}"]
    if failures:
        parts.append(f"Previous attempt FAILED verification: {', '.join(map(str, failures))}.")
    errors = list(getattr(state, "errors", None) or [])[-3:]
    if errors:
        parts.append("Errors:\n" + "\n".join(f"- {e[:300]}" for e in errors))
    if log_tail:
        parts.append("Verifier log (tail):\n" + log_tail)
    parts.append("Fix the issue using the workspace tools, then finish with your answer.")
    return "\n\n".join(parts)


def _read_verify_log_tail(state, chars: int = 2000) -> str:
    try:
        log = _last_verification(state).get("log", "")
        if not log:
            return ""
        text = Path(log).read_text(encoding="utf-8", errors="replace")
        return text[-chars:]
    except Exception:  # noqa: BLE001
        return ""


async def recover(state, runtime, original_request: str,
                  session_id: Optional[str] = None) -> Dict[str, Any]:
    """One bounded fix attempt. Returns agent-style {answer, run_on, steps}.

    Never raises — total failure becomes an answer payload so the turn can
    still complete with guidance instead of crashing.
    """
    from .events import log_event

    decision = decide_strategy(state)
    strategy = decision["strategy"]
    log_event(state, "recovery",
              f"strategy={strategy}: {decision['reason']}",
              classification="FAILURE" if strategy == ESCALATE else "PARTIAL_SUCCESS")
    if strategy == ESCALATE:
        return {"answer": decision["reason"], "run_on": "HarnessRecovery",
                "steps": [], "strategy": strategy}

    state.recovery_attempts += 1
    agent_name = getattr(state, "agent", None)
    if not agent_name or not runtime.agents.is_resolvable(agent_name):
        agent_name = "Kepler"  # fix-planner fallback
        if not runtime.agents.is_resolvable(agent_name):
            log_event(state, "recovery", "no runnable fix agent available",
                      classification="FAILURE")
            return {"answer": "No runnable agent available for recovery.",
                    "run_on": "HarnessRecovery", "steps": [], "strategy": ESCALATE}

    prompt = (original_request if strategy == RETRY
              else build_fix_prompt(state, original_request))
    sid = (session_id if session_id is not None
           else getattr(state, "session_id", None) or runtime.session_id)
    try:
        result = await runtime.agents.run_text(
            agent_name, prompt, session_id=sid, state=state,
        )
        result["strategy"] = strategy
        log_event(state, "recovery",
                  f"{strategy} attempt {state.recovery_attempts} via {agent_name}",
                  classification="SUCCESS")
        return result
    except Exception as exc:  # noqa: BLE001
        log_event(state, "recovery", f"fix attempt raised: {exc}",
                  classification="FAILURE")
        return {"answer": f"Recovery attempt failed: {exc}",
                "run_on": agent_name, "steps": [], "strategy": strategy}


__all__ = [
    "recover",
    "decide_strategy",
    "build_fix_prompt",
    "max_retries",
    "RETRY",
    "REPLAN",
    "ESCALATE",
]
