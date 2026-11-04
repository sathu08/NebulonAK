"""nak.plugins.tool.agent_tools -- orchestration tools (Phase 6).

    delegate(agent_name, task)   run another agent synchronously as a sub-step;
                                 returns {agent, answer, steps}
    create_plan(steps)           replace the current turn's plan (string list)
    update_plan(action, ...)     append | complete <index> | clear the plan

Plan tools reach the active turn's HarnessState via the ambient harness-state
slot (set by HarnessRuntime.handle); outside a turn they report an error
instead of touching anything. Policy gating happens in ToolExecutor like any
other tool; nested agent tool calls reuse the ambient workspace + the agents'
existing approval flow.

Recursion guard: delegation depth is capped (default 2) so A -> B -> A loops
become a clean error instead of runaway Mind calls.
"""

from __future__ import annotations

import importlib
import json
import os
from contextvars import ContextVar
from typing import Any, Dict, List, Optional

_delegation_depth: ContextVar[int] = ContextVar("nak_delegation_depth", default=0)


def max_delegation_depth() -> int:
    """Nesting cap via config (default 2, clamped 1..5)."""
    from nak.utils.config import get_max_delegation

    return get_max_delegation()


def _resolve_agent_class(agent_name: str):
    """Import nak.agents.<Name>.agent.Agent. Raises FileNotFoundError/AttributeError."""
    name = (agent_name or "").strip()
    if not name:
        raise ValueError("agent_name must be non-empty")
    try:
        mod = importlib.import_module(f"nak.agents.{name}.agent")
    except ModuleNotFoundError:
        raise FileNotFoundError(f"agent {name!r} is not installed")
    cls = getattr(mod, "Agent", None)
    if cls is None:
        raise AttributeError(f"Agent {name} has no Agent class")
    return cls


def delegate(
    brain,
    agent_name: str,
    task: str,
    *,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run another agent on a sub-task. Returns {agent, answer, steps}.

    Inherits the ambient turn workspace when called inside a harness run.
    Standalone callers that let the sub-agent write files should scope with
    workspace.set_current_workspace() first, or writes land in the repo root
    (legacy agent behaviour).
    """
    depth = _delegation_depth.get()
    if depth >= max_delegation_depth():
        raise ValueError(
            f"delegation depth {depth} exceeds cap {max_delegation_depth()} "
            "(refactor the task instead of nesting agents)"
        )
    if not task or not task.strip():
        raise ValueError("task must be non-empty")
    cls = _resolve_agent_class(agent_name)
    inst = cls(brain=brain)
    if not hasattr(inst, "run_sync"):
        raise AttributeError(f"Agent {agent_name} has no run_sync")
    token = _delegation_depth.set(depth + 1)
    try:
        data = inst.run_sync(task.strip(), session_id=session_id)
    finally:
        _delegation_depth.reset(token)
    if not isinstance(data, dict):
        return {"agent": agent_name, "answer": str(data), "steps": []}
    return {
        "agent": agent_name,
        "answer": str(data.get("answer") or data.get("content") or ""),
        "steps": data.get("steps") if isinstance(data.get("steps"), list) else [],
    }


def _active_state():
    from .workspace import current_harness_state

    state = current_harness_state()
    if state is None:
        raise ValueError("no active harness turn (plan tools work inside a run only)")
    return state


def create_plan(steps: List[str]) -> Dict[str, Any]:
    """Replace the current turn's plan with `steps`."""
    state = _active_state()
    clean = [str(s).strip() for s in (steps or []) if str(s).strip()]
    if not clean:
        raise ValueError("steps must be a non-empty list of strings")
    state.plan = clean
    return {"plan": list(state.plan)}


def update_plan(action: str, step: Any = None, index: Optional[int] = None) -> Dict[str, Any]:
    """Mutate the current turn's plan.

    action=append needs step (str); action=complete needs index (int, marks
    "[done] ..."); action=clear empties the plan.
    """
    state = _active_state()
    act = (action or "").strip().lower()
    if act == "append":
        text = str(step or "").strip()
        if not text:
            raise ValueError("append needs step text")
        state.plan.append(text)
    elif act == "complete":
        if index is None:
            raise ValueError("complete needs index")
        i = int(index)
        if not 0 <= i < len(state.plan):
            raise ValueError(f"index {i} out of range (plan has {len(state.plan)} steps)")
        if not state.plan[i].startswith("[done]"):
            state.plan[i] = "[done] " + state.plan[i]
    elif act == "clear":
        state.plan = []
    else:
        raise ValueError(f"invalid action {action!r}: use append|complete|clear")
    return {"plan": list(state.plan)}


AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "delegate",
            "description": "Delegate a sub-task to another agent (e.g. Apollo writes code, Pulsar runs tests). Use to split work across specialists.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_name": {"type": "string", "description": "Exact agent name (e.g. Apollo)"},
                    "task": {"type": "string", "description": "Self-contained sub-task for that agent"},
                },
                "required": ["agent_name", "task"],
                "additionalProperties": False,
            },
        },
    },
]

PLAN_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "create_plan",
            "description": "Replace the current turn's step-by-step plan. Use after deciding how to attack the task so progress is trackable.",
            "parameters": {
                "type": "object",
                "properties": {
                    "steps": {"type": "array", "items": {"type": "string"},
                              "description": "Ordered plan steps"},
                },
                "required": ["steps"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_plan",
            "description": "Update the current turn's plan: append a step, mark index complete, or clear.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["append", "complete", "clear"]},
                    "step": {"type": "string", "description": "Step text (for append)"},
                    "index": {"type": "integer", "description": "Step index (for complete)", "minimum": 0},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        },
    },
]


def execute_agent_tools(brain, name: str, arguments: str | Dict[str, Any]) -> str:
    """Route delegate / create_plan / update_plan. Loop-safe JSON errors."""
    if isinstance(arguments, str):
        try:
            args = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError:
            return json.dumps({"error": f"invalid JSON arguments: {arguments[:200]}"})
    else:
        args = dict(arguments or {})
    try:
        if name == "delegate":
            session_id = args.get("session_id")
            res = delegate(brain, args.get("agent_name", ""), args.get("task", ""),
                           session_id=session_id)
            return json.dumps(res, ensure_ascii=False, default=str)[:20000]
        if name == "create_plan":
            steps = args.get("steps", [])
            if not isinstance(steps, list):
                raise ValueError("steps must be a list of strings")
            return json.dumps(create_plan(steps), ensure_ascii=False, default=str)
        if name == "update_plan":
            return json.dumps(
                update_plan(args.get("action", ""), step=args.get("step"),
                            index=args.get("index")),
                ensure_ascii=False, default=str)
        return json.dumps({"error": f"unknown orchestration tool {name!r}"})
    except Exception as exc:  # keep LLM loop alive
        return json.dumps({"error": str(exc)[:500]})


__all__ = [
    "AGENT_TOOLS",
    "PLAN_TOOLS",
    "delegate",
    "create_plan",
    "update_plan",
    "execute_agent_tools",
    "max_delegation_depth",
]
