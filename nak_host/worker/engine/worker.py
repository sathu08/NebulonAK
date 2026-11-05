"""GenericWorker — the ONE worker engine, composed from mixins.

Worker is INDIVIDUAL: no bundled tools, no harness, no ReAct loop.
workers.json entry -> GenericWorker(name, brain=...) -> prompt -> brain.chat()

BOUNDARY (strict): this module NEVER imports nak_host.brain / nak_host.decision /
nak_host.harness. Brain (LLM), decider (laya triage) and policy evaluator arrive
INJECTED via the constructor (duck-typed). Harness is NEVER injected: it is
owned by nak_host/wiring.py (the composition root), which passes only a session id
and a plain approver callable into chat(). The ONLY place that imports all
sides together is nak_host/wiring.py.

Rules:
  * Every LLM call goes via the INJECTED brain -> NebulonMD. No local LLM.
  * identity block: ONLY the IDENTITY_FIELDS (no name: top-level entry
    name is truth; no memory flag: top-level memory.enabled is truth).
  * tools are PLACEHOLDERS: accepted via constructor or JSON passthrough and
    stored, never executed here. Harness objects never reach a worker.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from nak_host.utils.constant import DEFAULT_WORKERS_JSON as _DEFAULT_WORKERS

from .chat import ChatMixin
from .memory import MemoryMixin
from .triage import TriageMixin
from .autonomy import AutonomyMixin
from .identity import IdentityMixin

from .registry import list_workers
from .autonomy import _autonomy_from_crafts
from .prompt import _crafts_from_entry, _load_prompt_template


class GenericWorker(AutonomyMixin, TriageMixin, MemoryMixin, ChatMixin,
                    IdentityMixin):
    """One class that BECOMES any JSON entry. Standalone; tools are optional.

    Placeholders (never executed here, only stored/forwarded):
      tools: optional dict passed by caller (e.g. {"shell": fn})
    A JSON entry's "tools"/"harness" blocks are exposed via show() as context
    only — execution belongs to nak_host/harness, not this worker. The harness
    object itself is never accepted or stored here; nak_host/wiring.py owns it.
    """

    def __init__(self, name: str, brain: Optional[Any] = None,
                 workers_file: str | Path | None = None,
                 tools: Optional[Dict[str, Any]] = None,
                 decider: Optional[Callable[..., Dict[str, Any]]] = None,
                 policy_evaluator: Optional[Callable[..., Dict[str, Any]]] = None,
                 crafts: Optional[List[Dict[str, str]]] = None) -> None:
        """BOUNDARY: brain/decider/evaluator are INJECTED (duck-typed).

        Use nak_host/wiring.py make_worker() to get a fully wired instance.
        brain: object with .chat/.search/.decide/.save_chat (+optional
          .require_mind). None = worker loads but chat()/decide() raise a
          clear "no brain injected" error instead of importing nak_host.brain.
        decider: callable (goal, *, observation, workers, tools, llm=True,
          engine, workers_file) -> decide_task dict. None = triage() reports
          "no decider injected" instead of importing nak_host.decision.
        crafts: [{name, description}] craft set. None = resolved from the
          entry's own "crafts" list in workers.json (user sets it there
          ONLY — make_worker loads descriptions from crafts.json/craft.md).
          Stored + rendered into the prompt only — never executed here.
        policy_evaluator: callable (triage, policy) -> {action,...}.
          None = minimal local gate (clarify on needs_clarification,
          escalate on max_risk) without importing nak_host.decision.policy.
        """
        resolved = workers_file or _DEFAULT_WORKERS
        entries = {e["name"]: e for e in list_workers(resolved)}
        if name not in entries:
            raise ValueError(f"unknown worker {name!r}. Have: {sorted(entries)}")
        self._workers_file = str(resolved)
        self.entry = entries[name]
        self.identity: Dict[str, Any] = self.entry["identity"]
        # Behavior comes from crafts only (no autonomy block in JSON).
        # entry["autonomy"] is the derived dict stamped by list_workers.
        self.autonomy: Dict[str, Any] = dict(
            self.entry.get("autonomy") or _autonomy_from_crafts(
                self.entry.get("crafts"), resolved, name))
        self.brain = brain  # injected; NEVER `or Brain()` here (boundary rule)
        self._decider = decider
        self._policy_evaluator = policy_evaluator
        # Placeholder: stored, never imported, never executed here.
        # The harness is intentionally NOT stored (wiring owns it).
        self.tools: Dict[str, Any] = dict(tools) if tools else {}
        if crafts is None:
            crafts = _crafts_from_entry(self.entry, resolved)
        self.crafts: List[Dict[str, str]] = [
            {"name": str(s.get("name", "")).strip(),
             "description": str(s.get("description", "")).strip()}
            for s in (crafts or []) if isinstance(s, dict)
            and str(s.get("name", "")).strip()]
        self.history: List[Dict[str, str]] = []
        # Markdown prompt view mapped from workers.json ("prompt_file").
        # Read once; missing/unreadable -> built-in builder in prompt().
        self._prompt_template = _load_prompt_template(self.entry, resolved)
