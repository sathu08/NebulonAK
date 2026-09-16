"""
nak.agents.Apollo.agent -- implementation specialist (Phase 6).

Mind-only ReAct loop over the shared helper: reads, writes and edits code in
the task workspace, then verifies with run_test/shell. The harness (not this
agent) owns verification + recovery.
"""
from __future__ import annotations

import asyncio
from typing import Dict, Any, List, Optional

from nak.agents._react import run_react_loop
from nak.brain.client import Brain


class Agent:
    """Apollo — implements features, fixes bugs, edits code (verified with tests)."""

    name = "Apollo"

    def __init__(self, brain: Optional[Brain] = None, system_prompt: Optional[str] = None) -> None:
        self.brain = brain or Brain()
        default_hint = (
            "You are Apollo, an implementation specialist working in an isolated "
            "task workspace. Prefer small verifiable steps: list_files/read_file/search_files "
            "first, then write_file/edit_file, then run_test or shell to verify. Never claim "
            "done without running the relevant checks. Finish with a concise summary of what "
            "changed plus the verification output."
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
