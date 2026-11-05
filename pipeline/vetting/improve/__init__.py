"""Improvement loop: turn vetting findings into staged craft candidates.

The loop is deliberately conservative:

1. collect improvements from a vetting run;
2. stage a candidate craft tree under ``<workspace_root>/improve/<run>/``;
3. exercise the candidate with the ``core`` team;
4. re-vet affected workers with Nunnan in a fresh session;
5. record the result in ``<workspace_root>/improve/history.json``.

Pushing a candidate into ``nak_host/harness/craft`` is never automatic — every
record keeps ``push_status: "pending"`` for a human to review.
"""

from .apply import apply_improvements, discard_candidate
from .plan import collect_improvements, plan_improvements

__all__ = [
    "apply_improvements",
    "collect_improvements",
    "discard_candidate",
    "plan_improvements",
]
