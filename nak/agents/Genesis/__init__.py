"""nak.agents.Genesis -- confirm-gated agent scaffolding (Mind-only, async)."""
from .agent import Genesis

# Backward-compat alias (old import path keeps working).
AgentCreator = Genesis

__all__ = ["Genesis", "AgentCreator"]
