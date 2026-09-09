"""
nak.agents.base -- minimal harness that runs on NebulonMind via the brain.

No direct LLM keys/handling here. The LLM loop lives inside NebulonMind
(`POST /agent/chat` already does recall + remember + decide via its own
provider/model from nebulonmind.cfg). The agent just proxies through `Brain`.

This keeps NAK decoupled from provider config — NebulonMind owns the LLM,
NAK owns the agent behavior on top of the brain.

Example:
    from nak.agents.base import SimpleAgent

    agent = SimpleAgent()  # uses Brain() -> nebulonak.cfg -> http://localhost:9696/api/NebulonMind
    reply = agent.run("My name is Ada, remember it.")
    print(reply["answer"])

    # with explicit brain / user
    from nak.brain import Brain
    agent = SimpleAgent(brain=Brain(user="alice"))
    agent.run("What is my name?")
"""

from __future__ import annotations

from typing import Dict, Any, List, Optional

from nak.brain.client import Brain


class SimpleAgent:
    """Thin agent that delegates every turn to NebulonMind's brain."""

    def __init__(
        self,
        brain: Optional[Brain] = None,
        system_prompt: Optional[str] = None,
    ) -> None:
        """
        Args:
            brain: Brain instance (creates one from nebulonak.cfg if None).
                   The brain already knows base_url + user + timeouts.
            system_prompt: optional system prompt hint (passed through if
                           NebulonMind supports it via agent config; otherwise
                           kept locally and prepended to history).
        """
        self.brain = brain or Brain()
        self.system_prompt = system_prompt or ""

    def run(
        self,
        text: str,
        *,
        messages: Optional[List[Dict[str, str]]] = None,
        session_id: Optional[str] = None,
        user: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Run one agent turn via the brain (NebulonMind).

        Args:
            text: user utterance (required)
            messages: optional prior turns [{role, content}] for stateless history
            session_id: optional persistent session from brain.create_session()
            user: override user for this turn

        Returns:
            Brain's agent/chat envelope data: {answer, turns, transcript, trace, ...}
        """
        if not text or not text.strip():
            raise ValueError("text must be non-empty")
        # system_prompt handling: NebulonMind's /agent/chat uses its own
        # nmd_agent_system_prompt from config; we just inject locally as a
        # leading system message if the caller supplied one.
        history = list(messages or [])
        if self.system_prompt and not any(m.get("role") == "system" for m in history):
            history = [{"role": "system", "content": self.system_prompt}] + history
        return self.brain.chat(text, messages=history or None, session_id=session_id, user=user)

    # convenience aliases
    def chat(self, text: str, **kwargs: Any) -> str:
        """Return just the answer string."""
        data = self.run(text, **kwargs)
        return str(data.get("answer") or data.get("content") or "")

    def remember(self, text: str, **kwargs: Any) -> Dict[str, Any]:
        """Store a memory directly via the brain."""
        return self.brain.remember(text, **kwargs)

    def recall(self, query: str, **kwargs: Any) -> List[Dict[str, Any]]:
        """Recall memories via the brain."""
        return self.brain.search(query, **kwargs)
    