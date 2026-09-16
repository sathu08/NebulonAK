"""nak.agents.Polaris -- async router via NebulonMind (Mind-only).

Includes the planning gate (.planning) alongside routing (.agent).
Formerly named DecisionAgent; DecisionAgent is kept as an alias.
"""
from .models import DecisionResult, Action, VALID_ACTIONS
from .agent import Polaris
from .planning import (
    PLANNING_MODES,
    planning_mode,
    needs_planning,
    parse_plan_steps,
    make_plan,
)

# Backward-compat alias (old import path keeps working).
DecisionAgent = Polaris

__all__ = ["Polaris", "DecisionAgent", "DecisionResult", "Action", "VALID_ACTIONS",
           "PLANNING_MODES", "planning_mode", "needs_planning",
           "parse_plan_steps", "make_plan"]
