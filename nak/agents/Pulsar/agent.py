"""
nak.agents.Pulsar.agent -- testing specialist (Phase 6).

Mind-only ReAct loop over the shared helper: runs pytest/compileall, reports
pass/fail with failing test names, and writes minimal regression tests when
asked. Read-heavy; writes only test files unless instructed otherwise.
"""
from __future__ import annotations

import asyncio
from typing import Dict, Any, List, Optional

from nak.agents._react import run_react_loop
from nak.brain.client import Brain


class Agent:
    """Pulsar — runs test suites, reports results, adds regression tests."""

    name = "Pulsar"

    def __init__(self, brain: Optional[Brain] = None, system_prompt: Optional[str] = None) -> None:
        self.brain = brain or Brain()
        default_hint = (
            "You are Pulsar, a testing specialist working in an isolated task "
            "workspace. Run suites with run_test (or shell for custom commands) and "
            "compileall for syntax. Report pass/fail counts plus failing test names and "
            "their error tails. When asked to add coverage, write minimal regression tests "
            "with write_file. Do not refactor production code unless explicitly asked — "
            "report the defect with file:line instead."
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
