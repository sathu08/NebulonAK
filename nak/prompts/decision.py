"""Compatibility shim — loads nak/prompts/decision.md.

Prefer: from nak.prompts import render_prompt; render_prompt("decision", ...)
This file keeps backward compat for `from nak.prompts.decision import DECISION_AGENT_PROMPT`.
"""
from . import load_prompt

try:
    DECISION_AGENT_PROMPT = load_prompt("decision")
except FileNotFoundError:
    DECISION_AGENT_PROMPT = """You are DecisionAgent. Decide USE_AGENT/CREATE_AGENT/ASK_USER for: {user_request} Available: {available_agents}"""
