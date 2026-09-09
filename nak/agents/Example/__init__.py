"""
nak.agents.Example -- minimal harness that runs on NebulonMind via the brain.

See nak/agents/DecisionAgent for routing and nak/agents/AgentCreator for
confirm-gated scaffolding. This Example remains a thin proxy to Brain.chat.
"""

from .base import SimpleAgent  # noqa: F401

__all__ = ["SimpleAgent"]
