"""
nak.agents.ResearchAgent.agent -- generated skeleton (confirm-gated).

This agent proxies to NebulonMind via Brain, like Example/SimpleAgent.
Fill in domain logic; keep Mind-only (no direct openai client).

Usage:
    from nak.agents.ResearchAgent.agent import Agent
    from nak.brain import Brain
    agent = Agent(brain=Brain())
    reply = await agent.run("hello")  # or agent.run_sync("hello")
"""
from __future__ import annotations

import asyncio
import json
from typing import Dict, Any, List, Optional

from nak.brain.client import Brain
from nak.plugins import PLUGIN_TOOLS, execute_plugin
from nak.utils.config import load_config


def _live_specs():
    try:
        from nak.plugins.tool.manifest import manifest_specs as _ms
        return _ms() or list(PLUGIN_TOOLS)
    except Exception:
        return list(PLUGIN_TOOLS)


def _parse_step_answer(raw):
    """Mind reply -> answer-step or tool-step. Unparseable replies become final answers."""
    answer = ""
    if isinstance(raw, dict):
        answer = str(raw.get("answer") or raw.get("content") or raw.get("text") or "")
    else:
        answer = str(raw)
    text = answer.strip()
    data = None
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        # brace-free fallback for markdown-wrapped JSON (no regex: braces
        # would need escaping in this .format() template)
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            try:
                data = json.loads(text[start:end + 1])
            except (ValueError, TypeError):
                data = None
    if isinstance(data, dict):
        if "answer" in data and "tool" not in data:
            return {"kind": "answer", "text": str(data["answer"]) }
        if "tool" in data:
            args = data.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (ValueError, TypeError):
                    args = {}
            if not isinstance(args, dict):
                args = {}
            return {"kind": "tool", "name": str(data["tool"]), "args": args}
    return {"kind": "answer", "text": answer}


class Agent:
    """ResearchAgent — handles: No existing agent can handle research tasks on Databricks"""

    name = "ResearchAgent"

    def __init__(self, brain: Optional[Brain] = None, system_prompt: Optional[str] = None) -> None:
        self.brain = brain or Brain()
        # NOTE: NebulonMind /agent/chat accepts only roles user/assistant/tool
        # (no "system"). role_hint + custom_instructions travel as plain
        # context prefixed to the user text — never as a message.
        default_hint = "You are ResearchAgent, a helpful assistant for no existing agent can handle research tasks on databricks."
        self.role_hint = system_prompt if system_prompt is not None else default_hint
        self.custom_instructions = ""

    def _mind_step(self, prompt, messages, session_id):
        """One Mind reasoning step (overridable in tests without network)."""
        return self.brain.chat(prompt, messages=messages or None, session_id=session_id)

    def run_sync(self, text: str, messages: Optional[List[Dict[str, str]]] = None, **kwargs: Any) -> Dict[str, Any]:
        if not text or not text.strip():
            raise ValueError("text must be non-empty")
        context = " ".join(
            p.strip() for p in (self.role_hint, self.custom_instructions) if p and p.strip()
        ).strip()
        # NOTE: doubled braces below - this is a format template, so they
        # render as single braces in the generated agent file.
        effective = f"Agent context: {context}\nUser request: {text.strip()}" if context else text.strip()
        # Only forward user/assistant/tool roles — Mind rejects "system".
        history = [m for m in (messages or []) if m.get("role") in ("user", "assistant", "tool")]
        from nak.plugins.tool.policy import reset_tool_once, approve_tool_use
        from nak.utils.config import load_config
        try:
            max_turns = max(1, load_config().policy_max_turns)
        except Exception:
            max_turns = 6
        reset_tool_once()
        session_id = kwargs.get("session_id")
        tool_brief = "; ".join(
            s["function"]["name"] + ": " + s["function"].get("description", "")[:80] for s in _live_specs()
        )
        transcript = [{"role": "user", "content": effective}]
        last_text, turns = "", 0
        for _ in range(max_turns):
            turns += 1
            step_prompt = (
                "Reply with ONLY JSON - either " +
                '{"tool": "<name>", "arguments": {...}}' +
                " to use one tool, or " +
                '{"answer": "..."}' +
                " when done. No other text.\n" +
                "Available tools: " + tool_brief + "\n" +
                "History so far:\n" + json.dumps(transcript[-10:], ensure_ascii=False)[:6000]
            )
            try:
                raw = self._mind_step(step_prompt, (history + transcript)[-12:] or None, session_id)
            except Exception as exc:
                last_text = f"[brain error: {exc}]"
                break
            step = _parse_step_answer(raw)
            if step["kind"] == "answer":
                last_text = step["text"]
                break
            tname, targs = step["name"], step["args"]
            if approve_tool_use(tname, targs):
                try:
                    result = execute_plugin(self.brain, tname, targs)
                except Exception as exc:
                    result = json.dumps({"error": str(exc)[:500]})
            else:
                result = json.dumps({"error": f"tool {tname!r} use denied (policy/user)"})
            transcript.append({"role": "assistant",
                               "content": json.dumps({"tool": tname, "arguments": targs}, ensure_ascii=False)})
            transcript.append({"role": "tool", "content": result})
        return {"answer": last_text or "(no answer after tool loop)", "turns": turns, "agent": self.name}

    async def run(self, text: str, **kwargs: Any) -> Dict[str, Any]:
        return await asyncio.to_thread(self.run_sync, text, **kwargs)

    def chat(self, text: str, **kwargs: Any) -> str:
        data = self.run_sync(text, **kwargs)
        return str(data.get("answer") or data.get("content") or "")

    async def chat_async(self, text: str, **kwargs: Any) -> str:
        data = await self.run(text, **kwargs)
        return str(data.get("answer") or data.get("content") or "")
