"""Decision policy — user-tunable gates over laya triage scores.

laya returns raw scores (no enforcement):
  decision = {"priority": 0.6, "risk": 0.7, "needs_clarification": False, ...}

Policy turns scores into an action the worker/harness must obey:
  {"action": "chat" | "clarify" | "escalate", "allowed": bool, "reasons": [...]}

User sets it free per worker in workers.json:
  "decision": {"enabled": true, "model": "laya", "questions": [...],
               "policy": {"min_confidence": 0.7, "max_risk": 2.0, ...}}

Defaults live in nak/utils/settings.py (DEFAULT_DECISION_*) so a newly
created worker automatically gets a policy block. Entry policy wins over
Settings defaults (entry > Settings > built-in).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from nak.utils.constant import DEFAULT_POLICY, POLICY_TYPES

# Backward-compat alias (same object as nak.utils.constant.POLICY_TYPES).
_POLICY_TYPES = POLICY_TYPES


def normalize_policy(raw: Any) -> Dict[str, Any]:
    """Merge user dict over DEFAULT_POLICY. Unknown keys pass through."""
    out = dict(DEFAULT_POLICY)
    if isinstance(raw, dict):
        for k, v in raw.items():
            out[k] = v
    # coerce numerics into range
    try:
        out["min_confidence"] = max(0.0, min(1.0, float(out.get("min_confidence", 0.6))))
    except (TypeError, ValueError):
        out["min_confidence"] = 0.6
    for k in ("max_risk", "max_priority_auto"):
        try:
            out[k] = float(out.get(k, DEFAULT_POLICY[k]))
        except (TypeError, ValueError):
            out[k] = DEFAULT_POLICY[k]
    out["enabled"] = bool(out.get("enabled", True))
    return out


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def evaluate(
    triage: Dict[str, Any],
    policy: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Scores -> action. Never raises; empty triage -> escalate-safe default.

    Returns {"action": "chat"|"clarify"|"escalate", "allowed": bool,
             "reasons": [...], "policy": {...}}.
    chat = proceed to Brain/harness. clarify = ask user first.
    escalate = needs human approve (high risk / high priority / low confidence).
    """
    pol = normalize_policy(policy)
    reasons: List[str] = []
    if not pol.get("enabled", True):
        return {"action": "chat", "allowed": True,
                "reasons": ["policy disabled"], "policy": pol}
    if not isinstance(triage, dict) or not triage:
        return {"action": "escalate", "allowed": False,
                "reasons": ["empty triage (laya fallback) — human check"],
                "policy": pol}

    # 1. explicit needs_clarification from laya.
    if pol.get("clarify_on_needs_clarification", True) and triage.get("needs_clarification") is True:
        reasons.append("laya needs_clarification=true")
        return {"action": "clarify", "allowed": False, "reasons": reasons, "policy": pol}

    # 2. low confidence on routing (worker/tool pick untrusted).
    min_c = float(pol.get("min_confidence", 0.6))
    low: List[str] = []
    for k in ("worker", "tool"):
        c = _num(triage.get(f"{k}_confidence"))
        if c is not None and c < min_c:
            low.append(f"{k} confidence {c:.2f} < {min_c:.2f}")
    # also gate triage keys that carry confidence
    for k in pol.get("confidence_keys", []) or []:
        c = _num(triage.get(f"{k}_confidence"))
        if k == "needs_clarification" and c is not None and c < min_c and triage.get(k) is True:
            low.append(f"{k} uncertain ({c:.2f})")
    if low:
        reasons.extend(low)
        return {"action": "clarify", "allowed": False, "reasons": reasons, "policy": pol}

    # 3. high risk / high priority -> escalate.
    risk = _num(triage.get("risk"))
    pri = _num(triage.get("priority"))
    if pol.get("escalate_on_high_risk", True) and risk is not None and risk >= float(pol.get("max_risk", 2.0)):
        reasons.append(f"risk {risk:.2f} >= max {float(pol.get('max_risk', 2.0)):.2f}")
        return {"action": "escalate", "allowed": False, "reasons": reasons, "policy": pol}
    if pri is not None and pri >= float(pol.get("max_priority_auto", 2.5)):
        reasons.append(f"priority {pri:.2f} >= max-auto {float(pol.get('max_priority_auto', 2.5)):.2f}")
        return {"action": "escalate", "allowed": False, "reasons": reasons, "policy": pol}

    reasons.append("within policy")
    return {"action": "chat", "allowed": True, "reasons": reasons, "policy": pol}
