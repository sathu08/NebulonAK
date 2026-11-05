"""nak.decision — task-decision (goal -> worker + tool), not memory-decision.

Mind's POST /intelligence/decide stays memory-only (leave it).
THIS package answers: given goal + observation, which worker + tool next?

  decision = decide_task(goal, observation, workers, tools, llm=True)

Workers list comes from nak/manifests/workers.json
via list_workers().
"""

from .batch import decide_many
from .decider import decide_task, endpoint_live, local_rule
from .policy import DEFAULT_POLICY, evaluate, normalize_policy

__all__ = [
    "decide_many",
    "decide_task",
    "endpoint_live",
    "local_rule",
    "DEFAULT_POLICY",
    "evaluate",
    "normalize_policy",
]
