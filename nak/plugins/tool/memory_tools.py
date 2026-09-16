"""
nak.plugins.tool.memory_tools -- LLM tool schemas that proxy to NebulonMind.

The Brain itself already implements recall/remember/decide in Python.
This module exposes them as OpenAI-compatible function tool specs so
your own LLM agent loop can call the brain as tools.

Example with OpenAI SDK:

    from nak.brain import Brain
    from nak.plugins.tool import NEBULONAK_TOOLS, execute_tool
    # or: from nak.plugins import NEBULONAK_TOOLS, execute_tool
    import openai

    brain = Brain()
    client = openai.OpenAI(base_url="...", api_key="...")

    messages = [{"role": "user", "content": "Remember I work on NebulonAK"}]
    resp = client.chat.completions.create(
        model="glm-4.5-flash",
        messages=messages,
        tools=NEBULONAK_TOOLS,
    )
    call = resp.choices[0].message.tool_calls[0]
    result = execute_tool(brain, call.function.name, call.function.arguments)
    # feed result back as tool message and continue loop
"""

from __future__ import annotations

import json
from typing import Any, Dict, Callable

NEBULONAK_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": "Search the user's stored memories (semantic recall, ranked). Use when the user asks something that may have been remembered before, or to ground answers with context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language query to recall memories"},
                    "top_k": {"type": "integer", "description": "Number of memories to return (1-10)", "minimum": 1, "maximum": 10},
                    "expand": {"type": "boolean", "description": "Expand via graph (depth-1) before ranking"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "Store a new memory for the user. Use when the user shares a durable fact, preference, skill, or event that should be recalled later.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Content to remember (1-2 sentences, concrete)"},
                    "category": {"type": "string", "description": "Optional category hint (e.g. fact, preference, skill)"},
                    "memory_type": {"type": "string", "enum": ["working", "short_term", "long_term", "episodic", "semantic", "knowledge", "doc"], "description": "Memory type"},
                    "importance": {"type": "number", "minimum": 0, "maximum": 1, "description": "Importance score 0-1"},
                },
                "required": ["text"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "decide",
            "description": "Run the intelligence decision engine on text without storing. Returns what would be remembered vs rejected. Use to preview without persisting.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Conversation text to evaluate"},
                    "include_rejected": {"type": "boolean", "description": "Include rejected decisions in output"},
                },
                "required": ["text"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "build_context",
            "description": "Build a bounded, provenance-carrying LLM context string for a query from stored memories. Use when you need to inject memory context into a downstream prompt.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Query to build context for"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
                    "max_characters": {"type": "integer", "minimum": 100, "maximum": 20000},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "agent_chat",
            "description": "Delegate a full turn to NebulonMind's in-process agent (recall + remember + answer). Use when you want the brain to answer with tool-calling already integrated.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "User utterance to send to the brain agent"},
                    "session_id": {"type": "string", "description": "Optional persistent session id"},
                },
                "required": ["text"],
                "additionalProperties": False,
            },
        },
    },
]

# Aliases for backward compat
NEBULON_TOOLS = NEBULONAK_TOOLS
NEBULON_TOOL_SCHEMAS = NEBULONAK_TOOLS
NEBULONAK_TOOL_SCHEMAS = NEBULONAK_TOOLS


def execute_tool(brain, name: str, arguments: str | Dict[str, Any]) -> str:
    """
    Route a tool call to the Brain. Returns JSON string for tool message.

    Args:
        brain: Brain instance
        name: tool name (recall, remember, decide, build_context, agent_chat)
        arguments: JSON string or dict of arguments
    """
    if isinstance(arguments, str):
        try:
            args = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError:
            return json.dumps({"error": f"invalid JSON arguments: {arguments[:200]}"})
    else:
        args = dict(arguments or {})

    try:
        if name == "recall":
            query = args.get("query", "")
            top_k = args.get("top_k")
            expand = bool(args.get("expand", False))
            results = brain.search(query, top_k=top_k, expand=expand)
            return json.dumps({"query": query, "results": results[:10]}, ensure_ascii=False, default=str)

        elif name == "remember":
            text = args.get("text", "")
            # brain.remember returns {memory: {...}} envelope
            res = brain.remember(
                text,
                category=args.get("category"),
                memory_type=args.get("memory_type"),
                importance=args.get("importance"),
            )
            return json.dumps(res, ensure_ascii=False, default=str)

        elif name == "decide":
            text = args.get("text", "")
            res = brain.decide(text, include_rejected=bool(args.get("include_rejected", False)))
            return json.dumps(res, ensure_ascii=False, default=str)

        elif name == "build_context":
            query = args.get("query", "")
            res = brain.context(
                query,
                top_k=args.get("top_k"),
                max_characters=args.get("max_characters"),
            )
            return json.dumps(res, ensure_ascii=False, default=str)

        elif name == "agent_chat":
            text = args.get("text", "")
            res = brain.chat(text, session_id=args.get("session_id"))
            return json.dumps(res, ensure_ascii=False, default=str)

        else:
            return json.dumps({"error": f"unknown tool {name!r}"})

    except Exception as exc:  # keep LLM loop alive
        return json.dumps({"error": str(exc)[:500]})


def get_tool_map(brain) -> Dict[str, Callable[..., str]]:
    """Return {tool_name: callable(args_json_str) -> result_json_str} dict."""
    return {spec["function"]["name"]: lambda args, n=spec["function"]["name"]: execute_tool(brain, n, args) for spec in NEBULONAK_TOOLS}


__all__ = ["NEBULONAK_TOOLS", "NEBULON_TOOLS", "NEBULONAK_TOOL_SCHEMAS", "NEBULON_TOOL_SCHEMAS", "execute_tool", "get_tool_map"]
