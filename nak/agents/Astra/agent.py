"""
nak.agents.Astra.agent -- code-review specialist (Phase 6).

Mind-only ReAct loop over the shared helper. Read-only by convention: inspects
code with read_file/list_files/search_files and reports ranked findings with
file:line references. Never modifies files.
"""
from __future__ import annotations

import asyncio
from typing import Dict, Any, List, Optional

from nak.agents._react import run_react_loop
from nak.brain.client import Brain


class Agent:
    """Astra — read-only code reviewer (ranked findings, file:line)."""

    name = "Astra"

    def __init__(self, brain: Optional[Brain] = None, system_prompt: Optional[str] = None) -> None:
        self.brain = brain or Brain()
        default_hint = (
            "You are Astra, a read-only code reviewer working in an isolated task "
            "workspace. Inspect code with read_file/list_files/search_files only — never "
            "write, edit, or execute files. Report findings ranked by severity "
            "(blocker / major / minor), each with file:line references and a concrete "
            "suggestion. Finish with a one-line verdict: approve, approve-with-comments, "
            "or needs-changes."
        )
        self.role_hint = system_prompt if system_prompt is not None else default_hint
        self.custom_instructions = ""

    def _mind_step(self, prompt, messages, session_id):
        """One Mind reasoning step (overridable in tests without network)."""
        return self.brain.chat(prompt, messages=messages or None, session_id=session_id)

    def run_sync(self, text: str, messages: Optional[List[Dict[str, str]]] = None, **kwargs: Any) -> Dict[str, Any]:
        return run_react_loop(
            brain=self.brain, agent_name=self.name, role_hint=self.role_hint,
            text=text, custom_instructions=self.custom_instructions,
            messages=messages, session_id=kwargs.get("session_id"),
            mind_step=self._mind_step,
        )

    async def run(self, text: str, **kwargs: Any) -> Dict[str, Any]:
        return await asyncio.to_thread(self.run_sync, text, **kwargs)

    def chat(self, text: str, **kwargs: Any) -> str:
        return str(self.run_sync(text, **kwargs).get("answer") or "")

    async def chat_async(self, text: str, **kwargs: Any) -> str:
        return str((await self.run(text, **kwargs)).get("answer") or "")
