"""pipeline.chatagent -- Polaris-powered terminal chat pipeline.

Layout:
    pipeline/chatagent/
        __init__.py     (this file, re-exports)
        pipeline.py     (ChatAgentPipeline: decide -> route -> answer)
        __main__.py     (terminal loop: python -m pipeline.chatagent)

Flow per user turn (production: EVERY turn decides first, no silent fallback):
    you> <text>
      1. Polaris.decide(text) via NebulonMind -> DecisionResult
      2. Route:
           USE_AGENT    -> run that agent ONLY if installed; else ask to create it
           CREATE_AGENT -> propose + ask "Can I create 'X'? [y/N]" -> Genesis (confirm-gated)
           ASK_USER     -> ask decision.reason as clarifying question (no task execution)
      3. Print [decision] + [Running on: X] + [answer]

Run:
    python -m pipeline.chatagent
    python -m pipeline.chatagent --user alice --no-decision
"""
from .pipeline import ChatAgentPipeline

__all__ = ["ChatAgentPipeline"]
