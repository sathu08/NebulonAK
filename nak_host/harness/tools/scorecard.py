"""Scorecard tool — weighted scoring from explicit, deterministic rules.

Contract: fn(args: dict) -> JSON string. Never raises.

  in:  {"action": "score",
        "inputs": {"<metric>": number, "nested": {"metric": number}},
        "rules": [{"id", "metric", "weight", "min", "max",
                   "higher_is_better"}]}
  out: '{"ok": true, "score": 0..1, "breakdown": [...]}' or
       '{"ok": false, "error": ...}'

Each rule normalizes its metric into 0..1 between min/max (inverted when
higher_is_better is false), then the score is the weight-average. Dotted
metric paths ("assertion.passed") read nested inputs. No IO, no eval.
"""

from __future__ import annotations

import json

from typing import Any, Dict, List

from nak.utils.constant import SCORECARD_MAX_RULES as MAX_RULES


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _lookup(inputs: Dict[str, Any], metric: str) -> Any:
    node: Any = inputs
    for part in metric.split("."):
        if not isinstance(node, dict) or part not in node:
            raise ValueError(f"missing metric {metric!r}")
        node = node[part]
    return node


def _rule(spec: Any, index: int, inputs: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(spec, dict):
        raise ValueError(f"rule {index}: must be an object")
    rid = str(spec.get("id", "") or f"rule-{index}")
    metric = str(spec.get("metric", "")).strip()
    if not metric:
        raise ValueError(f"rule {rid}: missing metric")
    weight = spec.get("weight", 1)
    low = spec.get("min", 0)
    high = spec.get("max", 1)
    higher = spec.get("higher_is_better", True)
    if not _number(weight) or weight <= 0:
        raise ValueError(f"rule {rid}: weight must be a positive number")
    if not (_number(low) and _number(high)) or high <= low:
        raise ValueError(f"rule {rid}: max must be a number greater than min")
    value = _lookup(inputs, metric)
    if not _number(value):
        raise ValueError(f"rule {rid}: metric {metric!r} must be numeric")
    ratio = max(0.0, min(1.0, (float(value) - float(low)) / (float(high) - float(low))))
    normalized = ratio if higher is not False else 1.0 - ratio
    return {"id": rid, "metric": metric, "value": value,
            "weight": float(weight), "min": low, "max": high,
            "higher_is_better": higher is not False,
            "normalized": round(normalized, 6),
            "weighted": round(normalized * float(weight), 6)}


def scorecard(args: dict) -> str:
    """Score metrics against weighted rules; weights are normalized."""
    data = args or {}
    action = str(data.get("action", "")).strip().lower() or "score"
    if action not in ("score", "run", "calculate"):
        return json.dumps({"ok": False,
                           "error": f"bad action {action!r}: want score|run|calculate"})
    rules = data.get("rules")
    if not isinstance(rules, list) or not rules:
        return json.dumps({"ok": False, "error": "rules must be a non-empty list"})
    if len(rules) > MAX_RULES:
        return json.dumps({"ok": False,
                           "error": f"too many rules: {len(rules)} > {MAX_RULES}"})
    inputs = data.get("inputs", data.get("metrics"))
    if not isinstance(inputs, dict):
        return json.dumps({"ok": False, "error": "inputs must be an object"})
    try:
        breakdown: List[Dict[str, Any]] = [
            _rule(spec, i, inputs) for i, spec in enumerate(rules, 1)
        ]
    except ValueError as exc:
        return json.dumps({"ok": False, "error": str(exc)[:300]})
    total = sum(r["weight"] for r in breakdown)
    score = sum(r["weighted"] for r in breakdown) / total
    return json.dumps({"ok": True, "score": round(score, 6),
                       "total_weight": round(total, 6),
                       "rules": len(breakdown), "breakdown": breakdown})
